from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

import bot
from database import Database
from integrations import MockIntegration
from mock_data import build_mock_data, MockData
from models import Campus, CourseContent, CalendarEvent
from sync_engine import SyncEngine
from integrations import MyCamuIntegration, BlackboardIntegration


NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)


def provider(source):
    data = build_mock_data(NOW)
    courses = tuple(replace(item, source=source, external_id=f"{item.code}-{item.campus.value}") for item in data.courses)
    def changed(items):
        return tuple(replace(item, source=source, external_id=item.id) for item in items)
    return MockIntegration(MockData(courses, changed(data.assignments), changed(data.events), changed(data.announcements)))


class SyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.database = Database(Path(temporary.name) / "sync.db")
        self.database.initialize()

    def rows(self, table):
        with self.database.connect() as connection:
            return [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]

    async def test_first_repeat_stats_and_campuses(self):
        engine = SyncEngine(self.database, {"mock": MockIntegration(build_mock_data(NOW))})
        first = (await engine.sync_all())["mock"]
        self.assertIsNone(first.error)
        self.assertEqual(first.counts["courses"]["inserted"], 6)
        self.assertEqual(first.counts["assignments"]["inserted"], 8)
        second = await engine.sync_mock()
        self.assertEqual(second.counts["courses"]["unchanged"], 6)
        self.assertEqual(second.counts["assignments"]["unchanged"], 8)
        self.assertEqual(len(self.rows("assignments")), 8)
        self.assertEqual({row["campus"] for row in self.rows("courses")}, {"Mumbai", "Chennai"})
        self.assertTrue(all(row["last_synced_at"] for row in self.rows("announcements")))
        self.database.initialize()

    async def test_seed_adoption_and_repeat_seed(self):
        data = build_mock_data(NOW)
        self.database.seed(data)
        assignment = data.assignments[0]
        self.database.claim_reminder(assignment, 168, NOW)
        engine = SyncEngine(self.database, {"mock": MockIntegration(data)})
        result = await engine.sync_mock()
        self.assertIsNone(result.error)
        self.assertEqual(result.counts["courses"]["inserted"], 0)
        self.database.seed(data)
        self.assertEqual(len(self.rows("courses")), 6)
        self.assertEqual(len(self.rows("assignments")), 8)
        self.assertEqual(len(self.rows("sent_reminders")), 1)

    async def test_updates_due_content_announcements(self):
        integration = provider("blackboard")
        content = CourseContent("content", "CITS1401", "Original", "text", "document", Campus.Mumbai,
                                "blackboard", "external-content")
        integration.get_content = AsyncMock(return_value=[content])
        engine = SyncEngine(self.database, {"blackboard": integration})
        self.assertIsNone((await engine.sync_blackboard()).error)
        old = integration._data
        integration._data = replace(old, assignments=(replace(old.assignments[0], due_at=NOW+timedelta(days=20)), *old.assignments[1:]),
                                    announcements=(replace(old.announcements[0], body="Changed"), *old.announcements[1:]))
        integration.get_content.return_value = [replace(content, title="Updated")]
        result = await engine.sync_blackboard()
        self.assertEqual(result.counts["assignments"]["updated"], 1)
        self.assertEqual(result.counts["content"]["updated"], 1)
        self.assertEqual(result.counts["announcements"]["updated"], 1)
        self.assertEqual(len(self.rows("assignments")), 8)
        self.assertEqual(self.rows("course_content")[0]["title"], "Updated")

    async def test_source_separation_and_partial_failure(self):
        first, second = provider("mycamu"), provider("blackboard")
        engine = SyncEngine(self.database, {"mycamu": first, "blackboard": second})
        results = await engine.sync_all()
        self.assertTrue(all(result.error is None for result in results.values()))
        self.assertEqual(len(self.rows("courses")), 12)
        self.assertEqual(len(self.rows("assignments")), 16)
        self.database.initialize()
        second.get_assignments = AsyncMock(side_effect=RuntimeError("private-token"))
        results = await engine.sync_all()
        self.assertIsNone(results["mycamu"].error)
        self.assertIsNotNone(results["blackboard"].error)
        self.assertNotIn("private-token", results["blackboard"].error)

    async def test_duplicates_conflicts_and_atomic_rollback(self):
        integration = provider("mycamu")
        old = integration._data
        integration._data = replace(old, assignments=old.assignments + (old.assignments[0],))
        engine = SyncEngine(self.database, {"mycamu": integration})
        self.assertEqual((await engine.sync_mycamu()).counts["assignments"]["inserted"], 8)
        integration._data = replace(old, assignments=old.assignments + (replace(old.assignments[0], title="conflict"),))
        self.assertIsNotNone((await engine.sync_mycamu()).error)
        integration._data = replace(old, assignments=(replace(old.assignments[0], course_code="UNKNOWN"),))
        before = self.rows("courses")
        self.assertIsNotNone((await engine.sync_mycamu()).error)
        self.assertEqual(self.rows("courses"), before)

    async def test_holidays_and_zero_duration_deadlines(self):
        integration = provider("mycamu")
        event = CalendarEvent("holiday", None, "Holiday", NOW, NOW, "", Campus.Chennai,
                              "mycamu", "holiday", all_day=True)
        integration._data = replace(integration._data, events=(event,))
        engine = SyncEngine(self.database, {"mycamu": integration})
        self.assertIsNone((await engine.sync_mycamu()).error)
        self.assertTrue(self.database.get_events_for_day(NOW)[0].all_day)
        self.assertIsNone(self.database.get_events_for_day(NOW)[0].course_code)

    async def test_admin_command_denies_nonadmin_and_dm(self):
        engine = SyncEngine(self.database, {"mock": MockIntegration(build_mock_data(NOW))})
        engine.sync_all = AsyncMock()
        async with bot.UwaBot(self.database, sync_engine=engine) as client:
            command = client.tree.get_command("sync")
            for guild, admin in [(1, False), (None, True)]:
                interaction = SimpleNamespace(guild_id=guild, permissions=SimpleNamespace(administrator=admin),
                    response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()))
                await command.callback(interaction)
                interaction.response.send_message.assert_awaited_once()
            engine.sync_all.assert_not_awaited()

    async def test_admin_command_reports_real_stats(self):
        engine = SyncEngine(self.database, {"mock": MockIntegration(build_mock_data(NOW))})
        async with bot.UwaBot(self.database, sync_engine=engine) as client:
            interaction = SimpleNamespace(guild_id=1, permissions=SimpleNamespace(administrator=True),
                response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))
            await client.tree.get_command("sync").callback(interaction)
            embed = interaction.followup.send.call_args.kwargs["embed"]
            self.assertIn("6 added", embed.fields[0].value)
            self.assertTrue(interaction.followup.send.call_args.kwargs["ephemeral"])

    async def test_unconfigured_real_adapters_do_not_block_mock(self):
        engine = SyncEngine(self.database, {"mycamu": MyCamuIntegration(), "blackboard": BlackboardIntegration(),
                                           "mock": MockIntegration(build_mock_data(NOW))})
        results = await engine.sync_all()
        self.assertIsNotNone(results["mycamu"].error)
        self.assertIsNotNone(results["blackboard"].error)
        self.assertIsNone(results["mock"].error)

    async def test_shared_campus_fallback_and_changed_course(self):
        integration = provider("mycamu")
        data = integration._data
        course = replace(data.courses[0], campus=None)
        integration._data = MockData((course,), (data.assignments[1],), ())
        engine = SyncEngine(self.database, {"mycamu": integration})
        self.assertIsNone((await engine.sync_mycamu()).error)
        integration._data = replace(integration._data, courses=(replace(course, name="Updated name"),))
        self.assertEqual((await engine.sync_mycamu()).counts["courses"]["updated"], 1)
        self.assertEqual(len(self.rows("courses")), 1)

    async def test_populated_legacy_events_migrate_without_loss(self):
        data = build_mock_data(NOW)
        self.database.seed(data)
        with self.database.connect() as connection:
            connection.execute("ALTER TABLE calendar_events RENAME TO upgraded_events")
            connection.execute("""CREATE TABLE calendar_events (
                id TEXT PRIMARY KEY, course_id INTEGER NOT NULL REFERENCES courses(id),
                title TEXT NOT NULL, start_at TEXT NOT NULL, end_at TEXT NOT NULL CHECK(end_at>start_at),
                location TEXT NOT NULL, campus TEXT REFERENCES campuses(name), source TEXT NOT NULL,
                external_id TEXT, source_url TEXT)""")
            connection.execute("""INSERT INTO calendar_events SELECT id, course_id, title, start_at,
                end_at, location, campus, source, external_id, source_url FROM upgraded_events""")
            connection.execute("DROP TABLE upgraded_events")
        self.database.initialize()
        self.database.initialize()
        self.assertEqual(len(self.rows("calendar_events")), 12)
        with self.database.connect() as connection:
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
