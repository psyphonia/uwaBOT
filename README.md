# uwa-india-discord-bot

Persistent Discord bot for UWA India students in Mumbai and Chennai. Academic commands read from SQLite. `/setup` selects a campus, sample courses, timezone, and private-message reminders; `/profile` shows the saved preferences. `/sync` remains server-administrator-only and can be restricted to one Discord account with `SYNC_ADMIN_USER_ID`.

MyCamu is paused and is not loaded by the bot. Blackboard is the only planned live university integration. Optional Blackboard OAuth registration variables are validated at startup but do not enable authorization, account linking, or live synchronization. No university password, OAuth token, or client secret is stored in SQLite. Stage 14 and AI querying are intentionally omitted.

## Persistent deployment

Use one Docker container on an always-on Linux VM with persistent local storage. SQLite is sufficient for the current single-process bot; do not run multiple replicas against the same database.

Copy `.env.example` to `.env`, then set `DISCORD_TOKEN`. Set `SYNC_ADMIN_USER_ID` to limit `/sync` to one server administrator. Keep all four Blackboard variables empty until UWA approves and registers the integration. Never commit `.env`.

Install the Discord application with the `bot` and `applications.commands` scopes. It needs View Channel, Send Messages, and Embed Links permissions; no privileged intents or Discord Administrator permission are required. Students must allow bot direct messages to receive reminders.

Start or update the bot from the project directory:

```sh
docker compose up -d --build
```

Inspect it with:

```sh
docker compose ps
docker compose logs --tail=100 bot
```

The named `bot-data` volume keeps `/data/uwa_india.db` across restarts and container replacement. Do not run `docker compose down -v` unless you intend to delete the database. For an existing database, stop the bot, copy the database into the volume as `uwa_india.db`, grant UID 10001 read/write access, and restart. Database initialization is repeatable and preserves legacy data without guessing student ownership.

Compose uses `restart: unless-stopped`, a 30-second shutdown period, and an init process. SIGTERM closes reminder tasks and the Discord connection. A local heartbeat provides container health without exposing a network port. An unhealthy status is diagnostic; Compose does not restart a container solely because it is unhealthy.

Reminder claims and successful delivery state are stored per user, assignment, deadline, and threshold. Confirmed Discord rejection releases a claim. An uncertain delivery or crash retains its claim to prevent duplicate messages; inspect delivery before manually changing such state. The same SQLite volume must survive restarts to retain this protection.

## Environment

`DISCORD_TOKEN` is required. `DATABASE_PATH` defaults to `uwa_india.db`; production should use an absolute path on persistent storage. `LOG_LEVEL` accepts `INFO`, `WARNING`, or `ERROR`. `HEALTH_FILE` is optional outside Docker. `UWA_CAMPUS` remains an optional fallback for legacy/test use; configured student profiles take precedence.

The future Blackboard variables are `BLACKBOARD_BASE_URL`, `BLACKBOARD_CLIENT_ID`, `BLACKBOARD_CLIENT_SECRET`, and `BLACKBOARD_REDIRECT_URI`. Either set all four or leave all four empty. Providing them only validates the approved registration shape; live OAuth and university sync remain disabled until UWA approval and secure per-user token storage are implemented.

For local development with Python 3.12 or newer:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe bot.py
```

Run tests without Discord or university access:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```
