import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
import logging
import os
import sqlite3

import discord

from database import Database, utc_text
from models import Assignment, INDIA_TZ


LOGGER = logging.getLogger("uwa_india_bot.reminders")
WINDOWS = ((168, 24), (24, 6), (3, 3))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def load_reminder_channel_id() -> int | None:
    value = os.getenv("REMINDER_CHANNEL_ID", "").strip()
    if not value:
        LOGGER.warning("event=reminders_disabled REMINDER_CHANNEL_ID is missing.")
        return None
    if not value.isascii() or not value.isdecimal() or not 0 < int(value) < 2**64:
        LOGGER.warning("event=reminders_disabled REMINDER_CHANNEL_ID must be a positive Discord channel ID.")
        return None
    return int(value)


def applicable_threshold(due_at: datetime, now: datetime) -> int | None:
    utc_text(due_at)
    utc_text(now)
    remaining = due_at.astimezone(timezone.utc) - now.astimezone(timezone.utc)
    for threshold, grace in WINDOWS:
        if timedelta(hours=threshold - grace) < remaining <= timedelta(hours=threshold):
            return threshold
    return None


def reminder_embed(assignment: Assignment) -> discord.Embed:
    deadline = assignment.due_at.astimezone(INDIA_TZ)
    embed = discord.Embed(
        title="Assignment reminder",
        description=f"{assignment.course_code} — {assignment.title}",
        color=discord.Color.orange(),
    )
    embed.add_field(name="Due", value=f"{deadline:%d %b %Y, %I:%M %p} IST\n"
                    f"{discord.utils.format_dt(assignment.due_at, style='R')}", inline=False)
    embed.add_field(name="Campus", value=assignment.campus.value if assignment.campus else "Mumbai and Chennai")
    if assignment.source_url:
        embed.add_field(name="Open assignment", value=assignment.source_url, inline=False)
    if assignment.source == "mock":
        embed.set_footer(text="FAKE / SAMPLE DATA • Not an official university deadline")
    return embed


class ReminderService:
    def __init__(
        self, database: Database, deliver: Callable[[Assignment], Awaitable[None]],
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.database = database
        self.deliver = deliver
        self.clock = clock

    async def check(self) -> None:
        assignments = await asyncio.to_thread(self.database.get_upcoming_assignments, self.clock())
        for assignment in assignments:
            now = self.clock()
            threshold = applicable_threshold(assignment.due_at, now)
            if threshold is None:
                continue
            claimed = await asyncio.to_thread(self.database.claim_reminder, assignment, threshold, now)
            if not claimed:
                continue
            # Recheck after waiting for SQLite, so a queued reminder cannot become obsolete.
            if applicable_threshold(assignment.due_at, self.clock()) != threshold:
                await asyncio.to_thread(self.database.release_reminder, assignment, threshold)
                continue
            try:
                await self.deliver(assignment)
            except discord.HTTPException as error:
                if 400 <= error.status < 500:
                    await asyncio.to_thread(self.database.release_reminder, assignment, threshold)
                    LOGGER.warning("event=reminder_delivery_rejected assignment_id=%s threshold=%d", assignment.id, threshold)
                else:
                    LOGGER.error("event=reminder_delivery_uncertain assignment_id=%s threshold=%d review_pending_delivery", assignment.id, threshold)
                continue
            except (OSError, TimeoutError):
                LOGGER.error("event=reminder_delivery_uncertain assignment_id=%s threshold=%d review_pending_delivery", assignment.id, threshold)
                continue
            try:
                await asyncio.to_thread(self.database.mark_reminder_sent, assignment, threshold, self.clock())
            except sqlite3.Error:
                LOGGER.error("event=reminder_record_failed assignment_id=%s threshold=%d review_pending_delivery", assignment.id, threshold)
                continue
            LOGGER.info("event=reminder_sent assignment_id=%s threshold=%d", assignment.id, threshold)
