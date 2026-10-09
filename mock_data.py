from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

from models import Announcement, Assignment, CalendarEvent, Campus, Course, INDIA_TZ


@dataclass(frozen=True)
class MockData:
    courses: tuple[Course, ...]
    assignments: tuple[Assignment, ...]
    events: tuple[CalendarEvent, ...]
    announcements: tuple[Announcement, ...] = ()


def build_mock_data(now: datetime) -> MockData:
    if now.utcoffset() is None:
        raise ValueError("Mock data requires a timezone-aware datetime.")
    today = now.astimezone(INDIA_TZ).date()

    def at(day: int, hour: int) -> datetime:
        return datetime.combine(today + timedelta(days=day), time(hour), INDIA_TZ).astimezone(timezone.utc)

    courses: list[Course] = []
    events: list[CalendarEvent] = []
    assignments: list[Assignment] = []
    announcements: list[Announcement] = []
    for campus in Campus:
        announcements.append(Announcement(
            id=f"{campus.value}-announcement-0", title="Sample study group notice",
            body=f"Fictional notice for {campus.value}: try the sample programming exercises.",
            published_at=now.astimezone(timezone.utc), campus=campus, course_code="CITS1401",
        ))
        courses.extend([
            Course("CITS1401", "Sample Programming Fundamentals", campus),
            Course("STAT1400", "Sample Statistics", campus),
            Course("BUSN1100", "Sample Business Foundations", campus),
        ])
        for index, (day, hour, code, title) in enumerate([
            (2, 11, "STAT1400", "Statistics workshop"),
            (0, 14, "BUSN1100", "Business seminar"),
            (6, 10, "CITS1401", "Programming lab"),
            (0, 9, "CITS1401", "Programming lecture"),
            (1, 10, "STAT1400", "Statistics lecture"),
            (8, 11, "BUSN1100", "Business workshop"),
        ]):
            start = at(day, hour)
            events.append(CalendarEvent(
                id=f"{campus.value}-event-{index}", course_code=code, title=title,
                start_at=start, end_at=start + timedelta(hours=1),
                location=f"Sample room {index + 1}, {campus.value}", campus=campus,
            ))
        for index, (day, code, title) in enumerate([
            (6, "BUSN1100", "Sample business case study"),
            (1, "CITS1401", "Sample programming exercise"),
            (3, "STAT1400", "Sample statistics worksheet"),
            (-1, "CITS1401", "Sample past exercise"),
        ]):
            assignments.append(Assignment(
                id=f"{campus.value}-assignment-{index}", course_code=code,
                title=title, due_at=at(day, 18), campus=campus,
            ))
    return MockData(tuple(courses), tuple(assignments), tuple(events), tuple(announcements))
