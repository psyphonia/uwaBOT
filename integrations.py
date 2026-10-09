from datetime import datetime, timezone
from typing import Protocol

from mock_data import MockData, build_mock_data
from models import Announcement, Assignment, CalendarEvent, Course


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


class MyCamuIntegration:
    async def get_courses(self) -> list[Course]:
        raise NotImplementedError("MyCamu courses: authorized access must be investigated before implementation.")

    async def get_assignments(self) -> list[Assignment]:
        raise NotImplementedError("MyCamu assignments: authorized access must be investigated before implementation.")

    async def get_calendar_events(self) -> list[CalendarEvent]:
        raise NotImplementedError("MyCamu calendar: authorized access must be investigated before implementation.")

    async def get_announcements(self) -> list[Announcement]:
        raise NotImplementedError("MyCamu announcements: authorized access must be investigated before implementation.")


class BlackboardIntegration:
    async def get_courses(self) -> list[Course]:
        raise NotImplementedError("Blackboard courses: authorized API access must be confirmed before implementation.")

    async def get_assignments(self) -> list[Assignment]:
        raise NotImplementedError("Blackboard assignments: authorized API access must be confirmed before implementation.")

    async def get_calendar_events(self) -> list[CalendarEvent]:
        raise NotImplementedError("Blackboard calendar: authorized API access must be confirmed before implementation.")

    async def get_announcements(self) -> list[Announcement]:
        raise NotImplementedError("Blackboard announcements: authorized API access must be confirmed before implementation.")
