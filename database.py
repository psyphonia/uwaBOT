from contextlib import contextmanager
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
import sqlite3
from typing import Iterator, TYPE_CHECKING

from models import Assignment, CalendarEvent, Campus, Course, INDIA_TZ

if TYPE_CHECKING:
    from mock_data import MockData


def utc_text(value: datetime) -> str:
    if value.utcoffset() is None:
        raise ValueError("Database dates must be timezone-aware.")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def campus_value(campus: Campus | None) -> str | None:
    return campus.value if campus is not None else None


def read_campus(value: str | None) -> Campus | None:
    return Campus(value) if value is not None else None


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=2)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS campuses (
                    name TEXT PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS courses (
                    id INTEGER PRIMARY KEY,
                    code TEXT NOT NULL,
                    name TEXT NOT NULL,
                    campus TEXT REFERENCES campuses(name),
                    source TEXT NOT NULL,
                    external_id TEXT,
                    source_url TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS course_identity
                    ON courses(code, COALESCE(campus, ''));
                CREATE TABLE IF NOT EXISTS assignments (
                    id TEXT PRIMARY KEY,
                    course_id INTEGER NOT NULL REFERENCES courses(id),
                    title TEXT NOT NULL,
                    due_at TEXT NOT NULL,
                    campus TEXT REFERENCES campuses(name),
                    source TEXT NOT NULL,
                    external_id TEXT,
                    source_url TEXT
                );
                CREATE TABLE IF NOT EXISTS calendar_events (
                    id TEXT PRIMARY KEY,
                    course_id INTEGER NOT NULL REFERENCES courses(id),
                    title TEXT NOT NULL,
                    start_at TEXT NOT NULL,
                    end_at TEXT NOT NULL CHECK(end_at > start_at),
                    location TEXT NOT NULL,
                    campus TEXT REFERENCES campuses(name),
                    source TEXT NOT NULL,
                    external_id TEXT,
                    source_url TEXT
                );
                CREATE INDEX IF NOT EXISTS assignment_due ON assignments(due_at);
                CREATE INDEX IF NOT EXISTS event_start ON calendar_events(start_at);
                CREATE TABLE IF NOT EXISTS sent_reminders (
                    assignment_id TEXT NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
                    due_at TEXT NOT NULL,
                    reminder_threshold INTEGER NOT NULL CHECK(reminder_threshold IN (168, 24, 3)),
                    claimed_at TEXT NOT NULL,
                    sent_at TEXT,
                    PRIMARY KEY (assignment_id, due_at, reminder_threshold)
                );
            """)
            connection.executemany(
                "INSERT INTO campuses(name) VALUES (?) ON CONFLICT DO NOTHING",
                [(campus.value,) for campus in Campus],
            )

    def seed(self, data: "MockData") -> None:
        """Insert sample records atomically without changing existing records or dates."""
        with self.connect() as connection:
            connection.executemany(
                """INSERT INTO courses(code, name, campus, source, external_id, source_url)
                   VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING""",
                [(item.code, item.name, campus_value(item.campus), item.source,
                  item.external_id, item.source_url) for item in data.courses],
            )

            def course_id(code: str, campus: Campus | None) -> int:
                row = connection.execute(
                    """SELECT id FROM courses WHERE code = ? AND (campus IS ? OR campus IS NULL)
                       ORDER BY campus IS NULL LIMIT 1""", (code, campus_value(campus)),
                ).fetchone()
                if row is None:
                    raise ValueError(f"No matching course for sample record: {code}")
                return row["id"]

            connection.executemany(
                """INSERT INTO assignments
                   (id, course_id, title, due_at, campus, source, external_id, source_url)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO NOTHING""",
                [(item.id, course_id(item.course_code, item.campus), item.title,
                  utc_text(item.due_at), campus_value(item.campus), item.source,
                  item.external_id, item.source_url) for item in data.assignments],
            )
            connection.executemany(
                """INSERT INTO calendar_events
                   (id, course_id, title, start_at, end_at, location, campus, source, external_id, source_url)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO NOTHING""",
                [(item.id, course_id(item.course_code, item.campus), item.title,
                  utc_text(item.start_at), utc_text(item.end_at), item.location,
                  campus_value(item.campus), item.source, item.external_id, item.source_url)
                 for item in data.events],
            )

    def get_course(self, course_code: str, campus: Campus | None = None) -> list[Course]:
        selected = campus_value(campus)
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM courses WHERE code = ?
                   AND (? IS NULL OR campus = ? OR campus IS NULL)
                   ORDER BY campus, id""", (course_code.strip().upper(), selected, selected),
            ).fetchall()
        return [Course(code=row["code"], name=row["name"], campus=read_campus(row["campus"]),
                       source=row["source"], external_id=row["external_id"], source_url=row["source_url"])
                for row in rows]

    def claim_reminder(self, assignment: Assignment, threshold: int, now: datetime) -> bool:
        with self.connect() as connection:
            result = connection.execute(
                """INSERT INTO sent_reminders
                   (assignment_id, due_at, reminder_threshold, claimed_at)
                   VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING""",
                (assignment.id, utc_text(assignment.due_at), threshold, utc_text(now)),
            )
            return result.rowcount == 1

    def mark_reminder_sent(self, assignment: Assignment, threshold: int, now: datetime) -> None:
        with self.connect() as connection:
            connection.execute(
                """UPDATE sent_reminders SET sent_at = ?
                   WHERE assignment_id = ? AND due_at = ? AND reminder_threshold = ?""",
                (utc_text(now), assignment.id, utc_text(assignment.due_at), threshold),
            )

    def release_reminder(self, assignment: Assignment, threshold: int) -> None:
        with self.connect() as connection:
            connection.execute(
                """DELETE FROM sent_reminders WHERE assignment_id = ? AND due_at = ?
                   AND reminder_threshold = ? AND sent_at IS NULL""",
                (assignment.id, utc_text(assignment.due_at), threshold),
            )

    def pending_reminder_count(self) -> int:
        with self.connect() as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM sent_reminders WHERE sent_at IS NULL"
            ).fetchone()[0]

    def get_upcoming_assignments(
        self, now: datetime, campus: Campus | None = None
    ) -> list[Assignment]:
        selected = campus_value(campus)
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT a.*, c.code AS course_code FROM assignments a
                   JOIN courses c ON c.id = a.course_id
                   WHERE a.due_at >= ? AND (? IS NULL OR a.campus = ? OR a.campus IS NULL)
                   ORDER BY a.due_at, a.id""", (utc_text(now), selected, selected),
            ).fetchall()
        return [Assignment(
            id=row["id"], course_code=row["course_code"], title=row["title"],
            due_at=datetime.fromisoformat(row["due_at"]), campus=read_campus(row["campus"]),
            source=row["source"], external_id=row["external_id"], source_url=row["source_url"],
        ) for row in rows]

    def get_events_between(
        self, start: datetime, end: datetime, campus: Campus | None = None
    ) -> list[CalendarEvent]:
        selected = campus_value(campus)
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT e.*, c.code AS course_code FROM calendar_events e
                   JOIN courses c ON c.id = e.course_id
                   WHERE e.start_at >= ? AND e.start_at < ?
                   AND (? IS NULL OR e.campus = ? OR e.campus IS NULL)
                   ORDER BY e.start_at, e.id""", (utc_text(start), utc_text(end), selected, selected),
            ).fetchall()
        return [CalendarEvent(
            id=row["id"], course_code=row["course_code"], title=row["title"],
            start_at=datetime.fromisoformat(row["start_at"]),
            end_at=datetime.fromisoformat(row["end_at"]), location=row["location"],
            campus=read_campus(row["campus"]), source=row["source"],
            external_id=row["external_id"], source_url=row["source_url"],
        ) for row in rows]

    def get_events_for_day(self, now: datetime, campus: Campus | None = None) -> list[CalendarEvent]:
        utc_text(now)
        start = datetime.combine(now.astimezone(INDIA_TZ).date(), time.min, INDIA_TZ)
        return self.get_events_between(start, start + timedelta(days=1), campus)

    def get_events_for_week(self, now: datetime, campus: Campus | None = None) -> list[CalendarEvent]:
        utc_text(now)
        start = now.astimezone(timezone.utc)
        return self.get_events_between(start, start + timedelta(days=7), campus)
