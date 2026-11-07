# Recovery Companion

Personal Telegram bot for self-monitoring load and recovery: short morning and
evening check-ins, free-text notes, and a summary for the weekly session with a
psychologist. It is a self-observation tool and does not diagnose anything or
replace a doctor.

Design and milestones: [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md).

## What it does (M1)

| Command | |
|---|---|
| `/start` | Onboarding: timezone, check-in times, optional psychologist session day |
| `/morning`, `/evening` | Short check-ins on buttons. Every question can be skipped, a check-in can be finished early or resumed |
| `/now` | Note your state outside the schedule |
| any text | Saved as a diary note, with a "📌 На сессию" button |
| `/discuss` | Add an item to the list for the next session |
| `/session` | Factual summary since the last session, plus a Markdown file |
| `/week` | Last 7 days as a Markdown file |
| `/settings` | Times, timezone, session, quiet hours, reminders |
| `/privacy`, `/export_all`, `/delete_me` | What is stored; full JSON export; delete everything |

Scheduled prompts arrive at your local times. You get at most one gentle
reminder, and none after "Не сейчас". The session summary arrives 3 hours
before the session. A message that suggests immediate danger stops the
normal flow and shows an emergency number (`RECOVERY_EMERGENCY_NUMBER`,
default 112).

## Stack

Python 3.12 · aiogram 3 (long polling) · SQLAlchemy 2 async + asyncpg · Alembic ·
APScheduler · PostgreSQL 16 (shared with watering-system, separate database).

## Branches

Feature work happens on `feature/*` branches, which are merged into `dev`.
`main` gets releases from `dev`.

## Local development

Everything runs in a virtual environment. You don't need Docker locally.

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.lock -e ".[dev]"
```

Checks:

```bash
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy
```

Tests run against a real PostgreSQL 16. Point `TEST_DATABASE_URL` at an empty
database; tests that need it are skipped when it isn't set:

```bash
TEST_DATABASE_URL=postgresql+asyncpg://postgres@127.0.0.1:5432/recovery_test .venv/bin/pytest -q
```

To run the bot locally, copy `.env.example` to `.env`, set `RECOVERY_BOT_TOKEN`,
`RECOVERY_OWNER_USER_ID` and `RECOVERY_DATABASE_URL`, then:

```bash
.venv/bin/alembic upgrade head
.venv/bin/python -m recovery
```

After changing dependencies in `pyproject.toml`, regenerate the lock file:

```bash
uv pip compile pyproject.toml --python-version 3.12 -o requirements.lock
```

## Deployment (VM shared with watering-system)

The bot runs as its own Compose project (`recovery`) in its own directory, for
example `/opt/recovery`, with its own `.env`. It reuses watering-system's
Postgres container through a separate database and role. **Nothing that
belongs to watering-system is changed:** not its compose file, `.env`,
database, network or backup bucket.

Two safeguards keep the projects apart:

- Every variable is prefixed with `RECOVERY_`. watering-system uses
  `BOT_TOKEN`, `OWNER_USER_ID`, `DATABASE_URL`, and docker compose lets
  exported shell variables override `.env`, so unprefixed names could pick up
  the watering bot's values.
- The bot refuses to start if its token has a webhook configured. That would
  mean it's the watering bot's token, and polling with it would delete that
  bot's webhook.

1. **Find the watering network.** The bot joins it as an external network.
   Joining doesn't modify it.

   ```bash
   docker network ls | grep plant-monitor
   ```

   If the name isn't `plant-monitor_default`, set `RECOVERY_PG_NETWORK` in `.env`.

2. **Database and role** (once). Generate a password, put it in `.env` as
   `RECOVERY_DB_PASSWORD`, and run the script below. It only creates the
   `recovery_app` role and the `recovery` database:

   ```bash
   docker exec -i plant-monitor-timescaledb-1 psql -U plantuser -d postgres -v ON_ERROR_STOP=1 -v recovery_password="$RECOVERY_DB_PASSWORD" < scripts/create_db.sql
   ```

3. **Memory check.** The VM has 1 GB. Look at real usage before adding the bot
   (`mem_limit: 160m`):

   ```bash
   docker stats --no-stream && free -m
   ```

4. **Start.** This runs migrations, then starts the bot:

   ```bash
   docker compose up -d --build
   ```

5. **Backups:** `scripts/backup-recovery-db.sh` keeps age-encrypted dumps in
   `/var/backups/recovery`. It uploads off-site only to a bucket you set in
   `BUCKET`, never watering's. Usage and the restore steps are in the script
   header.
