import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol
from urllib.parse import urlsplit

from models import Announcement, Assignment, Attendance, CalendarEvent, Campus, Course
from mycamu_config import MyCamuConfig
import mycamu_parsing as parse


class MyCamuConfigurationError(ValueError):
    pass


class MyCamuRequestError(RuntimeError):
    pass


@dataclass(frozen=True)
class MyCamuResponse:
    status: int
    body: object


class MyCamuTransport(Protocol):
    async def post(
        self, url: str, *, body: Mapping[str, object], headers: Mapping[str, str]
    ) -> MyCamuResponse: ...


AuthProvider = Callable[[], Awaitable[Mapping[str, str]]]


class MyCamuIntegration:
    """Offline-ready adapter. No default network transport or authentication flow."""

    def __init__(
        self, config: MyCamuConfig | None = None, *,
        transport: MyCamuTransport | None = None, auth_provider: AuthProvider | None = None,
    ) -> None:
        self.config = config if config is not None else MyCamuConfig()
        self.transport = transport
        self.auth_provider = auth_provider

    async def _request(self, path: str, extra: dict[str, object]) -> object:
        if self.auth_provider is None:
            raise MyCamuConfigurationError("MyCamu authentication provider is unavailable; supported authentication is unconfirmed.")
        if self.transport is None:
            raise MyCamuConfigurationError("MyCamu HTTP transport is not configured.")
        config = self.config
        if not isinstance(config.campus, Campus) or not config.context:
            raise MyCamuConfigurationError("Explicit campus and authorized student request context are required.")
        base = urlsplit(config.base_url)
        if base.scheme != "https" or not base.netloc or base.username or base.password or base.query or base.fragment or base.path not in ("", "/"):
            raise MyCamuConfigurationError("MyCamu base URL must be an HTTPS origin.")
        if not path.startswith("/api/") or any(char in path for char in ("?", "#", "\\")) or ".." in path:
            raise MyCamuConfigurationError("MyCamu endpoint must be a relative academic API path.")
        if config.timeout_seconds <= 0 or config.page_size <= 0 or config.max_pages <= 0:
            raise MyCamuConfigurationError("Timeout and pagination limits must be positive.")
        try:
            async with asyncio.timeout(config.timeout_seconds):
                headers = await self.auth_provider()
                if not isinstance(headers, Mapping) or not headers or any(
                    not isinstance(k, str) or not isinstance(v, str) or not v.strip()
                    or "\r" in k + v or "\n" in k + v for k, v in headers.items()
                ):
                    raise MyCamuConfigurationError("MyCamu credential/header configuration is unavailable or invalid.")
                if any(key.lower() == "cookie" for key in headers):
                    raise MyCamuConfigurationError("Browser cookie/session reuse is not supported.")
                response = await self.transport.post(
                    config.base_url.rstrip("/") + path,
                    body={**config.context, **extra}, headers=dict(headers),
                )
        except MyCamuConfigurationError:
            raise
        except TimeoutError:
            raise MyCamuRequestError("MyCamu request timed out.") from None
        except Exception:
            raise MyCamuRequestError("MyCamu transport/authentication provider failed.") from None
        if not isinstance(response, MyCamuResponse) or type(response.status) is not int:
            raise parse.MyCamuResponseError("Invalid HTTP response.")
        if not 200 <= response.status < 300:
            raise MyCamuRequestError(f"MyCamu HTTP request failed with status {response.status}.")
        envelope = response.body
        if isinstance(envelope, dict) and "output" in envelope:
            envelope = envelope["output"]
        if not isinstance(envelope, dict) or "data" not in envelope or envelope.get("errors"):
            raise parse.MyCamuResponseError("Malformed MyCamu envelope or provider-reported error.")
        return envelope["data"]

    async def get_courses(self) -> list[Course]:
        payload = await self._request(self.config.endpoints.courses, {})
        records = parse.unique(parse.courses(payload, self.config), lambda row: row.external_id)
        return sorted(records, key=lambda row: row.code)

    async def get_assignments(self) -> list[Assignment]:
        result = []
        expected = None
        seen_pages = set()
        for page in range(1, self.config.max_pages + 1):
            payload = await self._request(self.config.endpoints.assignments, {
                "getVerified": "yes", "getRange": "upcoming",
                "docsPerPage": self.config.page_size, "pageNo": page,
            })
            if not isinstance(payload, dict) or not isinstance(payload.get("pageData"), dict):
                raise parse.MyCamuResponseError("Assignment pagination metadata is missing.")
            count = payload["pageData"].get("itemCount")
            if type(count) is not int or count < 0 or expected is not None and count != expected:
                raise parse.MyCamuResponseError("Invalid or changing assignment item count.")
            expected = count
            batch = parse.assignments(payload.get("data"), self.config)
            signature = tuple(row.external_id for row in batch)
            if not batch and len(result) < expected or signature in seen_pages and batch:
                raise parse.MyCamuResponseError("Assignment pagination did not advance.")
            seen_pages.add(signature)
            result.extend(batch)
            result = parse.unique(result, lambda row: row.external_id)
            if len(result) >= expected:
                if len(result) != expected:
                    raise parse.MyCamuResponseError("Assignment count does not match pagination metadata.")
                return sorted(result, key=lambda row: (row.due_at, row.id))
        raise parse.MyCamuResponseError("Assignment pagination limit reached.")

    async def get_timetable(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        if start.utcoffset() is None or end.utcoffset() is None or end <= start:
            raise MyCamuConfigurationError("An ordered timezone-aware timetable interval is required.")
        if self.config.source_timezone is None:
            raise MyCamuConfigurationError("Confirmed source timezone is required for timetable query dates.")
        local_start, local_end = start.astimezone(self.config.source_timezone), end.astimezone(self.config.source_timezone)
        last_day = (local_end - timedelta(microseconds=1)).date()
        day = local_start.date()
        if (last_day - day).days >= 31:
            raise MyCamuConfigurationError("Timetable queries are limited to 31 local calendar days.")
        records = []
        # Public code proves selected-day requests, not a multi-day range contract.
        while day <= last_day:
            payload = await self._request(self.config.endpoints.timetable, {
                "start": day.isoformat(), "end": day.isoformat(),
                "schdlTyp": "slctdSchdl", "isShowCancelledPeriod": False, "isFromTt": True,
            })
            records.extend(parse.timetable(payload, self.config))
            day += timedelta(days=1)
        records = parse.unique(records, lambda row: row.id)
        return sorted((row for row in records if start <= row.start_at < end), key=lambda row: (row.start_at, row.id))

    async def get_holidays(self) -> list[CalendarEvent]:
        payload = await self._request(self.config.endpoints.holidays, {"getRange": "all", "isForMobile": True})
        return sorted(parse.unique(parse.holidays(payload, self.config), lambda row: row.id), key=lambda row: (row.start_at, row.id))

    async def get_calendar_events(self) -> list[CalendarEvent]:
        now = datetime.now(timezone.utc)
        records = await self.get_timetable(now, now + timedelta(days=7))
        records.extend(await self.get_holidays())
        return sorted(records, key=lambda row: (row.start_at, row.id))

    async def get_attendance(self) -> list[Attendance]:
        payload = await self._request(self.config.endpoints.attendance, {
            "isForWeb": True, "isFrAbLg": False, "isMinAttPer": True,
        })
        return parse.attendance(payload, self.config)

    async def get_announcements(self) -> list[Announcement]:
        raise NotImplementedError("MyCamu announcements are outside this adapter's verified schema coverage.")
