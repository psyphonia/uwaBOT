from datetime import date, datetime, time, timedelta, timezone
from typing import TypeVar

from models import Assignment, Attendance, CalendarEvent, Course
from mycamu_config import MyCamuConfig


class MyCamuResponseError(ValueError):
    pass


def required(row: dict, field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise MyCamuResponseError(f"Missing or invalid field: {field}")
    return value.strip()


def rows(value: object) -> list[dict]:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise MyCamuResponseError("Expected a list of objects.")
    return value


def instant(value: str, config: MyCamuConfig) -> datetime:
    if len(value) <= 10:
        raise MyCamuResponseError("Exact timestamp required; date-only value is insufficient.")
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            if config.source_timezone is None:
                raise MyCamuResponseError("Source timezone is required for local timestamps.")
            parsed = parsed.replace(tzinfo=config.source_timezone)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        raise MyCamuResponseError("Invalid timestamp or missing timezone configuration.") from None


def code(row: dict, field: str, config: MyCamuConfig) -> str:
    subject = required(row, field)
    mapped = config.subject_codes.get(subject)
    if not isinstance(mapped, str) or not mapped.strip():
        raise MyCamuResponseError("A confirmed subject-to-course-code mapping is required.")
    return mapped.strip().upper()


def courses(payload: object, config: MyCamuConfig) -> list[Course]:
    return [Course(code=code(row, "SubId", config), name=required(row, "SubNa"),
                   campus=config.campus, source="mycamu", external_id=required(row, "SubId"))
            for row in rows(payload)]


def assignments(payload: object, config: MyCamuConfig) -> list[Assignment]:
    result = []
    for row in rows(payload):
        raw_due = required(row, "assgnDueDt")
        end_time = row.get("endTme")
        if end_time:
            if config.source_timezone is None:
                raise MyCamuResponseError("Source timezone required for assignment endTme.")
            try:
                local_day = date.fromisoformat(raw_due[:10])
                clock = time.fromisoformat(end_time)
                if clock.tzinfo is not None:
                    raise ValueError
                due = datetime.combine(local_day, clock, config.source_timezone)
            except (ValueError, TypeError):
                raise MyCamuResponseError("Invalid assignment date/time.") from None
            due = due.astimezone(timezone.utc)
        else:
            due = instant(raw_due, config)
        external = required(row, "CmAssID")
        result.append(Assignment(id=f"mycamu:assignment:{external}", external_id=external,
                                 course_code=code(row, "SubId", config), title=required(row, "Title"),
                                 due_at=due, campus=config.campus, source="mycamu"))
    return result


def timetable(payload: object, config: MyCamuConfig) -> list[CalendarEvent]:
    if not config.timetable_course_field or not config.timetable_title_field:
        raise MyCamuResponseError("Confirmed timetable course/title field mappings are required.")
    result = []
    for group in rows(payload):
        for row in rows(group.get("Periods", [])):
            external = required(row, "_id")
            start, end = instant(required(row, "start"), config), instant(required(row, "end"), config)
            if end <= start:
                raise MyCamuResponseError("Event end must follow its start.")
            location = row.get(config.timetable_location_field, "") if config.timetable_location_field else ""
            if not isinstance(location, str):
                raise MyCamuResponseError("Invalid timetable location.")
            result.append(CalendarEvent(
                id=f"mycamu:timetable:{external}", external_id=external,
                course_code=code(row, config.timetable_course_field, config),
                title=required(row, config.timetable_title_field), start_at=start, end_at=end,
                location=location, campus=config.campus, source="mycamu",
            ))
    return result


def holidays(payload: object, config: MyCamuConfig) -> list[CalendarEvent]:
    result = []
    for row in rows(payload):
        external = required(row, "_id")
        raw_start, raw_end = required(row, "HldDtFrom"), required(row, "HldDtTo")
        all_day = len(raw_start) == 10 and len(raw_end) == 10
        if all_day:
            if config.source_timezone is None or config.holiday_end_inclusive is None:
                raise MyCamuResponseError("Date-only holidays require timezone and end-date semantics.")
            try:
                start_day, end_day = date.fromisoformat(raw_start), date.fromisoformat(raw_end)
                if config.holiday_end_inclusive:
                    end_day += timedelta(days=1)
                start = datetime.combine(start_day, time.min, config.source_timezone).astimezone(timezone.utc)
                end = datetime.combine(end_day, time.min, config.source_timezone).astimezone(timezone.utc)
            except ValueError:
                raise MyCamuResponseError("Invalid holiday dates.") from None
        else:
            start, end = instant(raw_start, config), instant(raw_end, config)
        if end <= start:
            raise MyCamuResponseError("Holiday end must follow its start.")
        result.append(CalendarEvent(id=f"mycamu:holiday:{external}", external_id=external,
                                    course_code=None, title=required(row, "Name"), start_at=start,
                                    end_at=end, location="", campus=config.campus, source="mycamu",
                                    all_day=all_day))
    return result


def attendance(payload: object, config: MyCamuConfig) -> list[Attendance]:
    if not isinstance(payload, dict):
        raise MyCamuResponseError("Expected attendance summary object.")
    counts = [payload.get(field) for field in ("OvrAllCnt", "OvrAllPCnt")]
    if any(value is not None and (type(value) is not int or value < 0) for value in counts):
        raise MyCamuResponseError("Invalid attendance counts.")
    total, present = counts
    percentage = payload.get("OvrAllPrcntg")
    if percentage is not None and (type(percentage) not in (int, float) or not 0 <= percentage <= 100):
        raise MyCamuResponseError("Invalid attendance percentage.")
    if total is not None and present is not None and present > total:
        raise MyCamuResponseError("Present count exceeds total count.")
    if all(value is None for value in (total, present, percentage)):
        raise MyCamuResponseError("Attendance summary fields are missing.")
    return [Attendance(id=f"mycamu:attendance:{config.campus.value}", campus=config.campus,
                       total_count=total, present_count=present, percentage=percentage)]


T = TypeVar("T")


def unique(records: list[T], key) -> list[T]:
    found = {}
    for record in records:
        identity = key(record)
        if identity in found and found[identity] != record:
            raise MyCamuResponseError("Conflicting duplicate source records.")
        found[identity] = record
    return list(found.values())
