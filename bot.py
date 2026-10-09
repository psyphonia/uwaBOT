import logging
import asyncio
from contextlib import suppress
import os
import sqlite3
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
from models import Assignment


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
    def __init__(self, database: Database, reminder_channel_id: int | None = None) -> None:
        super().__init__(intents=discord.Intents(guilds=True))
        self.tree = app_commands.CommandTree(self)
        self.tree.command(name="ping", description="Check whether the bot is online.")(ping)
        register_academic_commands(self.tree, database)
        self.database = database
        self.reminder_channel_id = reminder_channel_id
        self.reminders = ReminderService(database, self.deliver_reminder)

    async def setup_hook(self) -> None:
        commands = await self.tree.sync()
        LOGGER.info("event=commands_synced count=%d", len(commands))
        if self.reminder_channel_id is not None:
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
        task = self.reminder_loop.get_task()
        self.reminder_loop.cancel()
        if task is not None:
            with suppress(asyncio.CancelledError):
                await task
        await super().close()

    async def on_ready(self) -> None:
        LOGGER.info("event=ready user_id=%s", self.user.id if self.user else "unknown")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s level=%(levelname)s logger=%(name)s %(message)s",
    )
    try:
        token = load_token()
    except ValueError as error:
        LOGGER.error("event=configuration_error %s", error)
        return 1

    LOGGER.info("event=starting")
    try:
        database = Database(DB_PATH)
        database.initialize()
        database.seed(build_mock_data(datetime.now(timezone.utc)))
        LOGGER.info("event=database_ready")
        UwaBot(database, load_reminder_channel_id()).run(token, log_handler=None)
    except sqlite3.Error:
        LOGGER.error("event=database_failed Could not initialize or seed the SQLite database.")
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
