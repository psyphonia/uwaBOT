# uwa-india-discord-bot

Discord bot project for The University of Western Australia India students at the Mumbai and Chennai campuses.

Stage 4: `/ping` replies `Pong!`. `/today`, `/week`, `/due`, and `/course course_code:CITS1401` read fake academic data from SQLite. Each academic command accepts an optional Mumbai/Chennai campus choice; omit it to show both campuses.

Startup creates `uwa_india.db` beside `bot.py` and seeds missing sample records. Repeated starts preserve existing records and dates. `/today` covers the India calendar day; `/week` covers the next 7 days from now; `/due` excludes past deadlines. Dates are stored in UTC and displayed in IST. Course names, rooms, classes, and assignments are fictional. To regenerate fresh sample dates, stop the bot and move the development database aside before restarting.

The database uses Python's built-in `sqlite3`. A null campus on an academic record means both campuses, and matches either campus filter. Tests use temporary databases and never modify `uwa_india.db`.

## Setup

The MyCamu adapter is available for offline testing through `MyCamuIntegration`, with `MyCamuConfig`, an injected async transport, and an injected async authentication provider. It has no built-in network or login implementation and is not enabled by bot startup. Campus, subject-code mappings, source timezone, and timetable field mappings must be explicitly configured from confirmed data. Missing configuration and invalid responses fail clearly. Date-only holidays also require confirmed inclusive/exclusive end-date semantics. Attendance is normalized separately; announcements remain unsupported in this adapter.

The adapter does not write to SQLite. Campus holidays have no course association (`course_code=None`) and may be all-day events; a later authorized sync must provide suitable persistence for those records before importing them. Supported university authentication and live schema validation remain unresolved.

Requires Python 3.12 or newer. Prefer 3.12, then 3.13; use 3.14 only if neither is installed. From the project directory in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Change `-3.12` to the available version when needed. Skip environment creation if `.venv` already exists, and skip the copy command if `.env` already exists.

Set `DISCORD_TOKEN` in `.env` to your Discord application bot token. Keep `.env` private and never store UWA passwords.

Optionally set `REMINDER_CHANNEL_ID` in `.env` to a test text channel ID. Give the bot View Channel, Send Messages, and Embed Links permissions there. Missing or invalid channel configuration disables reminders with a warning; commands still work. The single test channel receives sample reminders from both campuses.

Reminders check every minute after Discord is ready. Catch-up windows are: more than 6 and at most 7 days remaining, more than 18 and at most 24 hours remaining, and more than 0 and at most 3 hours remaining. Older thresholds are skipped. Messages include the exact IST deadline, relative time, campus, and source link when available.

SQLite reserves each assignment/deadline/threshold before delivery and records `sent_at` only after success. Confirmed Discord rejections release the reservation for retry. Timeouts, uncertain server responses, interrupted sends, or delivery-recording failures remain pending to avoid duplicate sends. These entries are logged for manual review and never automatically resent. After a crash, a pending entry may represent either a delivered or an undelivered message; check the channel before clearing that specific pending reservation. Keep the database across restarts to preserve reminder history.


Install your Discord application in a test server with the `bot` and `applications.commands` OAuth2 scopes. No privileged intents or Administrator permission are needed. Allow the bot to view the test channel and send messages.

```powershell
.\.venv\Scripts\python.exe bot.py
```

Startup registers commands globally using [discord.py command synchronization](https://discordpy.readthedocs.io/en/stable/interactions/api.html#discord.app_commands.CommandTree.sync); Discord may take time to display them. After the ready log appears, run `/ping` in the test server. Stop with Ctrl+C.

Local tests (no Discord connection or real token required):

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
