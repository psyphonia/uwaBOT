import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import AsyncMock, patch

from integrations import MyCamuIntegration
from models import Campus, INDIA_TZ
from mycamu import MyCamuConfigurationError, MyCamuRequestError, MyCamuResponse
from mycamu_config import MyCamuConfig, MyCamuEndpoints
from mycamu_parsing import MyCamuResponseError


NOW = datetime(2026, 10, 10, 9, tzinfo=INDIA_TZ)


def assignment(identity: str = "a1", due: str = "2026-10-11", **extra: object) -> dict:
    return {"CmAssID": identity, "Title": "Sample assignment", "SubId": "subject-1",
            "assgnDueDt": due, "endTme": "23:59", **extra}


def envelope(payload: object, wrapped: bool = True) -> MyCamuResponse:
    value = {"data": payload, "errors": []}
    return MyCamuResponse(200, {"output": value} if wrapped else value)


def page(items: list, count: int | None = None) -> MyCamuResponse:
    return envelope({"data": items, "pageData": {"itemCount": len(items) if count is None else count}})


class MyCamuTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.config = MyCamuConfig(
            campus=Campus.Mumbai, context={"StuID": "fake-student", "InId": "fake-institute"},
            subject_codes={"subject-1": "CITS1401"}, source_timezone=INDIA_TZ,
            timetable_course_field="sampleSubject", timetable_title_field="sampleTitle",
            timetable_location_field="sampleRoom",
        )
        self.transport = AsyncMock()
        self.auth = AsyncMock(return_value={"Authorization": "Bearer fake-test-only"})
        self.integration = MyCamuIntegration(self.config, transport=self.transport, auth_provider=self.auth)

    async def test_missing_auth_transport_credentials_and_campus_fail(self) -> None:
        with patch("socket.socket", side_effect=AssertionError("No network")):
            for method in ("get_courses", "get_assignments", "get_calendar_events", "get_attendance"):
                with self.subTest(method=method):
                    with self.assertRaises(MyCamuConfigurationError):
                        await getattr(MyCamuIntegration(), method)()
        with self.assertRaises(MyCamuConfigurationError):
            await MyCamuIntegration(self.config, auth_provider=self.auth).get_courses()
        for headers in ({}, {"Authorization": " "}, {"Cookie": "fake-session"}):
            self.auth.return_value = headers
            with self.assertRaises(MyCamuConfigurationError):
                await self.integration.get_courses()
        self.transport.post.assert_not_awaited()
        self.auth.return_value = {"Authorization": "Bearer fake-test-only"}
        self.integration.config = replace(self.config, campus=None)
        with self.assertRaises(MyCamuConfigurationError):
            await self.integration.get_courses()

    async def test_courses_are_normalized_deduplicated_and_campus_specific(self) -> None:
        row = {"SubId": "subject-1", "SubNa": "Sample programming"}
        self.transport.post.return_value = envelope([row, row], wrapped=False)
        for campus in Campus:
            self.integration.config = replace(self.config, campus=campus)
            records = await self.integration.get_courses()
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].code, "CITS1401")
            self.assertEqual(records[0].campus, campus)
            self.assertEqual(records[0].external_id, "subject-1")
            self.assertEqual(records[0].source, "mycamu")
        self.assertEqual(self.transport.post.call_args.args[0], self.config.base_url + self.config.endpoints.courses)

    async def test_assignments_pagination_sorting_and_deadline_timezone(self) -> None:
        late = assignment("a2", "2026-10-12")
        early = assignment("a1")
        self.transport.post.side_effect = [page([late], 2), page([early], 2)]
        records = await self.integration.get_assignments()
        self.assertEqual([record.external_id for record in records], ["a1", "a2"])
        self.assertEqual(records[0].due_at, datetime(2026, 10, 11, 18, 29, tzinfo=timezone.utc))
        self.assertEqual(records[0].source, "mycamu")
        calls = self.transport.post.await_args_list
        self.assertEqual([call.kwargs["body"]["pageNo"] for call in calls], [1, 2])
        self.assertEqual(calls[0].kwargs["body"]["StuID"], "fake-student")
        self.assertNotIn("Authorization", calls[0].kwargs["body"])

    async def test_duplicate_assignments_and_conflicts(self) -> None:
        self.transport.post.return_value = page([assignment(), assignment()], 1)
        self.assertEqual(len(await self.integration.get_assignments()), 1)
        self.transport.post.return_value = page([assignment(), assignment(Title="Different")], 1)
        with self.assertRaises(MyCamuResponseError):
            await self.integration.get_assignments()

    async def test_pagination_does_not_loop_or_return_partial_results(self) -> None:
        self.transport.post.return_value = page([assignment()], 2)
        with self.assertRaisesRegex(MyCamuResponseError, "advance"):
            await self.integration.get_assignments()
        self.integration.config = replace(self.config, max_pages=1)
        with self.assertRaisesRegex(MyCamuResponseError, "limit"):
            await self.integration.get_assignments()
        self.transport.post.return_value = page([], 1)
        with self.assertRaises(MyCamuResponseError):
            await self.integration.get_assignments()

    async def test_missing_deadline_fields_and_mapping_are_rejected(self) -> None:
        for row in (assignment(endTme=None), assignment(Title=""), assignment(SubId="unknown"),
                    assignment(assgnDueDt="bad"), assignment(endTme="25:00")):
            self.transport.post.return_value = page([row])
            with self.assertRaises(MyCamuResponseError):
                await self.integration.get_assignments()
        self.transport.post.return_value = page([assignment()])
        self.integration.config = replace(self.config, source_timezone=None)
        with self.assertRaises(MyCamuResponseError):
            await self.integration.get_assignments()

    async def test_offset_deadline_without_end_time(self) -> None:
        self.transport.post.return_value = page([assignment(due="2026-10-11T23:59:00+05:30", endTme=None)])
        self.integration.config = replace(self.config, source_timezone=None)
        self.assertEqual((await self.integration.get_assignments())[0].due_at.hour, 18)

    def event(self, identity: str, start: str, end: str) -> dict:
        return {"_id": identity, "start": start, "end": end, "sampleSubject": "subject-1",
                "sampleTitle": "Sample class", "sampleRoom": "Sample room"}

    async def test_timetable_ordering_duplicates_and_window(self) -> None:
        early = self.event("t1", "2026-10-10T10:00:00+05:30", "2026-10-10T11:00:00+05:30")
        late = self.event("t2", "2026-10-10T14:00:00+05:30", "2026-10-10T15:00:00+05:30")
        past = self.event("t0", "2026-10-09T10:00:00+05:30", "2026-10-09T11:00:00+05:30")
        self.transport.post.return_value = envelope([{"Periods": [late, early, early, past]}])
        records = await self.integration.get_timetable(NOW, NOW + timedelta(days=1))
        self.assertEqual([record.external_id for record in records], ["t1", "t2"])
        self.assertEqual(records[0].location, "Sample room")
        self.assertEqual(records[0].start_at.utcoffset(), timedelta(0))
        self.assertFalse(self.transport.post.call_args.kwargs["body"]["isShowCancelledPeriod"])
        self.assertTrue(all(call.kwargs["body"]["start"] == call.kwargs["body"]["end"]
                            for call in self.transport.post.await_args_list))

    async def test_timetable_unknown_fields_invalid_intervals_and_naive_dates(self) -> None:
        self.integration.config = replace(self.config, timetable_course_field=None)
        self.transport.post.return_value = envelope([])
        with self.assertRaises(MyCamuResponseError):
            await self.integration.get_timetable(NOW, NOW + timedelta(days=1))
        self.integration.config = self.config
        self.transport.post.return_value = envelope([{"Periods": [self.event("t1", "bad", "bad")]}])
        with self.assertRaises(MyCamuResponseError):
            await self.integration.get_timetable(NOW, NOW + timedelta(days=1))
        with self.assertRaises(MyCamuConfigurationError):
            await self.integration.get_timetable(NOW.replace(tzinfo=None), NOW)

    async def test_holidays_all_day_mapping_and_ambiguity(self) -> None:
        row = {"_id": "h1", "Name": "Sample holiday", "HldDtFrom": "2026-10-11", "HldDtTo": "2026-10-11"}
        self.transport.post.return_value = envelope([row, row])
        with self.assertRaises(MyCamuResponseError):
            await self.integration.get_holidays()
        self.integration.config = replace(self.config, holiday_end_inclusive=True)
        result = await self.integration.get_holidays()
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0].all_day)
        self.assertIsNone(result[0].course_code)
        self.assertEqual(result[0].end_at - result[0].start_at, timedelta(days=1))

    async def test_combined_calendar_contains_timetable_and_holidays(self) -> None:
        event = self.event("t1", NOW.isoformat(), (NOW + timedelta(hours=1)).isoformat())
        holiday = {"_id": "h1", "Name": "Holiday", "HldDtFrom": NOW.isoformat(),
                   "HldDtTo": (NOW + timedelta(days=1)).isoformat()}
        self.transport.post.side_effect = [envelope([{"Periods": [event]}])] * 8 + [envelope([holiday])]
        with patch("mycamu.datetime") as clock:
            clock.now.return_value = NOW
            records = await self.integration.get_calendar_events()
        self.assertEqual(len(records), 2)
        self.assertEqual({row.id.split(":")[1] for row in records}, {"timetable", "holiday"})

    async def test_attendance_summary_and_invalid_data(self) -> None:
        self.transport.post.return_value = envelope({"OvrAllCnt": 10, "OvrAllPCnt": 8, "OvrAllPrcntg": 80})
        record = (await self.integration.get_attendance())[0]
        self.assertEqual((record.total_count, record.present_count, record.percentage), (10, 8, 80))
        for payload in ({}, {"OvrAllCnt": 1, "OvrAllPCnt": 2}, {"OvrAllPrcntg": 101}, []):
            self.transport.post.return_value = envelope(payload)
            with self.assertRaises(MyCamuResponseError):
                await self.integration.get_attendance()

    async def test_timeouts_and_errors_do_not_expose_credentials_or_payloads(self) -> None:
        for error in (TimeoutError("secret"), RuntimeError("Bearer secret")):
            self.transport.post.side_effect = error
            with self.assertRaises(MyCamuRequestError) as caught:
                await self.integration.get_courses()
            self.assertNotIn("secret", str(caught.exception))
        self.transport.post.side_effect = None
        self.transport.post.return_value = MyCamuResponse(401, {"private": "secret"})
        with self.assertRaisesRegex(MyCamuRequestError, "401"):
            await self.integration.get_courses()
        self.integration.config = replace(self.config, timeout_seconds=0.01)
        async def slow(*args, **kwargs):
            await asyncio.sleep(1)
        self.transport.post.side_effect = slow
        with self.assertRaisesRegex(MyCamuRequestError, "timed out"):
            await self.integration.get_courses()

    async def test_malformed_envelopes_and_provider_errors(self) -> None:
        for body in (None, "not JSON", {}, {"output": {}}, {"data": [], "errors": ["private"]},
                     {"data": [None]}, {"data": [{"SubId": "subject-1"}]}):
            self.transport.post.return_value = MyCamuResponse(200, body)
            with self.assertRaises(MyCamuResponseError):
                await self.integration.get_courses()

    async def test_endpoint_configuration_is_separate_and_bad_urls_rejected(self) -> None:
        endpoints = replace(MyCamuEndpoints(), courses="/api/custom-approved-subjects")
        self.integration.config = replace(self.config, base_url="https://example.invalid", endpoints=endpoints)
        self.transport.post.return_value = envelope([])
        await self.integration.get_courses()
        self.assertEqual(self.transport.post.call_args.args[0], "https://example.invalid/api/custom-approved-subjects")
        self.integration.config = replace(self.config, base_url="http://example.invalid")
        with self.assertRaises(MyCamuConfigurationError):
            await self.integration.get_courses()
        self.integration.config = replace(self.config, endpoints=replace(endpoints, courses="//other.invalid/api"))
        with self.assertRaises(MyCamuConfigurationError):
            await self.integration.get_courses()

    async def test_announcements_remain_explicitly_unsupported(self) -> None:
        with self.assertRaises(NotImplementedError):
            await self.integration.get_announcements()
        self.transport.post.assert_not_awaited()
