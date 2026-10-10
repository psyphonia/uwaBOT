from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import bot
from academic_commands import relative_due, event_embed, source_line
from database import Database
from integrations import MockIntegration
from mock_data import build_mock_data
from models import Campus, CalendarEvent, CourseContent, INDIA_TZ
from sync_engine import SyncEngine, SyncResult


NOW = datetime(2026, 10, 10, 20, tzinfo=timezone.utc)


class StudentUXTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.database = Database(Path(folder.name) / "test.db")
        self.database.initialize()

    async def invoke(self, client, command, **arguments):
        interaction = SimpleNamespace(response=SimpleNamespace(send_message=AsyncMock()))
        with patch("academic_commands.utc_now", return_value=NOW):
            await client.tree.get_command(command).callback(interaction, **arguments)
        return interaction.response.send_message.call_args.kwargs["embed"]

    async def test_empty_states_and_unknown_course(self):
        async with bot.UwaBot(self.database) as client:
            for name in ("today", "tomorrow", "week", "due", "announcements", "nextclass"):
                embed = await self.invoke(client, name)
                self.assertTrue(embed.fields[0].value.startswith("No "))
                self.assertNotIn("SAMPLE DATA", embed.footer.text)
            embed = await self.invoke(client, "course", course_code="unknown")
            self.assertEqual(embed.fields[0].name, "Course not found")

    async def test_tomorrow_nextclass_and_week_boundaries(self):
        data = build_mock_data(NOW)
        midnight = datetime(2026, 10, 12, tzinfo=INDIA_TZ).astimezone(timezone.utc)
        original = data.events[0]
        events = [replace(original, id=str(i), start_at=start, end_at=start+timedelta(hours=1))
                  for i, start in enumerate([NOW-timedelta(hours=1), NOW, NOW+timedelta(seconds=1),
                                            midnight, midnight+timedelta(days=1), NOW+timedelta(days=7)])]
        integration = MockIntegration(replace(data, events=tuple(events)))
        await SyncEngine(self.database, {"mock": integration}).sync_all()
        self.assertEqual(self.database.get_next_event(NOW).start_at, NOW+timedelta(seconds=1))
        self.assertNotIn(NOW+timedelta(days=7), [e.start_at for e in self.database.get_events_for_week(NOW)])
        async with bot.UwaBot(self.database) as client:
            embed = await self.invoke(client, "tomorrow", campus=Campus.Mumbai)
            self.assertEqual(len(embed.fields), 1)
            self.assertIn("12 Oct", embed.fields[0].name)
            embed = await self.invoke(client, "nextclass", campus=Campus.Chennai)
            self.assertTrue(embed.fields[0].value.startswith("No "))

    def test_relative_deadlines_and_source_links(self):
        self.assertEqual(relative_due(NOW+timedelta(days=2), NOW), "Due in 2 days")
        self.assertEqual(relative_due(NOW+timedelta(hours=1), NOW), "Due in 1 hour")
        self.assertEqual(relative_due(None, NOW), "Due date unavailable")
        item = replace(build_mock_data(NOW).events[0], source="blackboard", source_url="https://learn.example.test/course")
        embed = event_embed("Classes", [item], Campus.Mumbai)
        self.assertIn("[Blackboard](https://learn.example.test/course)", embed.fields[0].value)
        self.assertNotIn("SAMPLE DATA", embed.footer.text)
        self.assertNotIn("javascript", source_line(replace(item, source_url="javascript:alert(1)")))

    async def test_announcements_campus_html_and_missing_deadline(self):
        data = build_mock_data(NOW)
        content = CourseContent("assessment", "CITS1401", "Undated assessment", "", "resource/x-bb-asmt-test",
                                Campus.Mumbai, "mock", "undated")
        integration = MockIntegration(replace(data, announcements=(replace(data.announcements[0], body="<p>Notice @everyone</p>"),)))
        integration.get_content = AsyncMock(return_value=[content])
        await SyncEngine(self.database, {"mock": integration}).sync_all()
        async with bot.UwaBot(self.database) as client:
            embed = await self.invoke(client, "announcements", campus=Campus.Mumbai)
            self.assertNotIn("<p>", embed.fields[0].value)
            self.assertNotIn("@everyone", embed.fields[0].value)
            embed = await self.invoke(client, "announcements", campus=Campus.Chennai)
            self.assertTrue(embed.fields[0].value.startswith("No "))
            embed = await self.invoke(client, "due", campus=Campus.Mumbai)
            self.assertTrue(any("Due date unavailable" in field.value for field in embed.fields))

    async def test_reminders_status_and_sync_errors(self):
        engine = SyncEngine(self.database, {})
        engine.sync_all = AsyncMock(return_value={"blackboard": SyncResult(error="Access unavailable.")})
        async with bot.UwaBot(self.database, sync_engine=engine) as client:
            embed = await self.invoke(client, "reminders")
            self.assertIn("Disabled", embed.fields[1].value)
            self.assertIn("24 hours", embed.fields[0].value)
            interaction = SimpleNamespace(guild_id=1, permissions=SimpleNamespace(administrator=True),
                response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))
            await client.tree.get_command("sync").callback(interaction)
            self.assertIn("Access unavailable", interaction.followup.send.call_args.kwargs["embed"].fields[0].value)
            engine.sync_all.side_effect = RuntimeError("secret")
            await client.tree.get_command("sync").callback(interaction)
            self.assertNotIn("secret", str(interaction.followup.send.call_args))

    def test_large_embeds_stay_within_discord_limits(self):
        item = build_mock_data(NOW).events[0]
        embed = event_embed("Many events", [replace(item, title="x"*1500)]*50, None)
        self.assertLessEqual(len(embed.fields), 25)
        self.assertLessEqual(len(embed), 6000)
        self.assertTrue(all(len(field.value)<=1024 and len(field.name)<=256 for field in embed.fields))

    async def test_configured_campus_default_and_override(self):
        await SyncEngine(self.database, {"mock": MockIntegration(build_mock_data(NOW))}).sync_all()
        with patch.dict("os.environ", {"UWA_CAMPUS": "Mumbai"}):
            async with bot.UwaBot(self.database) as client:
                embed = await self.invoke(client, "course", course_code="CITS1401")
                self.assertEqual(len(embed.fields), 1)
                self.assertIn("Mumbai", embed.fields[0].name)
                embed = await self.invoke(client, "course", course_code="CITS1401", campus=Campus.Chennai)
                self.assertIn("Chennai", embed.fields[0].name)
