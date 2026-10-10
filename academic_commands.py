import asyncio
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from html import unescape
import re
from urllib.parse import urlsplit

import discord
from discord import app_commands

from database import Database
from models import CalendarEvent, Campus, INDIA_TZ
from user_data import UserData, user_timezone

DISPLAY_TZ = ContextVar("display_timezone", default=INDIA_TZ)
NEEDS_SETUP = ContextVar("needs_setup", default=False)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def campus_label(campus: Campus | None) -> str:
    return campus.value if campus is not None else "Mumbai and Chennai"


def relative_due(due: datetime | None, now: datetime) -> str:
    if due is None:
        return "Due date unavailable"
    seconds = (due - now).total_seconds()
    if seconds < 0:
        return "Past due"
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        amount = int(seconds // size)
        if amount:
            return f"Due in {amount} {unit}{'s' if amount != 1 else ''}"
    return "Due now"


def plain(value: str) -> str:
    value = unescape(re.sub(r"<[^>]*>", "", value))
    value = re.sub(r"([\\`*_{}\[\]<>()#+.!|~\-])", r"\\\1", value)
    return re.sub(r"@(everyone|here|[!&]?[0-9]+)", "@\u200b\\1", value)


def source_line(item) -> str:
    name = {"mock": "Sample data", "mycamu": "MyCamu", "blackboard": "Blackboard"}.get(item.source, item.source)
    url = item.source_url
    if url:
        parsed = urlsplit(url)
        if parsed.scheme == "https" and parsed.netloc and not parsed.username and not parsed.password:
            return f"Source: [{name}]({url.replace(')', '%29').replace('(', '%28')})"
    return f"Source: {name}"


def sample_embed(title: str, campus: Campus | None, items=()) -> discord.Embed:
    embed = discord.Embed(title=title, description=f"Campus: {campus_label(campus)}", color=discord.Color.blue())
    sample = any(item.source == "mock" for item in items)
    embed.set_footer(text=("SAMPLE DATA â€¢ " if sample else "") + ("All times UTC" if DISPLAY_TZ.get() is timezone.utc else "All times IST (UTC+05:30)"))
    return embed


def add(embed: discord.Embed, name: str, value: str) -> bool:
    name, value = name[:256], value[:1024]
    if len(embed.fields) >= 24 or len(embed) + len(name) + len(value) > 5600:
        if len(embed.fields) < 25 and not any(field.name == "More results" for field in embed.fields):
            embed.add_field(name="More results", value="Showing the first results. Narrow your campus selection.", inline=False)
        return False
    embed.add_field(name=name, value=value, inline=False)
    return True


def event_embed(title: str, events: list[CalendarEvent], campus: Campus | None) -> discord.Embed:
    embed = sample_embed(title, campus, events)
    if not events:
        add(embed, "Classes", "No classes or events in this period.")
    for event in events:
        start, end = event.start_at.astimezone(DISPLAY_TZ.get()), event.end_at.astimezone(DISPLAY_TZ.get())
        clock = f"{start:%a %d %b, %H:%M}â€“{end:%H:%M} IST"
        if start.date() != end.date():
            clock = f"{start:%a %d %b, %H:%M}â€“{end:%d %b, %H:%M} IST"
        if event.all_day:
            clock = f"{start:%a %d %b} â€¢ All day"
        if not add(embed, f"{clock} | {event.course_code or 'Calendar'}",
                   f"{plain(event.title)}\n{campus_label(event.campus)} â€¢ {plain(event.location) or 'Location unavailable'}\n{source_line(event)}"):
            break
    return embed


async def send(interaction: discord.Interaction, embed: discord.Embed) -> None:
    if NEEDS_SETUP.get():
        embed.description = 'Run /setup to choose your campus and comma-separated sample course codes.'
    if DISPLAY_TZ.get() is timezone.utc:
        for index, field in enumerate(embed.fields):
            embed.set_field_at(index, name=field.name.replace(" IST", " UTC"),
                               value=field.value.replace(" IST", " UTC"), inline=field.inline)
    await interaction.response.send_message(embed=embed, ephemeral=hasattr(interaction, "user"), allowed_mentions=discord.AllowedMentions.none())


def register_academic_commands(tree: app_commands.CommandTree, database: Database, default_campus: Campus | None = None) -> None:
    users = UserData(database)

    async def preferences(interaction, campus):
        DISPLAY_TZ.set(INDIA_TZ)
        NEEDS_SETUP.set(False)
        if not hasattr(interaction, 'user'):
            return campus or default_campus
        record = await asyncio.to_thread(users.profile, interaction.user.id)
        NEEDS_SETUP.set(record is None)
        if record:
            DISPLAY_TZ.set(user_timezone(record))
            return campus or Campus(record['campus'])
        return campus or default_campus

    async def query(interaction, method, *args):
        if method == database.get_events_for_day and hasattr(interaction, 'user'):
            now, campus = args
            start = now.astimezone(DISPLAY_TZ.get()).replace(hour=0,minute=0,second=0,microsecond=0)
            records = await asyncio.to_thread(database.get_events_between,start,start+timedelta(days=1),campus)
        else:
            records = await asyncio.to_thread(method,*args)
        if hasattr(interaction, 'user'):
            return await asyncio.to_thread(users.filter,interaction.user.id,records)
        return records

    @tree.command(name="today", description="Show today's classes and events in IST.")
    async def today(interaction: discord.Interaction, campus: Campus | None = None) -> None:
        campus = await preferences(interaction, campus)
        events = await query(interaction, database.get_events_for_day, utc_now(), campus)
        await send(interaction, event_embed("Today's classes", events, campus))

    @tree.command(name="tomorrow", description="Show tomorrow's classes and events in IST.")
    async def tomorrow(interaction: discord.Interaction, campus: Campus | None = None) -> None:
        campus = await preferences(interaction, campus)
        events = await query(interaction, database.get_events_for_day, utc_now() + timedelta(days=1), campus)
        await send(interaction, event_embed("Tomorrow's classes", events, campus))

    @tree.command(name="week", description="Show classes and events starting in the next 7 days.")
    async def week(interaction: discord.Interaction, campus: Campus | None = None) -> None:
        campus = await preferences(interaction, campus)
        events = await query(interaction, database.get_events_for_week, utc_now(), campus)
        await send(interaction, event_embed("Classes â€¢ Next 7 days", events, campus))

    @tree.command(name="nextclass", description="Find your next future class or event.")
    async def nextclass(interaction: discord.Interaction, campus: Campus | None = None) -> None:
        campus = await preferences(interaction, campus)
        events = await query(interaction, database.get_events_between, utc_now()+timedelta(microseconds=1), datetime.max.replace(tzinfo=timezone.utc), campus)
        await send(interaction, event_embed("Next class or event", events[:1], campus))

    @tree.command(name="due", description="Show upcoming assignment deadlines in IST.")
    async def due(interaction: discord.Interaction, campus: Campus | None = None) -> None:
        campus = await preferences(interaction, campus)
        now = utc_now()
        assignments = await query(interaction, database.get_upcoming_assignments, now, campus)
        undated = await query(interaction, database.get_undated_assessments, campus)
        embed = sample_embed("Upcoming assignments", campus, assignments + undated)
        if not assignments and not undated:
            add(embed, "Assignments", "No upcoming assignments.")
        for item in assignments:
            if not add(embed, f"{item.course_code} | {plain(item.title)}",
                       f"Due {item.due_at.astimezone(DISPLAY_TZ.get()):%a %d %b %Y, %H:%M} IST\n{relative_due(item.due_at, now)}\n{campus_label(item.campus)}\n{source_line(item)}"):
                break
        for item in undated:
            if not add(embed, f"{item.course_code} | {plain(item.title)}", f"Due date unavailable in synced data\n{campus_label(item.campus)}\n{source_line(item)}"):
                break
        await send(interaction, embed)

    @tree.command(name="course", description="Look up course information, such as CITS1401.")
    @app_commands.describe(course_code="Course code, for example CITS1401")
    async def course(interaction: discord.Interaction, course_code: str, campus: Campus | None = None) -> None:
        campus = await preferences(interaction, campus)
        courses = await query(interaction, database.get_course, course_code, campus)
        embed = sample_embed("Course information", campus, courses)
        if not courses:
            add(embed, "Course not found", "No matching course for this campus selection.")
        for item in courses:
            if not add(embed, f"{item.code} â€¢ {campus_label(item.campus)}", f"{plain(item.name)}\n{source_line(item)}"):
                break
        await send(interaction, embed)

    @tree.command(name="announcements", description="Show recent synced course announcements.")
    async def announcements(interaction: discord.Interaction, campus: Campus | None = None) -> None:
        campus = await preferences(interaction, campus)
        items = await query(interaction, database.get_announcements, campus)
        embed = sample_embed("Announcements", campus, items)
        if not items:
            add(embed, "Announcements", "No announcements available.")
        for item in items:
            if not add(embed, plain(item.title), f"{item.published_at.astimezone(DISPLAY_TZ.get()):%d %b %Y, %H:%M} IST â€¢ {campus_label(item.campus)}\n{plain(item.body)[:700]}\n{source_line(item)}"):
                break
        await send(interaction, embed)
