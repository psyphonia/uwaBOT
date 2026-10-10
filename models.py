from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum


INDIA_TZ = timezone(timedelta(hours=5, minutes=30), name="IST")


class Campus(str, Enum):
    Mumbai = "Mumbai"
    Chennai = "Chennai"


@dataclass(frozen=True)
class Course:
    code: str
    name: str
    campus: Campus | None  # None means both campuses.
    source: str = "mock"
    external_id: str | None = None
    source_url: str | None = None


@dataclass(frozen=True)
class Assignment:
    id: str
    course_code: str
    title: str
    due_at: datetime
    campus: Campus | None
    source: str = "mock"
    source_url: str | None = None
    external_id: str | None = None


@dataclass(frozen=True)
class CalendarEvent:
    id: str
    course_code: str | None
    title: str
    start_at: datetime
    end_at: datetime
    location: str
    campus: Campus | None
    source: str = "mock"
    external_id: str | None = None
    source_url: str | None = None
    all_day: bool = False


@dataclass(frozen=True)
class Announcement:
    id: str
    title: str
    body: str
    published_at: datetime
    campus: Campus | None
    course_code: str | None = None
    source: str = "mock"
    external_id: str | None = None
    source_url: str | None = None


@dataclass(frozen=True)
class Attendance:
    id: str
    campus: Campus
    total_count: int | None
    present_count: int | None
    percentage: float | None
    source: str = "mycamu"
    external_id: str | None = None


@dataclass(frozen=True)
class CourseContent:
    id: str
    course_code: str
    title: str
    description: str
    handler: str | None
    campus: Campus | None
    source: str
    external_id: str
    source_url: str | None = None
