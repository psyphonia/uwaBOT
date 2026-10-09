import asyncio
from datetime import datetime, timezone

import discord
from discord import app_commands

from database import Database
from models import CalendarEvent, Campus, INDIA_TZ


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def campus_label(campus: Campus | None) -> str:
    return campus.value if campus is not None else "Mumbai and Chennai"


def sample_embed(title: str, campus: Campus | None) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=f"Campus: {campus_label(campus)}",
        color=discord.Color.blue(),
    )
    embed.set_footer(text="FAKE / SAMPLE DATA • All times IST (UTC+05:30) • Not an official timetable")
    return embed


def event_embed(title: str, events: list[CalendarEvent], campus: Campus | None) -> discord.Embed:
    embed = sample_embed(title, campus)
    if not events:
        embed.add_field(name="Classes", value="No sample classes in this period.", inline=False)
    for event in events:
        start = event.start_at.astimezone(INDIA_TZ)
        end = event.end_at.astimezone(INDIA_TZ)
        embed.add_field(
            name=f"{start:%a %d %b, %H:%M}–{end:%H:%M} | {event.course_code}",
            value=f"{event.title}\n{campus_label(event.campus)} • {event.location}", inline=False,
        )
    return embed


def register_academic_commands(tree: app_commands.CommandTree, database: Database) -> None:

    @tree.command(name="today", description="Show today's sample classes in India time.")
    async def today(interaction: discord.Interaction, campus: Campus | None = None) -> None:
        events = await asyncio.to_thread(database.get_events_for_day, utc_now(), campus)
        await interaction.response.send_message(embed=event_embed("Today's classes", events, campus))

    @tree.command(name="week", description="Show sample classes starting in the next 7 days.")
    async def week(interaction: discord.Interaction, campus: Campus | None = None) -> None:
        events = await asyncio.to_thread(database.get_events_for_week, utc_now(), campus)
        await interaction.response.send_message(embed=event_embed("Classes • Next 7 days", events, campus))

    @tree.command(name="due", description="Show upcoming sample assignment deadlines.")
    async def due(interaction: discord.Interaction, campus: Campus | None = None) -> None:
        assignments = await asyncio.to_thread(database.get_upcoming_assignments, utc_now(), campus)
        embed = sample_embed("Upcoming assignments", campus)
        if not assignments:
            embed.add_field(name="Assignments", value="No upcoming sample assignments.", inline=False)
        for assignment in assignments:
            deadline = assignment.due_at.astimezone(INDIA_TZ)
            embed.add_field(
                name=f"{assignment.course_code} | {assignment.title}",
                value=f"Due {deadline:%a %d %b %Y, %H:%M} IST\n{campus_label(assignment.campus)}", inline=False,
            )
        await interaction.response.send_message(embed=embed)

    @tree.command(name="course", description="Look up a sample course, such as CITS1401.")
    @app_commands.describe(course_code="Course code, for example CITS1401")
    async def course(
        interaction: discord.Interaction, course_code: str, campus: Campus | None = None
    ) -> None:
        courses = await asyncio.to_thread(database.get_course, course_code, campus)
        embed = sample_embed("Course information", campus)
        if not courses:
            embed.add_field(name="Course not found", value="No matching sample course for this campus selection.", inline=False)
        for item in courses:
            embed.add_field(name=f"{item.code} • {campus_label(item.campus)}", value=item.name, inline=False)
        await interaction.response.send_message(embed=embed)
