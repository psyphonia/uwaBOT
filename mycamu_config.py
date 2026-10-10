from dataclasses import dataclass, field
from datetime import tzinfo
from typing import Mapping

from models import Campus


@dataclass(frozen=True)
class MyCamuEndpoints:
    courses: str = "/api/subject/get-student-subject"
    timetable: str = "/api/Timetable/get"
    assignments: str = "/api/Assignment/getAssignment"
    holidays: str = "/api/HolidayDefinition/getUpcomingEvents"
    attendance: str = "/api/Attendance/getDtaForStupage"


@dataclass(frozen=True)
class MyCamuConfig:
    base_url: str = "https://student.india.uwa.edu.au"
    endpoints: MyCamuEndpoints = field(default_factory=MyCamuEndpoints)
    campus: Campus | None = None  # Required; missing campus must not mean both.
    context: Mapping[str, object] = field(default_factory=dict, repr=False)
    subject_codes: Mapping[str, str] = field(default_factory=dict)
    source_timezone: tzinfo | None = None
    timetable_course_field: str | None = None
    timetable_title_field: str | None = None
    timetable_location_field: str | None = None
    holiday_end_inclusive: bool | None = None
    timeout_seconds: float = 15
    page_size: int = 20
    max_pages: int = 100
