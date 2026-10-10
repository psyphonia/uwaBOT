import logging
import asyncio
from contextlib import suppress
import os
import sqlite3
import signal
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

from academic_commands import register_academic_commands
from database import Database
from mock_data import build_mock_data
from reminders import ReminderService, load_reminder_channel_id, reminder_embed
from models import Assignment, Campus
from integrations import MockIntegration
from sync_engine import SyncEngine
from sync_commands import register_sync_command
from user_data import UserData, user_timezone
from user_reminders import UserReminderService
from profile_commands import register_profile_commands
from deployment import database_path, admin_id, blackboard_config, configure_logging


LOGGER = logging.getLogger("uwa_india_bot")
ENV_PATH = Path(__file__).resolve().parent / ".env"
DB_PATH = Path(__file__).resolve().parent / "uwa_india.db"


def load_token(env_path: Path = ENV_PATH) -> str:
    load_dotenv(env_path, override=False)
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise ValueError("DISCORD_TOKEN is missing. Set it in .env or the environment.")
    return token


async def ping(interaction: discord.Interaction) -> None:
    await interaction.response.send_message("Pong!")


class UwaBot(discord.Client):
    def __init__(self, database: Database, reminder_channel_id: int | None = None,
                 sync_engine: SyncEngine | None = None) -> None:
        super().__init__(intents=discord.Intents(guilds=True))
        self.tree = app_commands.CommandTree(self)
        self.tree.command(name="ping", description="Check whether the bot is online.")(ping)
        campus_setting = os.getenv("UWA_CAMPUS", "").strip()
        default_campus = Campus(campus_setting) if campus_setting else None
        register_academic_commands(self.tree, database, default_campus)
        self.sync_engine = sync_engine if sync_engine is not None else SyncEngine(database, {"mock": MockIntegration()})
        register_sync_command(self.tree, self.sync_engine, admin_id())
        self.database = database
        self.reminder_channel_id = reminder_channel_id
        self.students = UserData(database)
        register_profile_commands(self.tree, self.students)
        self.reminders = UserReminderService(self.students, self.deliver_user_reminder)

        @self.tree.command(name="reminders", description="Show deadline reminder thresholds and delivery status.")
        async def reminders(interaction: discord.Interaction) -> None:
            embed = discord.Embed(title="Deadline reminders", color=discord.Color.blue())
            if hasattr(interaction, 'user'):
                profile = await asyncio.to_thread(self.students.profile, interaction.user.id)
                if not profile:
                    await interaction.response.send_message('Run /setup to configure your campus, courses and reminders.', ephemeral=True)
                    return
                thresholds = [label for t,label in [(168,'7 days'),(24,'24 hours'),(3,'3 hours')] if profile[f'remind_{t}']]
                embed.add_field(name='Thresholds', value=' • '.join(thresholds) or 'None selected')
                embed.add_field(name='Delivery', value='Private Discord messages enabled' if profile['reminders_enabled'] else 'Disabled')
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            embed.add_field(name="Thresholds", value="7 days • 24 hours • 3 hours before the deadline", inline=False)
            configured = self.reminder_channel_id is not None
            embed.add_field(name="Delivery", value=("Enabled" if self.reminder_loop.is_running() else "Configured; waiting for startup")
                            if configured else "Disabled — reminder channel is not configured.", inline=False)
            embed.set_footer(text="Single-server settings • Due times shown in IST • No per-user preferences yet")
            await interaction.response.send_message(embed=embed, ephemeral=True,
                                                    allowed_mentions=discord.AllowedMentions.none())

    async def setup_hook(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            loop.add_signal_handler(signal.SIGTERM, lambda: asyncio.create_task(self.close()))
        except (NotImplementedError, RuntimeError):
            pass  # Ctrl+C remains supported by discord.py on Windows.
        if os.getenv('HEALTH_FILE','').strip() and not self.health_loop.is_running():
            self.health_loop.start()
        commands = await self.tree.sync()
        LOGGER.info("event=commands_synced count=%d", len(commands))
        if self.reminder_channel_id is not None or await asyncio.to_thread(self.students.reminder_users):
            pending = await asyncio.to_thread(self.database.pending_reminder_count)
            if pending:
                LOGGER.warning("event=reminders_pending_review count=%d Automatic retries disabled for uncertain deliveries.", pending)
            if not self.reminder_loop.is_running():
                self.reminder_loop.start()

    async def deliver_reminder(self, assignment: Assignment) -> None:
        channel = self.get_channel(self.reminder_channel_id)
        if channel is None:
            channel = await self.fetch_channel(self.reminder_channel_id)
        await channel.send(embed=reminder_embed(assignment), allowed_mentions=discord.AllowedMentions.none())

    async def deliver_user_reminder(self, user_id: int, assignment: Assignment) -> None:
        user = self.get_user(user_id) or await self.fetch_user(user_id)
        embed = reminder_embed(assignment)
        profile = await asyncio.to_thread(self.students.profile, user_id)
        if profile and profile['timezone'] == 'UTC':
            deadline = assignment.due_at.astimezone(user_timezone(profile))
            embed.set_field_at(0, name='Due', value=f"{deadline:%d %b %Y, %I:%M %p} UTC\n"
                               f"{discord.utils.format_dt(assignment.due_at, style='R')}", inline=False)
        await user.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @tasks.loop(seconds=30)
    async def health_loop(self) -> None:
        path = Path(os.environ['HEALTH_FILE'])
        try:
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps({'ready':self.is_ready(),'timestamp':time.time()}))
            temporary.replace(path)
        except OSError:
            LOGGER.error('event=health_write_failed')

    @tasks.loop(minutes=1)
    async def reminder_loop(self) -> None:
        try:
            await self.reminders.check()
        except Exception:
            LOGGER.error("event=reminder_check_failed Check will retry next minute; reserved deliveries require review.")

    @reminder_loop.before_loop
    async def before_reminder_loop(self) -> None:
        await self.wait_until_ready()

    async def close(self) -> None:
        health_task = self.health_loop.get_task()
        self.health_loop.cancel()
        if health_task is not None:
            with suppress(asyncio.CancelledError):
                await health_task
        if os.getenv('HEALTH_FILE','').strip():
            with suppress(OSError):
                Path(os.environ['HEALTH_FILE']).unlink()
        task = self.reminder_loop.get_task()
        self.reminder_loop.cancel()
        if task is not None:
            with suppress(asyncio.CancelledError):
                await task
        await super().close()

    async def on_ready(self) -> None:
        LOGGER.info("event=ready user_id=%s", self.user.id if self.user else "unknown")


def main() -> int:
    try:
        token = load_token()
        configure_logging()
        admin_id()
        config = blackboard_config()
        if config:
            LOGGER.info('event=blackboard_registration_configured live_access_disabled awaiting_approval_and_user_linking')
        LOGGER.info('event=university_integrations_disabled blackboard_pending_approval')
    except ValueError as error:
        LOGGER.error("event=configuration_error %s", error)
        return 1

    LOGGER.info("event=starting")
    try:
        database = Database(database_path(DB_PATH))
        database.initialize()
        database.seed(build_mock_data(datetime.now(timezone.utc)))
        LOGGER.info("event=database_ready")
        UwaBot(database, load_reminder_channel_id()).run(token, log_handler=None)
    except sqlite3.Error:
        LOGGER.error("event=database_failed Could not initialize or seed the SQLite database.")
        return 1
    except ValueError:
        LOGGER.error("event=configuration_error Check campus and deployment configuration.")
        return 1
    except discord.LoginFailure:
        LOGGER.error("event=login_failed Discord rejected DISCORD_TOKEN. Check the bot token.")
        return 1
    except (discord.DiscordException, OSError):
        LOGGER.error("event=startup_failed Could not connect to Discord or sync slash commands.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
