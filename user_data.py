"""Local profiles only. University credentials and account linking stay disabled."""
from datetime import datetime, timezone
import sqlite3
from models import Assignment, Campus, Course, INDIA_TZ
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from database import Database


def initialize_users(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            discord_user_id TEXT PRIMARY KEY, campus TEXT NOT NULL REFERENCES campuses(name)
            CHECK(campus IN ('Mumbai','Chennai')));
        CREATE TABLE IF NOT EXISTS user_preferences (
            user_id TEXT PRIMARY KEY REFERENCES users(discord_user_id) ON DELETE CASCADE,
            timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata' CHECK(timezone IN ('Asia/Kolkata','UTC')),
            reminders_enabled INTEGER NOT NULL DEFAULT 0,
            remind_168 INTEGER NOT NULL DEFAULT 1, remind_24 INTEGER NOT NULL DEFAULT 1,
            remind_3 INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS user_courses (
            user_id TEXT NOT NULL REFERENCES users(discord_user_id) ON DELETE CASCADE,
            course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
            PRIMARY KEY(user_id,course_id));
        CREATE TABLE IF NOT EXISTS integration_accounts (
            id INTEGER PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(discord_user_id) ON DELETE CASCADE,
            source TEXT NOT NULL CHECK(source IN ('mycamu','blackboard')), external_account_id TEXT,
            status TEXT NOT NULL DEFAULT 'disabled' CHECK(status='disabled'),
            UNIQUE(user_id,source));
        CREATE TABLE IF NOT EXISTS user_sent_reminders (
            user_id TEXT NOT NULL REFERENCES users(discord_user_id) ON DELETE CASCADE,
            assignment_id TEXT NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
            due_at TEXT NOT NULL, threshold INTEGER NOT NULL CHECK(threshold IN (168,24,3)),
            claimed_at TEXT NOT NULL, sent_at TEXT,
            PRIMARY KEY(user_id,assignment_id,due_at,threshold));
    """)


class UserData:
    def __init__(self, database: 'Database') -> None:
        self.database = database

    def profile(self, user_id: int) -> dict | None:
        with self.database.connect() as connection:
            row = connection.execute("""SELECT u.*, p.* FROM users u JOIN user_preferences p
                ON p.user_id=u.discord_user_id WHERE u.discord_user_id=?""", (str(user_id),)).fetchone()
        return dict(row) if row else None

    def setup(self, user_id: int, campus: Campus, codes: list[str], timezone_name: str = 'Asia/Kolkata',
              reminders: bool = False, thresholds: tuple[int, ...] = (168,24,3)) -> None:
        if not isinstance(campus, Campus) or timezone_name not in ('Asia/Kolkata','UTC') or set(thresholds)-{168,24,3}:
            raise ValueError('Invalid campus, timezone or reminder thresholds.')
        if not 0 < user_id < 2**64:
            raise ValueError('Invalid Discord user ID.')
        with self.database.connect() as connection:
            selected = []
            for code in set(code.strip().upper() for code in codes if code.strip()):
                rows = connection.execute("""SELECT id FROM courses WHERE source='mock' AND code=?
                    AND (campus=? OR campus IS NULL)""", (code,campus.value)).fetchall()
                if not rows:
                    raise ValueError('Unknown sample course for the selected campus.')
                selected.extend(row['id'] for row in rows)
            connection.execute("INSERT INTO users VALUES (?,?) ON CONFLICT(discord_user_id) DO UPDATE SET campus=excluded.campus",
                               (str(user_id),campus.value))
            connection.execute("""INSERT INTO user_preferences VALUES (?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
                timezone=excluded.timezone, reminders_enabled=excluded.reminders_enabled,
                remind_168=excluded.remind_168, remind_24=excluded.remind_24, remind_3=excluded.remind_3""",
                (str(user_id),timezone_name,int(reminders),*(int(t in thresholds) for t in (168,24,3))))
            connection.execute('DELETE FROM user_courses WHERE user_id=?',(str(user_id),))
            connection.executemany('INSERT INTO user_courses VALUES (?,?)',[(str(user_id),c) for c in selected])

    def courses(self, user_id: int) -> list[Course]:
        from models import Course
        from database import read_campus
        with self.database.connect() as connection:
            rows = connection.execute("""SELECT c.* FROM courses c JOIN user_courses uc ON uc.course_id=c.id
                JOIN users u ON u.discord_user_id=uc.user_id WHERE uc.user_id=? AND c.source='mock'
                AND (c.campus=u.campus OR c.campus IS NULL) ORDER BY c.code,c.id""",(str(user_id),)).fetchall()
        return [Course(r['code'],r['name'],read_campus(r['campus']),r['source'],r['external_id'],r['source_url']) for r in rows]

    def filter(self, user_id: int, records: list) -> list:
        profile = self.profile(user_id)
        if not profile:
            return []
        courses = self.courses(user_id)
        return [r for r in records if r.source=='mock' and r.campus in (None,Campus(profile['campus']))
                and any(c.code==getattr(r,'course_code',getattr(r,'code',None)) and
                        c.source==r.source and c.campus in (None,r.campus) for c in courses)]

    def reminder_users(self) -> list[dict]:
        with self.database.connect() as connection:
            rows=connection.execute('SELECT * FROM user_preferences WHERE reminders_enabled=1').fetchall()
        return [dict(r) for r in rows]

    def reminder_state(self, user_id: int, assignment: Assignment, threshold: int, now: datetime, action: str) -> bool:
        from database import utc_text
        key=(str(user_id),assignment.id,utc_text(assignment.due_at),threshold)
        with self.database.connect() as connection:
            if action=='claim':
                result=connection.execute("""INSERT INTO user_sent_reminders
                    (user_id,assignment_id,due_at,threshold,claimed_at) VALUES (?,?,?,?,?) ON CONFLICT DO NOTHING""",
                    (*key,utc_text(now)))
                return result.rowcount==1
            if action=='sent':
                connection.execute('UPDATE user_sent_reminders SET sent_at=? WHERE user_id=? AND assignment_id=? AND due_at=? AND threshold=?',
                                   (utc_text(now),*key))
            elif action=='release':
                connection.execute('DELETE FROM user_sent_reminders WHERE user_id=? AND assignment_id=? AND due_at=? AND threshold=? AND sent_at IS NULL',key)
            else:
                raise ValueError('Unknown reminder action.')
        return True


def user_timezone(profile: dict):
    return timezone.utc if profile['timezone']=='UTC' else INDIA_TZ
