import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import base64
from urllib.parse import parse_qs, urlsplit
import unittest

from blackboard import BlackboardConfig, BlackboardIntegration
from blackboard_auth import BlackboardError, BlackboardOAuth, OAuthConfig, Response, Token
from models import Campus


NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)
BASE = "https://learn.example.test"


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def request(self, method, url, *, headers, body=None):
        self.calls.append((method, url, headers, body))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def listed(*rows, next_page=None):
    return Response(200, {"results": list(rows), "paging": {} if next_page is None else {"nextPage": next_page}})


def course_responses():
    return [listed({"courseId": "_1_1"}, {"courseId": "_1_1"}),
            Response(200, {"id": "_1_1", "courseId": "CITS1401", "name": "Python", "organization": False})]


def token_response(**updates):
    return Response(200, {"access_token": "synthetic-access", "token_type": "bearer", "expires_in": 3600,
                          "user_id": "synthetic-user", "scope": "read offline", **updates})


class BlackboardTests(unittest.IsolatedAsyncioTestCase):
    def adapter(self, responses, *, offline=False, campuses=None):
        transport = FakeTransport(responses)
        auth = BlackboardOAuth(OAuthConfig(BASE, "synthetic-client", "synthetic-secret",
                                           "https://bot.example.test/callback", offline), transport)
        auth.token = Token("synthetic-access", datetime.now(timezone.utc) + timedelta(hours=1), "synthetic-user",
                           "synthetic-refresh" if offline else None)
        adapter = BlackboardIntegration(BlackboardConfig(BASE, campuses if campuses is not None else {"_1_1": Campus.Mumbai}),
                                       transport=transport, auth=auth)
        return adapter, transport, auth

    async def test_pkce_state_scope_and_exchange(self):
        adapter, transport, auth = self.adapter([token_response()])
        request = auth.begin()
        params = parse_qs(urlsplit(request.url).query)
        self.assertEqual(params["scope"], ["read"])
        self.assertEqual(params["code_challenge_method"], ["S256"])
        challenge = base64.urlsafe_b64encode(hashlib.sha256(request.verifier.encode()).digest()).decode().rstrip("=")
        self.assertEqual(params["code_challenge"], [challenge])
        with self.assertRaises(BlackboardError):
            await auth.complete(code="synthetic-code", state="wrong")
        self.assertEqual(transport.calls, [])
        token = await auth.complete(code="synthetic-code", state=request.state)
        self.assertIsNone(token.refresh_token)
        self.assertNotIn(token.access_token, repr(token))
        self.assertEqual(transport.calls[0][3]["code_verifier"], request.verifier)
        with self.assertRaises(BlackboardError):
            await auth.complete(code="synthetic-code", state=request.state)

    async def test_refresh_opt_in_expiry_and_identity(self):
        adapter, transport, auth = self.adapter([token_response()], offline=True)
        self.assertEqual(parse_qs(urlsplit(auth.begin().url).query)["scope"], ["read offline"])
        auth.token = Token("old", NOW, "synthetic-user", "synthetic-refresh")
        token = await auth.valid_token()
        self.assertEqual(token.refresh_token, "synthetic-refresh")
        self.assertEqual(transport.calls[0][3]["grant_type"], "refresh_token")
        auth.token = Token("old", NOW, "synthetic-user", "synthetic-refresh")
        transport.responses = [token_response(user_id="other-student")]
        with self.assertRaisesRegex(BlackboardError, "student"):
            await auth.valid_token()
        auth.config = OAuthConfig(BASE, "client", "secret", "https://bot.example.test/callback")
        with self.assertRaisesRegex(BlackboardError, "reauthorization"):
            await auth.valid_token()

    async def test_courses_memberships_and_campus(self):
        adapter, transport, _ = self.adapter(course_responses())
        courses = await adapter.get_courses()
        self.assertEqual(len(courses), 1)
        self.assertEqual((courses[0].code, courses[0].campus, courses[0].source), ("CITS1401", Campus.Mumbai, "blackboard"))
        self.assertIn("/users/synthetic-user/courses", transport.calls[0][1])
        adapter, _, _ = self.adapter(course_responses(), campuses={})
        with self.assertRaisesRegex(BlackboardError, "campus"):
            await adapter.get_courses()

    async def test_assignments_sorted_deduplicated_missing_due(self):
        row = {"id": "col1", "name": "Assignment", "grading": {"due": "2026-10-11T00:30:00+05:30"}}
        earlier = {"id": "col2", "name": "Earlier", "grading": {"due": "2026-10-10T12:00:00Z"}}
        adapter, _, _ = self.adapter(course_responses() + [listed(row, row, earlier, {"id": "undated", "grading": {}})])
        items = await adapter.get_assignments()
        self.assertEqual([item.title for item in items], ["Earlier", "Assignment"])
        self.assertEqual(items[1].due_at, datetime(2026, 10, 10, 19, tzinfo=timezone.utc))
        self.assertTrue(all(item.source == "blackboard" for item in items))

    async def test_announcements(self):
        adapter, _, _ = self.adapter(course_responses() + [listed({"id": "a1", "title": "Notice", "body": "Sample",
                                                                   "created": "2026-10-10T00:00:00Z"})])
        self.assertEqual((await adapter.get_announcements())[0].course_code, "CITS1401")

    async def test_content_children(self):
        adapter, transport, _ = self.adapter(course_responses() + [listed({"id": "folder", "title": "Week 1",
           "hasChildren": True}), listed({"id": "page", "title": "Reading", "description": "Sample",
           "contentHandler": {"id": "resource/x-bb-document"}})])
        items = await adapter.get_content()
        self.assertEqual([item.title for item in items], ["Week 1", "Reading"])
        self.assertTrue(transport.calls[-1][1].endswith("/folder/children"))

    async def test_calendar_instant_deadline_and_boundaries(self):
        adapter, transport, _ = self.adapter(course_responses() + [listed({"id": "calendar1", "title": "Due",
            "calendarId": "_1_1", "start": "2026-10-10T12:00:00Z", "end": "2026-10-10T12:00:00Z"})])
        item = (await adapter.get_calendar_events(NOW, NOW + timedelta(days=7)))[0]
        self.assertEqual(item.start_at, item.end_at)
        self.assertIn("courseId=_1_1", transport.calls[-1][1])
        with self.assertRaises(BlackboardError):
            await adapter.get_calendar_events(NOW.replace(tzinfo=None), NOW)

    async def test_pagination_and_unsafe_next_page(self):
        path = "/learn/api/public/v1/announcements"
        adapter, _, _ = self.adapter([listed({"id": "1"}, next_page=path + "?offset=1"), listed({"id": "2"})])
        self.assertEqual(len(await adapter._list(path)), 2)
        for next_page in ["https://evil.example/learn/api/public/v1/announcements", "//evil.example/path", path]:
            adapter, transport, _ = self.adapter([listed(next_page=next_page)])
            with self.assertRaises(BlackboardError):
                await adapter._list(path)
            self.assertEqual(len(transport.calls), 1)

    async def test_status_handling_and_401_refresh(self):
        path = "/learn/api/public/v1/announcements"
        for status in [401, 403, 429, 500]:
            adapter, _, _ = self.adapter([Response(status, {"secret": "never expose"})])
            with self.assertRaises(BlackboardError) as error:
                await adapter._get(path)
            self.assertNotIn("never expose", str(error.exception))
        adapter, transport, _ = self.adapter([Response(401, {}), token_response(), listed()], offline=True)
        self.assertEqual(await adapter._list(path), [])
        self.assertEqual([call[0] for call in transport.calls], ["GET", "POST", "GET"])

    async def test_malformed_responses_and_duplicates(self):
        for payload in [None, {}, {"results": [None]}, {"results": [], "paging": []}]:
            adapter, _, _ = self.adapter([Response(200, payload)])
            with self.assertRaises(BlackboardError):
                await adapter._list("/learn/api/public/v1/announcements")
        row = {"id": "1", "name": "One", "grading": {"due": "2026-10-10T12:00:00Z"}}
        adapter, _, _ = self.adapter(course_responses() + [listed(row, {**row, "name": "Conflict"})])
        with self.assertRaisesRegex(BlackboardError, "duplicate"):
            await adapter.get_assignments()
        adapter, _, _ = self.adapter(course_responses() + [listed({**row, "grading": {"due": "2026-10-10"}})])
        with self.assertRaisesRegex(BlackboardError, "timestamp"):
            await adapter.get_assignments()

    async def test_timeout_sanitized_and_limits(self):
        adapter, _, _ = self.adapter([TimeoutError("secret-token")])
        with self.assertRaisesRegex(BlackboardError, "timed out") as error:
            await adapter._get("/learn/api/public/v1/announcements")
        self.assertNotIn("secret-token", str(error.exception))
        adapter, _, _ = self.adapter([listed(next_page="/learn/api/public/v1/announcements?offset=1")])
        adapter.config = BlackboardConfig(BASE, max_pages=1)
        with self.assertRaisesRegex(BlackboardError, "limit"):
            await adapter._list("/learn/api/public/v1/announcements")

    async def test_missing_config_token_and_bad_token_response(self):
        with self.assertRaises(BlackboardError):
            await BlackboardIntegration().get_courses()
        adapter, _, auth = self.adapter([token_response(expires_in="bad")])
        auth.token = None
        with self.assertRaises(BlackboardError):
            await auth.valid_token()
        request = auth.begin()
        with self.assertRaisesRegex(BlackboardError, "Malformed"):
            await auth.complete(code="sample-code", state=request.state)

    async def test_token_shape_scope_header_injection_rejected(self):
        for updates in [{"scope": []}, {"token_type": None}, {"scope": "read write"},
                        {"access_token": "invalid\r\nheader"}]:
            _, _, auth = self.adapter([token_response(**updates)])
            request = auth.begin()
            with self.assertRaises(BlackboardError):
                await auth.complete(code="sample-code", state=request.state)

    async def test_explicit_shared_campus_and_foreign_calendar_rejected(self):
        adapter, _, _ = self.adapter(course_responses(), campuses={"_1_1": None})
        self.assertIsNone((await adapter.get_courses())[0].campus)
        adapter, _, _ = self.adapter(course_responses() + [listed({"id": "foreign", "calendarId": "other"})])
        with self.assertRaisesRegex(BlackboardError, "outside"):
            await adapter.get_calendar_events()
