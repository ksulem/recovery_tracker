# Recovery Companion

A personal Telegram bot for keeping track of your load and recovery: short
morning and evening check-ins on buttons, free-text notes, and a factual
summary to bring to sessions with your psychologist or therapist.

Each person runs **their own copy** with their own bot and their own database.
Nothing is shared between copies, and the bot answers only its owner.

> It is a self-observation tool. It does not diagnose anything and does not
> replace a doctor or a therapist.
>
> The bot talks in **Russian**.

## What it does

| Command | |
|---|---|
| `/start` | Setup: timezone, check-in times, optional session day and time |
| `/morning`, `/evening` | Short check-ins on buttons. Every question can be skipped; a check-in can be finished early or resumed later |
| `/now` | Note how you feel right now, outside the schedule |
| any text | Saved as a diary note, with a "📌 На сессию" button |
| `/discuss` | Add an item to discuss at your next session |
| `/session` | Factual summary since your last session, plus a Markdown file |
| `/week` | The last 7 days as a Markdown file |
| `/settings` | Times, timezone, session, quiet hours, reminders |
| `/privacy`, `/export_all`, `/delete_me` | What is stored; full JSON export; delete everything |

Check-in prompts arrive at your local times. You get at most one gentle
reminder, and none after "Не сейчас" or during quiet hours. The session summary
arrives 3 hours before the session. If a message suggests immediate danger, the
bot stops the normal flow and shows an emergency number.

## Run your own

### What you need

