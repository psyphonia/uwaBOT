import os
from pathlib import Path
import tempfile
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import discord

import bot
from database import Database


class TokenTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        patcher = patch.object(bot, "DB_PATH", Path(directory.name) / "test.db")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_loads_token_from_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text("DISCORD_TOKEN=sample-test-token\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(bot.load_token(env_path), "sample-test-token")

    def test_environment_takes_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text("DISCORD_TOKEN=file-test-token\n", encoding="utf-8")
            with patch.dict(os.environ, {"DISCORD_TOKEN": "env-test-token"}, clear=True):
                self.assertEqual(bot.load_token(env_path), "env-test-token")

    def test_missing_or_blank_token_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for environment in ({}, {"DISCORD_TOKEN": "   "}):
                with self.subTest(environment=environment):
                    with patch.dict(os.environ, environment, clear=True):
                        with self.assertRaisesRegex(ValueError, "DISCORD_TOKEN is missing"):
                            bot.load_token(Path(directory) / ".env")

    def test_missing_token_does_not_start_client(self) -> None:
        with patch.object(bot, "load_token", side_effect=ValueError("DISCORD_TOKEN is missing")):
            with patch.object(bot, "UwaBot") as client:
                with self.assertLogs(bot.LOGGER, level="ERROR"):
                    self.assertEqual(bot.main(), 1)
                client.assert_not_called()

    def test_invalid_token_fails_without_logging_token(self) -> None:
        with patch.object(bot, "load_token", return_value="sample-test-token"):
            with patch.object(bot.UwaBot, "run", side_effect=discord.LoginFailure("sample-test-token")):
                with self.assertLogs(bot.LOGGER, level="INFO") as logs:
                    self.assertEqual(bot.main(), 1)
                self.assertNotIn("sample-test-token", "\n".join(logs.output))
                self.assertIn("event=login_failed", "\n".join(logs.output))

    def test_database_failure_does_not_start_client(self) -> None:
        with patch.object(bot, "load_token", return_value="sample-test-token"):
            with patch.object(Database, "initialize", side_effect=sqlite3.OperationalError("test error")):
                with patch.object(bot, "UwaBot") as client:
                    with self.assertLogs(bot.LOGGER, level="ERROR") as logs:
                        self.assertEqual(bot.main(), 1)
                    client.assert_not_called()
                    self.assertIn("event=database_failed", "\n".join(logs.output))


class CommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Database(Path(directory.name) / "test.db")
        self.database.initialize()

    async def test_ping_slash_command_returns_pong(self) -> None:
        async with bot.UwaBot(self.database) as client:
            self.assertEqual(
                {command.name for command in client.tree.get_commands()},
                {"ping", "today", "week", "due", "course"},
            )
            command = client.tree.get_command("ping")
            self.assertIsInstance(command, discord.app_commands.Command)
            interaction = SimpleNamespace(response=SimpleNamespace(send_message=AsyncMock()))
            await command.callback(interaction)
            interaction.response.send_message.assert_awaited_once_with("Pong!")
            self.assertFalse(client.intents.message_content)
            self.assertFalse(client.intents.members)

    async def test_startup_syncs_commands_and_logs_ready(self) -> None:
        async with bot.UwaBot(self.database) as client:
            with patch.object(client.tree, "sync", new_callable=AsyncMock) as sync:
                sync.return_value = [client.tree.get_command("ping")]
                with self.assertLogs(bot.LOGGER, level="INFO") as logs:
                    await client.setup_hook()
                    await client.on_ready()
                sync.assert_awaited_once_with()
                self.assertIn("event=commands_synced count=1", "\n".join(logs.output))
                self.assertIn("event=ready", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
