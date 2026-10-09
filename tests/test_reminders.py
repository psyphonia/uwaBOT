import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord

from bot import UwaBot
from database import Database
from mock_data import MockData, build_mock_data
from models import INDIA_TZ
from reminders import ReminderService, applicable_threshold, load_reminder_channel_id, reminder_embed

NOW = datetime(2026, 10, 9, 20, tzinfo=timezone.utc)


class WindowTests(unittest.TestCase):
    def test_thresholds_and_catch_up(self) -> None:
        for hours, expected in ((168, 168), (167.5, 168), (145, 168), (24, 24), (23.5, 24),
                                (19, 24), (3, 3), (2, 3), (0.01, 3)):
            with self.subTest(hours=hours):
                self.assertEqual(applicable_threshold(NOW + timedelta(hours=hours), NOW), expected)

    def test_outside_windows_and_past(self) -> None:
        for hours in (169, 144, 100, 25, 18, 10, 3.01, 0, -1):
            with self.subTest(hours=hours):
                self.assertIsNone(applicable_threshold(NOW + timedelta(hours=hours), NOW))

    def test_timezone_boundary_and_naive_rejection(self) -> None:
        due = (NOW + timedelta(hours=3)).astimezone(INDIA_TZ)
        self.assertNotEqual(due.date(), NOW.date())
        self.assertEqual(applicable_threshold(due, NOW), 3)
        with self.assertRaises(ValueError):
            applicable_threshold(due.replace(tzinfo=None), NOW)
        with self.assertRaises(ValueError):
            applicable_threshold(due, NOW.replace(tzinfo=None))

    def test_channel_configuration(self) -> None:
        for value in (None, "", " ", "invalid", "-1", "0", str(2**64)):
            with patch.dict(os.environ, {} if value is None else {"REMINDER_CHANNEL_ID": value}, clear=True):
                with self.assertLogs("uwa_india_bot.reminders", level="WARNING"):
                    self.assertIsNone(load_reminder_channel_id())
        with patch.dict(os.environ, {"REMINDER_CHANNEL_ID": " 123456 "}, clear=True):
            self.assertEqual(load_reminder_channel_id(), 123456)


class ReminderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Database(Path(directory.name) / "test.db")
        self.database.initialize()
        data = build_mock_data(NOW)
        self.assignment = replace(data.assignments[0], id="reminder-test", due_at=NOW + timedelta(hours=23),
                                  source_url="https://example.invalid/assignment")
        self.database.seed(MockData(data.courses, (self.assignment,), ()))
        self.deliver = AsyncMock()
        self.service = ReminderService(self.database, self.deliver, clock=lambda: NOW)

    def rows(self) -> list[sqlite3.Row]:
        with self.database.connect() as connection:
            return connection.execute("SELECT * FROM sent_reminders").fetchall()

    async def test_repeated_and_concurrent_checks_do_not_duplicate(self) -> None:
        other = ReminderService(Database(self.database.path), self.deliver, clock=lambda: NOW)
        await asyncio.gather(self.service.check(), other.check(), self.service.check())
        await self.service.check()
        self.deliver.assert_awaited_once_with(self.assignment)
        self.assertEqual(len(self.rows()), 1)
        self.assertIsNotNone(self.rows()[0]["sent_at"])
        self.assertEqual(self.rows()[0]["reminder_threshold"], 24)

    async def test_delivery_persists_after_reopen(self) -> None:
        await self.service.check()
        database = Database(self.database.path)
        database.initialize()
        await ReminderService(database, self.deliver, clock=lambda: NOW).check()
        self.deliver.assert_awaited_once()

    async def test_rejected_delivery_is_not_sent_and_can_retry(self) -> None:
        self.deliver.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "denied")
        with self.assertLogs("uwa_india_bot.reminders", level="WARNING"):
            await self.service.check()
        self.assertEqual(self.rows(), [])
        self.deliver.side_effect = None
        await self.service.check()
        self.assertEqual(self.deliver.await_count, 2)
        self.assertIsNotNone(self.rows()[0]["sent_at"])

    async def test_uncertain_delivery_remains_pending_without_resending(self) -> None:
        self.deliver.side_effect = TimeoutError()
        with self.assertLogs("uwa_india_bot.reminders", level="ERROR"):
            await self.service.check()
        self.assertIsNone(self.rows()[0]["sent_at"])
        await ReminderService(Database(self.database.path), self.deliver, clock=lambda: NOW).check()
        self.deliver.assert_awaited_once()
        self.assertEqual(self.database.pending_reminder_count(), 1)

    async def test_record_failure_does_not_repeat_successful_delivery(self) -> None:
        with patch.object(self.database, "mark_reminder_sent", side_effect=sqlite3.OperationalError("disk")):
            with self.assertLogs("uwa_india_bot.reminders", level="ERROR"):
                await self.service.check()
        await self.service.check()
        self.deliver.assert_awaited_once()
        self.assertIsNone(self.rows()[0]["sent_at"])

    async def test_no_delivery_outside_window_or_after_due(self) -> None:
        for remaining in (169, 100, 18, 10, 0, -1):
            now = self.assignment.due_at - timedelta(hours=remaining)
            await ReminderService(self.database, self.deliver, clock=lambda: now).check()
        self.deliver.assert_not_awaited()
        self.assertEqual(self.rows(), [])

    async def test_expiry_during_check_is_not_delivered(self) -> None:
        clock = Mock(side_effect=[NOW, NOW, self.assignment.due_at])
        await ReminderService(self.database, self.deliver, clock=clock).check()
        self.deliver.assert_not_awaited()
        self.assertEqual(self.rows(), [])

    async def test_later_threshold_still_delivers(self) -> None:
        await self.service.check()
        later = self.assignment.due_at - timedelta(hours=2)
        await ReminderService(self.database, self.deliver, clock=lambda: later).check()
        self.assertEqual(self.deliver.await_count, 2)
        self.assertEqual({row["reminder_threshold"] for row in self.rows()}, {24, 3})

    async def test_delivery_order_is_chronological(self) -> None:
        early = replace(self.assignment, id="early", due_at=NOW + timedelta(hours=2))
        late = replace(self.assignment, id="late", due_at=NOW + timedelta(hours=167))
        self.database.seed(MockData((), (late, early), ()))
        await self.service.check()
        self.assertEqual([call.args[0].id for call in self.deliver.await_args_list], ["early", "reminder-test", "late"])

    def test_message_includes_exact_ist_deadline_and_source(self) -> None:
        embed = reminder_embed(self.assignment)
        self.assertIn(self.assignment.course_code, embed.description)
        self.assertIn(self.assignment.title, embed.description)
        self.assertIn(self.assignment.due_at.astimezone(INDIA_TZ).strftime("%d %b %Y, %I:%M %p"), embed.fields[0].value)
        self.assertIn("IST", embed.fields[0].value)
        self.assertIn(self.assignment.source_url, embed.fields[2].value)
        self.assertIn("SAMPLE DATA", embed.footer.text)
        self.assertEqual(len(reminder_embed(replace(self.assignment, source_url=None)).fields), 2)

    async def test_missing_channel_leaves_loop_disabled(self) -> None:
        async with UwaBot(self.database) as client:
            with patch.object(client.tree, "sync", new_callable=AsyncMock, return_value=[]):
                await client.setup_hook()
            self.assertFalse(client.reminder_loop.is_running())

    async def test_loop_waits_for_ready_and_is_cancelled_on_close(self) -> None:
        client = UwaBot(self.database, reminder_channel_id=123456)
        async with client:
            with patch.object(client.tree, "sync", new_callable=AsyncMock, return_value=[]):
                await client.setup_hook()
            await asyncio.sleep(0)
            self.assertTrue(client.reminder_loop.is_running())
            self.assertEqual(self.rows(), [])
        self.assertFalse(client.reminder_loop.is_running())

    async def test_discord_delivery_uses_configured_channel(self) -> None:
        async with UwaBot(self.database, reminder_channel_id=123456) as client:
            channel = SimpleNamespace(send=AsyncMock())
            with patch.object(client, "get_channel", return_value=None):
                with patch.object(client, "fetch_channel", new_callable=AsyncMock, return_value=channel) as fetch:
                    await client.deliver_reminder(self.assignment)
                    fetch.assert_awaited_once_with(123456)
            channel.send.assert_awaited_once()
            self.assertFalse(channel.send.call_args.kwargs["allowed_mentions"].everyone)

    async def test_loop_handles_errors_without_stopping(self) -> None:
        async with UwaBot(self.database, reminder_channel_id=123456) as client:
            with patch.object(client.reminders, "check", new_callable=AsyncMock, side_effect=sqlite3.OperationalError()):
                with self.assertLogs("uwa_india_bot", level="ERROR"):
                    await client.reminder_loop()