- **A machine that is always on**, with [Docker](https://docs.docker.com/engine/install/)
  and the Docker Compose plugin. Reminders only arrive while it runs. Any of these works:
  - a small Linux VPS (1 vCPU and 1 GB RAM is plenty);
  - a Raspberry Pi 4 or 5 with a 64-bit OS;
  - a home computer or NAS that stays on.

  The bot needs about 160 MB of RAM and Postgres about 100 MB. Disk use is under
  1 GB including the Docker images. No open ports or domain name are needed:
  the bot only makes outgoing connections to Telegram.
- **A Telegram account.**

### 1. Create your bot

1. In Telegram, open [@BotFather](https://t.me/BotFather), send `/newbot` and
   follow the steps. You'll get a **token** like `123456789:AAE...`. Keep it
   secret: anyone with it can act as your bot.
2. Find your **numeric user id**, for example by messaging
   [@userinfobot](https://t.me/userinfobot). The bot will answer only this id.

### 2. Get the code and configure it

```bash
git clone https://github.com/ksulem/recovery_tracker.git
cd recovery_tracker
cp .env.example .env
openssl rand -hex 16        # use the output as RECOVERY_DB_PASSWORD
```

Open `.env` and fill in:

| Variable | Value |
|---|---|
| `RECOVERY_BOT_TOKEN` | the token from @BotFather |
| `RECOVERY_OWNER_USER_ID` | your numeric Telegram id |
| `RECOVERY_DB_PASSWORD` | the random string from `openssl rand -hex 16`. Use only letters and digits: it becomes part of a database URL |
| `RECOVERY_EMERGENCY_NUMBER` | your local emergency number (default `112`) |
| `RECOVERY_DEFAULT_TIMEZONE` | optional; you choose your timezone in `/start` anyway |

### 3. Start it

```bash
docker compose up -d --build
```

This starts Postgres, creates the tables, then starts the bot. The first build
takes a few minutes. Check that everything is up:

```bash
docker compose ps              # db and bot "healthy", migrate "exited (0)"
docker compose logs -f bot     # should end with "Run polling for bot @your_bot"
```

### 4. Say hello

Open your bot in Telegram and send `/start`. Setup takes about a minute.

## Day-to-day operations

| Task | Command |
|---|---|
| See logs | `docker compose logs -f bot` |
| Stop / start | `docker compose stop` / `docker compose start` |
| Update to the latest version | `git pull && docker compose up -d --build` (database migrations run automatically) |
| Remove the containers, **keep** the data | `docker compose down` |
| Remove everything, **including all data** | `docker compose down -v` |

Logs never contain the text of your messages, only technical events and IDs.

### Backups

The simplest backup is `/export_all` in the bot: it sends you all your data as a
JSON file.

For automatic encrypted backups on a Linux server,
[`scripts/backup-recovery-db.sh`](scripts/backup-recovery-db.sh) dumps the
database and encrypts it with [age](https://github.com/FiloSottile/age) before
writing anything to disk:

1. On **your own computer** (not the server), create a key pair with
   `age-keygen -o recovery-backup-key.txt`. Store that file somewhere safe, such
   as your password manager: without it the backups can't be read. The command
   prints the **public** key (`age1...`).
2. On the server, install age (`sudo apt install age`) and test a backup:

   ```bash
   CONTAINER=recovery-db-1 LOCAL_DIR=$HOME/recovery-backups AGE_RECIPIENT=age1... \
     ./scripts/backup-recovery-db.sh
   ```

3. Run it nightly with cron (`crontab -e`), for example at 03:17:

   ```
   17 3 * * * CONTAINER=recovery-db-1 LOCAL_DIR=$HOME/recovery-backups AGE_RECIPIENT=age1... $HOME/recovery_tracker/scripts/backup-recovery-db.sh >> $HOME/recovery-backups/backup.log 2>&1
   ```

It keeps the last 7 dumps. Copy them off the server now and then, because a
backup that lives only on the same server doesn't survive losing it.

**Restore** (this replaces the current data):

```bash
age -d -i recovery-backup-key.txt recovery-<stamp>.dump.age > recovery.dump
docker compose stop bot
docker compose exec -T db dropdb -U recovery_app recovery
docker compose exec -T db createdb -U recovery_app -T template0 recovery
docker compose exec -T db pg_restore -U recovery_app -d recovery --no-owner < recovery.dump
docker compose start bot
```

### Changing the database password

`RECOVERY_DB_PASSWORD` is applied only when the database is created for the
first time. To change it later, change it in the database too, then restart:

```bash
docker compose exec db psql -U recovery_app -d recovery -c "ALTER ROLE recovery_app PASSWORD 'new-password'"
# put the same value into .env, then:
docker compose up -d
```

## Your data and privacy

- Everything is stored in **your** Postgres, on **your** machine. There is no
  central server, analytics or tracking.
- Messages travel through Telegram's servers, like any bot chat. Bot chats are
  not end-to-end encrypted.
- The bot ignores everyone except `RECOVERY_OWNER_USER_ID` and stores nothing
  about them.
- Whoever controls the machine can read the database. Run it somewhere you trust.
- `/export_all` gives you everything; `/delete_me` deletes everything.
- Planned features that would send text or voice to an external service (speech
  recognition, AI extraction) will ask for your consent first. Without consent,
  that data isn't sent.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| The bot doesn't answer at all | `RECOVERY_OWNER_USER_ID` is wrong: the bot silently ignores anyone else. Check the id, fix `.env`, run `docker compose up -d`. Also check `docker compose logs bot` |
| `Conflict: terminated by other getUpdates request` in the logs | The same token runs in two places, e.g. on your laptop and on the server. Stop one of them |
| The bot refuses to start: "RECOVERY_BOT_TOKEN has a webhook configured" | The token belongs to a bot that uses a webhook, probably a different bot of yours. Create a new bot for this. If you're sure the webhook is unused, remove it: `curl https://api.telegram.org/bot<TOKEN>/deleteWebhook` |
| `password authentication failed` or a database URL error | The password contains characters such as `#`, `?`, `@` or `/`, or was changed after the first start (see [Changing the database password](#changing-the-database-password)) |
| `RECOVERY_... is required` when starting | That variable is empty in `.env` |

## Already running Postgres on the same server?

You can reuse it instead of running a second one: see
[`docs/DEPLOY_SHARED_POSTGRES.md`](docs/DEPLOY_SHARED_POSTGRES.md).

## Development

Stack: Python 3.12 · aiogram 3 (long polling) · SQLAlchemy 2 async + asyncpg ·
Alembic · APScheduler · PostgreSQL 16. Design and milestones:
[`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md).

Feature work happens on `feature/*` branches, which are merged into `dev`.
`main` gets releases from `dev`.

You don't need Docker for development, only [uv](https://docs.astral.sh/uv/)
(on Windows, use `.venv\Scripts\...` instead of `.venv/bin/...`):

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.lock -e ".[dev]"
```

Checks:

```bash
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy
```

Tests run against a real PostgreSQL 16. Point `TEST_DATABASE_URL` at an empty
database you don't mind being wiped. Tests that need it are skipped when it
isn't set:

```bash
TEST_DATABASE_URL=postgresql+asyncpg://postgres@127.0.0.1:5432/recovery_test .venv/bin/pytest -q
```

To run the bot outside Docker, set `RECOVERY_DATABASE_URL` in `.env` (see
`.env.example`), then:

```bash
.venv/bin/alembic upgrade head
.venv/bin/python -m recovery
```

Use a separate test bot for this. Telegram allows only one running copy per
token, so a local copy and a server copy with the same token would conflict.

After changing dependencies in `pyproject.toml`, regenerate the lock file:

```bash
uv pip compile pyproject.toml --python-version 3.12 -o requirements.lock
```
