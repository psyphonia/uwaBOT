import json
import logging
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from database import Database
from deployment import SecretFilter, admin_id, blackboard_config, database_path
from healthcheck import healthy


class DeploymentTests(unittest.TestCase):
    def test_container_uses_persistent_volume_restart_and_healthcheck(self) -> None:
        root = Path(__file__).resolve().parents[1]
        compose = (root / "compose.yaml").read_text(encoding="utf-8")
        dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
        dockerignore = (root / ".dockerignore").read_text(encoding="utf-8")
        self.assertIn("bot-data:/data", compose)
        self.assertIn("restart: unless-stopped", compose)
        self.assertIn("stop_grace_period: 30s", compose)
        self.assertIn("HEALTHCHECK", dockerfile)
        self.assertIn('CMD ["python", "bot.py"]', dockerfile)
        self.assertIn(".env", dockerignore.splitlines())

    def test_database_path_is_persistent_and_reopens(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data" / "bot.db"
            with patch.dict(os.environ, {"DATABASE_PATH": str(path)}, clear=True):
                database = Database(database_path(Path("unused.db")))
                database.initialize()
                with database.connect() as connection:
                    connection.execute("INSERT INTO users VALUES (?, ?)", ("123", "Mumbai"))
                Database(path).initialize()
                with database.connect() as connection:
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)

    def test_memory_database_is_rejected(self) -> None:
        with patch.dict(os.environ, {"DATABASE_PATH": ":memory:"}, clear=True):
            with self.assertRaisesRegex(ValueError, "persistent"):
                database_path(Path("unused.db"))

    def test_blackboard_auth_is_optional_and_partial_configuration_fails(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(blackboard_config())
            self.assertIsNone(admin_id())
        with patch.dict(os.environ, {"BLACKBOARD_CLIENT_ID": "sample"}, clear=True):
            with self.assertRaisesRegex(ValueError, "incomplete"):
                blackboard_config()
        with patch.dict(os.environ, {"SYNC_ADMIN_USER_ID": "invalid"}, clear=True):
            with self.assertRaisesRegex(ValueError, "Discord user ID"):
                admin_id()

    def test_blackboard_registration_validation_does_not_make_network_requests(self) -> None:
        environment = {
            "BLACKBOARD_BASE_URL": "https://learn.example.test",
            "BLACKBOARD_CLIENT_ID": "sample-client",
            "BLACKBOARD_CLIENT_SECRET": "sample-secret",
            "BLACKBOARD_REDIRECT_URI": "https://bot.example.test/callback",
        }
        with patch.dict(os.environ, environment, clear=True), patch("socket.socket", side_effect=AssertionError("network")):
            config = blackboard_config()
        self.assertEqual(config.base_url, environment["BLACKBOARD_BASE_URL"])
        self.assertFalse(config.offline)

    def test_log_filter_redacts_environment_secrets(self) -> None:
        with patch.dict(os.environ, {"DISCORD_TOKEN": "synthetic-secret"}, clear=True):
            record = logging.LogRecord("test", logging.INFO, "", 0, "token=%s", ("synthetic-secret",), None)
            SecretFilter().filter(record)
            self.assertNotIn("synthetic-secret", record.getMessage())
            self.assertIn("[REDACTED]", record.getMessage())

    def test_health_requires_fresh_ready_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "health.json"
            self.assertFalse(healthy(path, 150))
            for ready, timestamp, expected in ((True, 149, True), (False, 149, False), (True, 0, False)):
                path.write_text(json.dumps({"ready": ready, "timestamp": timestamp}), encoding="utf-8")
                self.assertEqual(healthy(path, 150), expected)
