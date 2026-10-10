"""SQLite migrations and atomic per-source normalized record upserts."""
import json
import sqlite3
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from database import Database


def initialize_sync(connection: sqlite3.Connection) -> None:
    connection.execute("DROP INDEX IF EXISTS course_identity")
    connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS course_sample_identity ON courses(source, code, COALESCE(campus, '')) WHERE external_id IS NULL")
    for table in ("courses", "assignments", "calendar_events"):
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        if "last_synced_at" not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN last_synced_at TEXT")
    # Legacy events required a course and a positive duration; holidays/deadlines do not.
    columns = list(connection.execute("PRAGMA table_info(calendar_events)"))
    if "all_day" not in {row["name"] for row in columns}:
        connection.execute("ALTER TABLE calendar_events RENAME TO legacy_calendar_events")
        connection.execute("""CREATE TABLE calendar_events (
            id TEXT PRIMARY KEY, course_id INTEGER REFERENCES courses(id), title TEXT NOT NULL,
            start_at TEXT NOT NULL, end_at TEXT NOT NULL CHECK(end_at >= start_at), location TEXT NOT NULL,
            campus TEXT REFERENCES campuses(name), source TEXT NOT NULL, external_id TEXT,
            source_url TEXT, last_synced_at TEXT, all_day INTEGER NOT NULL DEFAULT 0)""")
        connection.execute("""INSERT INTO calendar_events
            SELECT id, course_id, title, start_at, end_at, location, campus, source, external_id,
                   source_url, last_synced_at, 0 FROM legacy_calendar_events""")
        connection.execute("DROP TABLE legacy_calendar_events")
        connection.execute("CREATE INDEX event_start ON calendar_events(start_at)")
    connection.execute("""CREATE TABLE IF NOT EXISTS announcements (
        id TEXT PRIMARY KEY, course_id INTEGER REFERENCES courses(id), title TEXT NOT NULL,
        body TEXT NOT NULL, published_at TEXT NOT NULL, campus TEXT REFERENCES campuses(name),
        source TEXT NOT NULL, external_id TEXT NOT NULL, source_url TEXT, last_synced_at TEXT NOT NULL)""")
    connection.execute("""CREATE TABLE IF NOT EXISTS course_content (
        id TEXT PRIMARY KEY, course_id INTEGER NOT NULL REFERENCES courses(id), title TEXT NOT NULL,
        description TEXT NOT NULL, handler TEXT, campus TEXT REFERENCES campuses(name),
        source TEXT NOT NULL, external_id TEXT NOT NULL, source_url TEXT, last_synced_at TEXT NOT NULL)""")
    for table in ("courses", "assignments", "calendar_events", "announcements", "course_content"):
        connection.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {table}_source_identity ON {table}(source, external_id)")


def persist(database: "Database", source: str, records: dict[str, list], now: datetime) -> dict[str, dict[str, int]]:
    from database import utc_text, campus_value
    counts = {kind: dict(inserted=0, updated=0, unchanged=0) for kind in records}
    stamp = utc_text(now)
    with database.connect() as connection:
        def upsert(table, values, external, kind):
            existing = connection.execute(f"SELECT * FROM {table} WHERE source=? AND external_id=?",
                                          (source, external)).fetchone()
            if existing is None and source == "mock":
                if table == "courses":
                    existing = connection.execute("SELECT * FROM courses WHERE source=? AND code=? AND campus IS ? AND external_id IS NULL",
                                                  (source, values["code"], values["campus"])).fetchone()
                elif table in ("assignments", "calendar_events"):
                    existing = connection.execute(f"SELECT * FROM {table} WHERE source=? AND id=? AND external_id IS NULL",
                                                  (source, values["id"])).fetchone()
            if existing:
                values.pop("id", None)
                changed = any(existing[key] != value for key, value in values.items())
                category = "updated" if changed else "unchanged"
                columns = list(values) + ["external_id", "last_synced_at"]
                connection.execute(f"UPDATE {table} SET " + ",".join(key+"=?" for key in columns) + " WHERE id=?",
                                   [*values.values(), external, stamp, existing["id"]])
                result_id = existing["id"]
            else:
                values.update(source=source, external_id=external, last_synced_at=stamp)
                columns = list(values)
                cursor = connection.execute(f"INSERT INTO {table} (" + ",".join(columns) + ") VALUES (" +
                                            ",".join("?" for _ in columns) + ")", list(values.values()))
                result_id = values.get("id", cursor.lastrowid)
                category = "inserted"
            counts[kind][category] += 1
            return result_id

        course_ids = {}
        for course in records["courses"]:
            if (course.code, course.campus) in course_ids:
                raise ValueError("Ambiguous course code/campus identities in one source.")
            external = course.external_id or json.dumps([course.code, campus_value(course.campus)], separators=(",", ":"))
            course_ids[(course.code, course.campus)] = upsert("courses", dict(code=course.code, name=course.name,
                campus=campus_value(course.campus), source_url=course.source_url), external, "courses")

        def course_id(item):
            code = getattr(item, "course_code", None)
            if code is None:
                return None
            exact = course_ids.get((code, item.campus))
            candidates = [exact] if exact is not None else [identifier for (course_code, campus), identifier in course_ids.items()
                          if course_code == code and campus is None]
            if len(candidates) != 1:
                raise ValueError("Missing or ambiguous same-source course association.")
            return candidates[0]

        for kind, table in [("assignments", "assignments"), ("events", "calendar_events"),
                            ("announcements", "announcements"), ("content", "course_content")]:
            for item in records[kind]:
                external = item.external_id or item.id
                # Keep seed IDs for mock records/reminder state; source-namespace all others.
                identifier = item.id if source == "mock" else json.dumps([source, kind, external], separators=(",", ":"))
                values = dict(id=identifier, course_id=course_id(item), title=item.title,
                              campus=campus_value(item.campus), source_url=item.source_url)
                if kind == "assignments":
                    values["due_at"] = utc_text(item.due_at)
                elif kind == "events":
                    values.update(start_at=utc_text(item.start_at), end_at=utc_text(item.end_at),
                                  location=item.location, all_day=int(item.all_day))
                elif kind == "announcements":
                    values.update(body=item.body, published_at=utc_text(item.published_at))
                else:
                    values.update(description=item.description, handler=item.handler)
                upsert(table, values, external, kind)
    return counts
