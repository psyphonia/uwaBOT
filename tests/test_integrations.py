from datetime import datetime, timedelta, timezone
import inspect
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from database import Database
from integrations import BlackboardIntegration, MockIntegration, MyCamuIntegration, UniversityIntegration
from blackboard_auth import BlackboardError
from mock_data import MockData, build_mock_data
from models import Announcement, Assignment, CalendarEvent, Campus, Course, INDIA_TZ


NOW = datetime(2026, 10, 9, 7, tzinfo=INDIA_TZ)
METHODS = ("get_courses", "get_assignments", "get_calendar_events", "get_announcements")


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_mock_returns_existing_normalized_records(self) -> None:
        data = build_mock_data(NOW)
        integration: UniversityIntegration = MockIntegration(data)
        for method, expected, model in zip(
            METHODS, (data.courses, data.assignments, data.events, data.announcements),
            (Course, Assignment, CalendarEvent, Announcement), strict=True,
        ):
            with self.subTest(method=method):
                records = await getattr(integration, method)()
                self.assertEqual(records, list(expected))
                self.assertTrue(all(isinstance(record, model) for record in records))
                self.assertEqual({record.campus for record in records}, set(Campus))
                self.assertTrue(all(record.source == "mock" for record in records))

    async def test_mock_snapshots_dates_and_returns_independent_lists(self) -> None:
        data = build_mock_data(NOW)
        with patch("integrations.build_mock_data", return_value=data) as factory:
            integration = MockIntegration()
            for method in METHODS:
                first = await getattr(integration, method)()
                expected = first.copy()
                first.clear()
                self.assertEqual(await getattr(integration, method)(), expected)
            factory.assert_called_once()
            self.assertEqual(factory.call_args.args[0].utcoffset(), timedelta(0))

    async def test_empty_mock_is_supported(self) -> None:
        integration = MockIntegration(MockData((), (), ()))
        for method in METHODS:
            self.assertEqual(await getattr(integration, method)(), [])

    async def test_every_stub_method_fails_clearly_without_network(self) -> None:
        with patch("socket.socket", side_effect=AssertionError("No network permitted")):
            for provider, name in ((BlackboardIntegration, "Blackboard"),):
                integration: UniversityIntegration = provider()
                for method in METHODS:
                    with self.subTest(provider=name, method=method):
                        self.assertTrue(inspect.iscoroutinefunction(getattr(integration, method)))
                        with self.assertRaisesRegex(BlackboardError, name + ".*authorized"):
                            await getattr(integration, method)()

    async def test_announcements_are_valid(self) -> None:
        integration = MockIntegration(build_mock_data(NOW))
        announcements = await integration.get_announcements()
        courses = {(course.code, course.campus) for course in await integration.get_courses()}
        self.assertEqual(len({item.id for item in announcements}), len(announcements))
        for item in announcements:
            self.assertTrue(item.title and item.body)
            self.assertEqual(item.published_at, NOW.astimezone(timezone.utc))
            self.assertEqual(item.published_at.utcoffset(), timedelta(0))
            self.assertIn((item.course_code, item.campus), courses)
            self.assertIsNone(item.source_url)

    async def test_integration_records_round_trip_through_existing_database(self) -> None:
        integration = MockIntegration(build_mock_data(NOW))
        data = MockData(
            tuple(await integration.get_courses()), tuple(await integration.get_assignments()),
            tuple(await integration.get_calendar_events()), tuple(await integration.get_announcements()),
        )
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            database.initialize()
            database.seed(data)
            database.seed(data)
            courses = [record for code in sorted({course.code for course in data.courses})
                       for record in database.get_course(code)]
            self.assertCountEqual(courses, data.courses)
            self.assertCountEqual(database.get_upcoming_assignments(NOW - timedelta(days=2)), data.assignments)
            self.assertCountEqual(database.get_events_between(NOW, NOW + timedelta(days=10)), data.events)
