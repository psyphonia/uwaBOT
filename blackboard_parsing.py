"""Normalize student-visible Learn metadata; never infer missing dates/campuses."""
from datetime import datetime, timezone
from models import Assignment, Announcement, CalendarEvent, Campus, Course, CourseContent
from blackboard_auth import BlackboardError


def text(row: dict, key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BlackboardError(f"Missing Blackboard field: {key}.")
    return value


def instant(value: object) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.utcoffset() is None:
            raise ValueError
        return result.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError):
        raise BlackboardError("Invalid or timezone-naive Blackboard timestamp.") from None


def unique(rows: list) -> list:
    found = {}
    for row in rows:
        key = row.external_id
        if key in found and found[key] != row:
            raise BlackboardError("Conflicting Blackboard duplicate records.")
        found[key] = row
    return list(found.values())


def course(row: dict, campus: Campus | None, url: str) -> Course:
    return Course(text(row, "courseId"), text(row, "name"), campus, "blackboard", text(row, "id"), url)


def content(row: dict, course: Course) -> CourseContent:
    handler = row.get("contentHandler", {})
    if not isinstance(handler, dict):
        raise BlackboardError("Invalid content handler.")
    if (not isinstance(row.get("description", ""), str)
            or handler.get("id") is not None and not isinstance(handler["id"], str)):
        raise BlackboardError("Invalid content metadata.")
    return CourseContent("blackboard:content:" + course.external_id + ":" + text(row, "id"),
        course.code, text(row, "title"), row.get("description", ""), handler.get("id"),
        course.campus, "blackboard", course.external_id + ":" + text(row, "id"), course.source_url)


def assignment(row: dict, course: Course) -> Assignment | None:
    grading = row.get("grading", {})
    if not isinstance(grading, dict):
        raise BlackboardError("Invalid grading metadata.")
    if grading.get("due") is None:
        return None  # Existing Assignment requires a deadline; content preserves undated items.
    external = course.external_id + ":" + text(row, "id")
    return Assignment("blackboard:assignment:" + external, course.code, text(row, "name"),
        instant(grading["due"]), course.campus, "blackboard", course.source_url, external)


def announcement(row: dict, course: Course) -> Announcement:
    external = course.external_id + ":" + text(row, "id")
    return Announcement("blackboard:announcement:" + external, text(row, "title"), text(row, "body"),
        instant(row.get("created")), course.campus, course.code, "blackboard", external, course.source_url)


def event(row: dict, course: Course) -> CalendarEvent:
    start, end = instant(row.get("start")), instant(row.get("end"))
    if end < start:
        raise BlackboardError("Calendar end precedes start.")
    external = course.external_id + ":" + text(row, "id")
    return CalendarEvent("blackboard:event:" + external, course.code, text(row, "title"), start, end,
        "", course.campus, "blackboard", external, course.source_url)
