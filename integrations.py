from datetime import datetime, timezone
from typing import Protocol

from mock_data import MockData, build_mock_data
from models import Announcement, Assignment, CalendarEvent, Course
from mycamu import MyCamuIntegration
from blackboard import BlackboardIntegration


class UniversityIntegration(Protocol):
    """Read-only sources return normalized internal models, not provider payloads."""

    async def get_courses(self) -> list[Course]: ...

    async def get_assignments(self) -> list[Assignment]: ...

    async def get_calendar_events(self) -> list[CalendarEvent]: ...

    async def get_announcements(self) -> list[Announcement]: ...


class MockIntegration:
    def __init__(self, data: MockData | None = None) -> None:
        self._data = data if data is not None else build_mock_data(datetime.now(timezone.utc))

    async def get_courses(self) -> list[Course]:
        return list(self._data.courses)

    async def get_assignments(self) -> list[Assignment]:
        return list(self._data.assignments)

    async def get_calendar_events(self) -> list[CalendarEvent]:
        return list(self._data.events)

    async def get_announcements(self) -> list[Announcement]:
        return list(self._data.announcements)
