# Recovery Companion

Personal Telegram bot for self-monitoring load and recovery: short morning and
evening check-ins, free-text notes, and a summary for the weekly session with a
psychologist. It is a self-observation tool and does not diagnose anything or
replace a doctor.

Design and milestones: [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md).

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

To run the bot locally, copy `.env.example` to `.env`, set `BOT_TOKEN`,
`OWNER_USER_ID` and `DATABASE_URL`, then:

```bash
.venv/bin/alembic upgrade head
.venv/bin/python -m recovery
```

After changing dependencies in `pyproject.toml`, regenerate the lock file:

```bash
uv pip compile pyproject.toml --python-version 3.12 -o requirements.lock
```

## Deployment (watering-system VM)

The bot runs as its own Compose project next to watering-system and uses that
project's Postgres container with a separate database and role.

1. **Shared network** (once):

   ```bash
   docker network create pg-shared
   ```

   In watering-system's `docker-compose.yml`, attach `timescaledb` to it:

   ```yaml
   services:
     timescaledb:
       networks: [default, pg-shared]
   networks:
     pg-shared:
       external: true
   ```

   While you're there, remove the `5432:5432` port mapping. Neither project
   needs it, and published Docker ports bypass the host firewall.

2. **Database and role** (once). Generate a password, put it in `.env` as
   `RECOVERY_DB_PASSWORD`, and run:

   ```bash
   docker exec -i plant-monitor-timescaledb-1 psql -U plantuser -d plantmonitor -v ON_ERROR_STOP=1 -v recovery_password="$RECOVERY_DB_PASSWORD" < scripts/create_db.sql
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

5. **Backups:** `scripts/backup-recovery-db.sh` (age-encrypted, `recovery/`
   prefix in the existing bucket). Usage and the restore steps are in the
   script header.
