import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode, urlsplit

from blackboard_auth import BlackboardError, BlackboardOAuth, Response, Transport, origin
import blackboard_parsing as parse
from models import Announcement, Assignment, CalendarEvent, Campus, Course, CourseContent


@dataclass(frozen=True)
class BlackboardConfig:
    base_url: str
    campuses: dict[str, Campus | None] = field(default_factory=dict)  # Explicit None means both.
    timeout: float = 15
    max_pages: int = 100
    max_content_items: int = 1000


class BlackboardIntegration:
    def __init__(self, config: BlackboardConfig | None = None, *, transport: Transport | None = None,
                 auth: BlackboardOAuth | None = None):
        self.config, self.transport, self.auth = config, transport, auth

    async def _get(self, path: str) -> object:
        config = self.config
        if config is None or self.transport is None or self.auth is None:
            raise BlackboardError("Blackboard authorized API configuration is unavailable.")
        base = origin(config.base_url)
        if (base != origin(self.auth.config.base_url) or config.timeout <= 0 or config.max_pages <= 0
                or config.max_content_items <= 0):
            raise BlackboardError("Invalid Blackboard configuration or OAuth tenant mismatch.")
        url = base + path if path.startswith("/") else path
        parsed = urlsplit(url)
        if (parsed.scheme + "://" + parsed.netloc != base or parsed.username or parsed.password
                or not parsed.path.startswith("/learn/api/public/") or parsed.fragment
                or ".." in parsed.path or "\\" in url):
            raise BlackboardError("Unsafe Blackboard API/pagination URL.")
        for attempt in range(2):
            token = await self.auth.valid_token(force_refresh=bool(attempt))
            try:
                async with asyncio.timeout(config.timeout):
                    response = await self.transport.request("GET", url,
                        headers={"Authorization": "Bearer " + token.access_token, "Accept": "application/json"})
            except Exception:
                raise BlackboardError("Blackboard API request failed or timed out.") from None
            if not isinstance(response, Response):
                raise BlackboardError("Malformed Blackboard HTTP response.")
            if response.status == 401 and attempt == 0:
                continue
            if response.status == 429:
                raise BlackboardError("Blackboard rate limit reached; retry on a later sync.")
            if response.status == 403:
                raise BlackboardError("Blackboard access denied; verify student permissions.")
            if response.status != 200:
                raise BlackboardError(f"Blackboard HTTP status {response.status}.")
            return response.body
        raise BlackboardError("Blackboard authorization rejected; reauthorization is required.")

    async def _list(self, path: str) -> list[dict]:
        rows, visited = [], set()
        for _ in range(self.config.max_pages if self.config else 1):
            if path in visited:
                raise BlackboardError("Blackboard pagination cycle.")
            visited.add(path)
            body = await self._get(path)
            if not isinstance(body, dict) or not isinstance(body.get("results"), list):
                raise BlackboardError("Malformed Blackboard list response.")
            if any(not isinstance(row, dict) for row in body["results"]):
                raise BlackboardError("Malformed Blackboard record.")
            rows.extend(body["results"])
            paging = body.get("paging", {})
            if not isinstance(paging, dict):
                raise BlackboardError("Malformed Blackboard paging.")
            path = paging.get("nextPage")
            if path is None:
                return rows
            if not isinstance(path, str) or not path:
                raise BlackboardError("Invalid Blackboard next page.")
        raise BlackboardError("Blackboard pagination limit exceeded.")

    async def get_courses(self) -> list[Course]:
        if self.auth is None:
            raise BlackboardError("Blackboard authorized API configuration is unavailable.")
        token = await self.auth.valid_token()
        memberships = await self._list("/learn/api/public/v1/users/" + quote(token.user_id, safe="") + "/courses")
        courses = []
        for course_id in sorted({parse.text(row, "courseId") for row in memberships}):
            row = await self._get("/learn/api/public/v3/courses/" + quote(course_id, safe=""))
            if not isinstance(row, dict):
                raise BlackboardError("Malformed Blackboard course.")
            if row.get("organization") is True:
                continue
            if row.get("id") != course_id:
                raise BlackboardError("Course identity mismatch.")
            campus = self.config.campuses.get(course_id)
            if course_id not in self.config.campuses or campus is not None and not isinstance(campus, Campus):
                raise BlackboardError("Confirmed course campus mapping is required.")
            courses.append(parse.course(row, campus, origin(self.config.base_url) +
                "/ultra/courses/" + quote(course_id, safe="") + "/outline"))
        return sorted(parse.unique(courses), key=lambda row: row.code)

    async def get_assignments(self) -> list[Assignment]:
        result = []
        for course in await self.get_courses():
            for row in await self._list("/learn/api/public/v2/courses/" + quote(course.external_id, safe="") + "/gradebook/columns"):
                item = parse.assignment(row, course)
                if item is not None:
                    result.append(item)
        return sorted(parse.unique(result), key=lambda row: (row.due_at, row.id))

    async def get_announcements(self) -> list[Announcement]:
        result = []
        for course in await self.get_courses():
            rows = await self._list("/learn/api/public/v1/courses/" + quote(course.external_id, safe="") + "/announcements")
            result.extend(parse.announcement(row, course) for row in rows)
        return sorted(parse.unique(result), key=lambda row: (row.published_at, row.id))

    async def get_content(self) -> list[CourseContent]:
        result = []
        for course in await self.get_courses():
            root = "/learn/api/public/v1/courses/" + quote(course.external_id, safe="") + "/contents"
            pending, seen = [root], set()
            while pending:
                for row in await self._list(pending.pop()):
                    item = parse.content(row, course)
                    result.append(item)
                    if len(result) > self.config.max_content_items:
                        raise BlackboardError("Blackboard content traversal limit exceeded.")
                    if item.external_id not in seen and row.get("hasChildren") is True:
                        pending.append(root + "/" + quote(parse.text(row, "id"), safe="") + "/children")
                    seen.add(item.external_id)
        return parse.unique(result)

    async def get_calendar_events(self, start: datetime | None = None, end: datetime | None = None) -> list[CalendarEvent]:
        start = start if start is not None else datetime.now(timezone.utc)
        end = end if end is not None else start + timedelta(days=7)
        if start.utcoffset() is None or end.utcoffset() is None or not timedelta(0) < end-start <= timedelta(days=28):
            raise BlackboardError("Calendar needs an aware interval of at most 28 days.")
        result = []
        for course in await self.get_courses():
            query = urlencode(dict(courseId=course.external_id, since=start.astimezone(timezone.utc).isoformat(),
                                   until=end.astimezone(timezone.utc).isoformat()))
            rows = await self._list("/learn/api/public/v1/calendars/items?" + query)
            if any(row.get("calendarId") != course.external_id for row in rows):
                raise BlackboardError("Calendar response is outside the requested course.")
            result.extend(parse.event(row, course) for row in rows)
        return sorted(parse.unique(result), key=lambda row: (row.start_at, row.id))
