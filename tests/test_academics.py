from dataclasses import is_dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import discord
from discord import app_commands

from academic_commands import register_academic_commands
from database import Database, utc_text
from mock_data import MockData, build_mock_data
from models import Campus, INDIA_TZ


NOW = datetime(2026, 10, 9, 7, tzinfo=INDIA_TZ)


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data = build_mock_data(NOW)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Database(Path(directory.name) / "test.db")
        self.database.initialize()
        self.database.seed(self.data)

    def test_models_and_sample_data_are_valid(self) -> None:
        self.assertEqual({campus.value for campus in Campus}, {"Mumbai", "Chennai"})
        course_keys = {(course.code, course.campus) for course in self.data.courses}
        self.assertEqual(len(course_keys), len(self.data.courses))
        for collection in (self.data.assignments, self.data.events):
            self.assertEqual(len({item.id for item in collection}), len(collection))
            self.assertEqual({item.campus for item in collection}, set(Campus))
            for item in collection:
                self.assertTrue(is_dataclass(item))
                self.assertIn((item.course_code, item.campus), course_keys)
                self.assertEqual(item.source, "mock")
        for course in self.data.courses:
            self.assertTrue(is_dataclass(course))
            self.assertTrue(course.name)
        for event in self.data.events:
            self.assertEqual(event.start_at.utcoffset(), timedelta(0))
            self.assertEqual(event.end_at.utcoffset(), timedelta(0))
            self.assertLess(event.start_at, event.end_at)
            self.assertTrue(event.location)
        for assignment in self.data.assignments:
            self.assertEqual(assignment.due_at.utcoffset(), timedelta(0))
            self.assertIsNone(assignment.source_url)
        with self.assertRaises(ValueError):
            build_mock_data(datetime(2026, 10, 9))

    def test_events_are_chronological_and_campus_filtered(self) -> None:
        for campus in (None, *Campus):
            events = self.database.get_events_for_week(NOW, campus)
            self.assertEqual(len(events), 10 if campus is None else 5)
            self.assertEqual([event.start_at for event in events], sorted(event.start_at for event in events))
            if campus:
                self.assertTrue(all(event.campus == campus for event in events))

    def test_event_window_includes_start_and_excludes_end(self) -> None:
        event = self.data.events[0]
        end = NOW + timedelta(days=7)
        events = tuple(replace(event, id=str(index), start_at=start, end_at=start + timedelta(hours=1))
                       for index, start in enumerate((NOW - timedelta(seconds=1), NOW, end - timedelta(seconds=1), end)))
        data = MockData((), (), events)
        with self.database.connect() as connection:
            connection.execute("DELETE FROM calendar_events")
        self.database.seed(data)
        self.assertEqual([item.id for item in self.database.get_events_between(NOW, end)], ["1", "2"])

    def test_assignments_are_sorted_and_past_deadlines_excluded(self) -> None:
        for campus in (None, *Campus):
            assignments = self.database.get_upcoming_assignments(NOW, campus)
            self.assertEqual(len(assignments), 6 if campus is None else 3)
            dates = [item.due_at for item in assignments]
            self.assertEqual(dates, sorted(dates))
            self.assertTrue(all(date >= NOW for date in dates))
            if campus:
                self.assertTrue(all(item.campus == campus for item in assignments))
        deadline = self.data.assignments[0].due_at
        self.assertIn(self.data.assignments[0], self.database.get_upcoming_assignments(deadline))

    def test_course_lookup_normalization_unknown_and_campus(self) -> None:
        self.assertEqual(len(self.database.get_course(" cits1401 ")), 2)
        for campus in Campus:
            self.assertEqual([item.campus for item in self.database.get_course("CITS1401", campus)], [campus])
        self.assertEqual(self.database.get_course("NOT_A_COURSE"), [])

    def test_initialization_and_seeding_are_repeatable_and_persistent(self) -> None:
        def snapshot() -> tuple[list[tuple], ...]:
            with self.database.connect() as connection:
                return tuple([tuple(row) for row in connection.execute(query)] for query in (
                    "SELECT * FROM campuses ORDER BY name",
                    "SELECT * FROM courses ORDER BY id",
                    "SELECT * FROM assignments ORDER BY id",
                    "SELECT * FROM calendar_events ORDER BY id",
                ))

        before = snapshot()
        self.assertEqual(tuple(map(len, before)), (2, 6, 8, 12))
        self.database.initialize()
        self.database.seed(self.data)
        self.assertEqual(snapshot(), before)
        self.database.seed(build_mock_data(NOW + timedelta(days=10)))
        self.assertEqual(snapshot(), before, "Restart must not move existing deadlines")
        reopened = Database(self.database.path)
        self.assertEqual(reopened.get_course("CITS1401"), self.database.get_course("CITS1401"))

    def test_new_database_is_empty_until_seeded(self) -> None:
        database = Database(self.database.path.with_name("empty.db"))
        database.initialize()
        database.initialize()
        self.assertTrue(database.path.is_file())
        self.assertEqual(database.get_course("CITS1401"), [])
        self.assertEqual(database.get_events_for_day(NOW), [])
        self.assertEqual(database.get_events_for_week(NOW), [])
        self.assertEqual(database.get_upcoming_assignments(NOW), [])

    def test_shared_records_match_both_campuses_without_duplicates(self) -> None:
        course = replace(self.data.courses[0], code="SHARED101", campus=None,
                         external_id="sample-course", source_url="https://example.invalid/course")
        event = replace(self.data.events[0], id="shared-event", course_code=course.code, campus=None,
                        external_id="sample-event", source_url="https://example.invalid/event")
        assignment = replace(self.data.assignments[0], id="shared-assignment", course_code=course.code,
                             campus=None, external_id="sample-assignment")
        samples = MockData((course,), (assignment,), (event,))
        self.database.seed(samples)
        self.database.seed(samples)
        for campus in (None, *Campus):
            self.assertEqual(self.database.get_course(course.code, campus), [course])
            events = self.database.get_events_for_week(NOW, campus)
            self.assertEqual([item for item in events if item.id == event.id], [event])
            assignments = self.database.get_upcoming_assignments(NOW, campus)
            self.assertEqual([item for item in assignments if item.id == assignment.id], [assignment])

    def test_day_boundaries_use_india_midnight_and_store_utc(self) -> None:
        start = NOW.replace(hour=0)
        end = start + timedelta(days=1)
        events = tuple(replace(self.data.events[0], id=f"boundary-{index}", start_at=when,
                               end_at=when + timedelta(minutes=30)) for index, when in enumerate(
            (start - timedelta(microseconds=1), start, end - timedelta(microseconds=1), end)))
        with self.database.connect() as connection:
            connection.execute("DELETE FROM calendar_events")
        self.database.seed(MockData((), (), events))
        # Same instant is October 8 UTC and October 9 IST.
        now = datetime(2026, 10, 8, 20, tzinfo=timezone.utc)
        result = self.database.get_events_for_day(now)
        self.assertEqual([event.id for event in result], ["boundary-1", "boundary-2"])
        with self.database.connect() as connection:
            stored = connection.execute("SELECT start_at FROM calendar_events WHERE id = ?",
                                        ("boundary-1",)).fetchone()["start_at"]
        self.assertEqual(stored, "2026-10-08T18:30:00.000000+00:00")
        self.assertTrue(all(event.start_at.utcoffset() == timedelta(0) for event in result))

    def test_naive_dates_are_rejected(self) -> None:
        naive = NOW.replace(tzinfo=None)
        for query in (utc_text, self.database.get_events_for_day, self.database.get_events_for_week,
                      self.database.get_upcoming_assignments):
            with self.assertRaises(ValueError):
                query(naive)

    def test_seed_failure_rolls_back_and_foreign_keys_are_enabled(self) -> None:
        course = replace(self.data.courses[0], code="ROLLBACK101")
        event = replace(self.data.events[0], id="bad-event", course_code=course.code,
                        end_at=self.data.events[0].start_at)
        with self.assertRaises(sqlite3.IntegrityError):
            self.database.seed(MockData((course,), (), (event,)))
        self.assertEqual(self.database.get_course(course.code), [])
        with self.database.connect() as connection:
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_course_input_is_treated_as_data(self) -> None:
        self.assertEqual(self.database.get_course("' OR 1=1 --"), [])
        self.assertEqual(len(self.database.get_course("CITS1401")), 2)


class AcademicCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.client = discord.Client(intents=discord.Intents(guilds=True))
        await self.client.__aenter__()
        self.tree = app_commands.CommandTree(self.client)
        self.data = build_mock_data(NOW)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Database(Path(directory.name) / "test.db")
        self.database.initialize()
        self.database.seed(self.data)
        register_academic_commands(self.tree, self.database)

    async def asyncTearDown(self) -> None:
        await self.client.close()

    async def invoke(self, name: str, now: datetime = NOW, **kwargs: object) -> discord.Embed:
        interaction = SimpleNamespace(response=SimpleNamespace(send_message=AsyncMock()))
        with patch("academic_commands.utc_now", return_value=now):
            await self.tree.get_command(name).callback(interaction, **kwargs)
        interaction.response.send_message.assert_awaited_once()
        embed = interaction.response.send_message.call_args.kwargs["embed"]
        self.assertIn("SAMPLE DATA", embed.footer.text)
        self.assertLessEqual(len(embed.fields), 25)
        self.assertLessEqual(len(embed), 6000)
        return embed

    async def test_today_includes_earlier_classes_and_is_sorted(self) -> None:
        embed = await self.invoke("today", now=NOW.replace(hour=16), campus=Campus.Mumbai)
        self.assertEqual(len(embed.fields), 2)
        self.assertIn("09:00", embed.fields[0].name)
        self.assertIn("14:00", embed.fields[1].name)
        self.assertTrue(all("Mumbai" in field.value for field in embed.fields))

    async def test_today_uses_india_date_at_utc_boundary(self) -> None:
        # It is still October 8 in UTC, but already October 9 in India.
        embed = await self.invoke("today", now=datetime(2026, 10, 8, 20, tzinfo=timezone.utc))
        self.assertEqual(len(embed.fields), 4)
        self.assertTrue(all("09 Oct" in field.name for field in embed.fields))

    async def test_week_excludes_earlier_today_and_matches_sorted_records(self) -> None:
        now = NOW.replace(hour=12)
        embed = await self.invoke("week", now=now, campus=Campus.Chennai)
        expected = self.database.get_events_for_week(now, Campus.Chennai)
        self.assertEqual(len(embed.fields), 4)
        for field, event in zip(embed.fields, expected, strict=True):
            self.assertIn(event.start_at.astimezone(INDIA_TZ).strftime("%a %d %b, %H:%M"), field.name)
            self.assertIn("Chennai", field.value)

    async def test_due_response_is_sorted_and_filtered(self) -> None:
        embed = await self.invoke("due", campus=Campus.Mumbai)
        expected = self.database.get_upcoming_assignments(NOW, Campus.Mumbai)
        self.assertEqual(len(embed.fields), 3)
        for field, assignment in zip(embed.fields, expected, strict=True):
            self.assertIn(assignment.title, field.name)
            self.assertIn("Mumbai", field.value)
            self.assertIn("IST", field.value)

    async def test_known_and_unknown_courses(self) -> None:
        embed = await self.invoke("course", course_code=" cits1401 ", campus=Campus.Chennai)
        self.assertEqual(len(embed.fields), 1)
        self.assertIn("CITS1401", embed.fields[0].name)
        self.assertIn("Chennai", embed.fields[0].name)
        embed = await self.invoke("course", course_code="unknown")
        self.assertEqual(embed.fields[0].name, "Course not found")

    async def test_unfiltered_responses_and_empty_periods(self) -> None:
        for command, kwargs, count in (("today", {}, 4), ("week", {}, 10), ("due", {}, 6),
                                       ("course", {"course_code": "CITS1401"}, 2)):
            embed = await self.invoke(command, **kwargs)
            self.assertEqual(len(embed.fields), count)
            self.assertIn("Mumbai and Chennai", embed.description)
        for command in ("today", "week", "due"):
            embed = await self.invoke(command, now=NOW + timedelta(days=30))
            self.assertEqual(len(embed.fields), 1)
            self.assertTrue(embed.fields[0].value.startswith("No "))

    async def test_slash_command_campus_choices(self) -> None:
        for command in self.tree.get_commands():
            payload = command.to_dict(self.tree)
            campus = next(option for option in payload["options"] if option["name"] == "campus")
            self.assertFalse(campus["required"])
            self.assertEqual({choice["value"] for choice in campus["choices"]}, {"Mumbai", "Chennai"})

    async def test_commands_read_current_database_contents(self) -> None:
        with self.database.connect() as connection:
            connection.execute("UPDATE courses SET name = ? WHERE code = ?", ("Updated sample name", "CITS1401"))
            connection.execute("DELETE FROM calendar_events")
            connection.execute("DELETE FROM assignments")
        embed = await self.invoke("course", course_code="CITS1401")
        self.assertTrue(all(field.value == "Updated sample name" for field in embed.fields))
        for name in ("today", "week", "due"):
            embed = await self.invoke(name)
            self.assertTrue(embed.fields[0].value.startswith("No "))

    async def test_shared_campus_presentation(self) -> None:
        course = replace(self.data.courses[0], code="SHARED101", campus=None)
        event = replace(self.data.events[0], id="shared-event", course_code=course.code,
                        campus=None, start_at=NOW, end_at=NOW + timedelta(hours=1))
        assignment = replace(self.data.assignments[0], id="shared-assignment", course_code=course.code, campus=None)
        self.database.seed(MockData((course,), (assignment,), (event,)))
        for campus in Campus:
            embed = await self.invoke("course", course_code=course.code, campus=campus)
            self.assertIn("Mumbai and Chennai", embed.fields[0].name)
            for command in ("today", "week", "due"):
                embed = await self.invoke(command, campus=campus)
                self.assertTrue(any("Mumbai and Chennai" in field.value for field in embed.fields))
