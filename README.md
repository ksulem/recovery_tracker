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
| any text | Saved as a diary note, with "📌 На сессию" and "🅿️ На потом" buttons |
| `/later` | Park a thought for later; without text, list parked thoughts and review them one at a time |
| `/discuss` | Add an item to discuss at your next session |
| `/session` | Factual summary since your last session, plus a Markdown file |
| `/week` | The last 7 days as a Markdown file |
| `/settings` | Times, timezone, session, quiet hours, reminders, the battery, bedtime, evening walk, optional question sets |
| `/privacy`, `/export_all`, `/delete_me` | What is stored; full JSON export; delete everything |

Check-in prompts arrive at your local times. You get at most one gentle
reminder, and none after "Не сейчас" or during quiet hours. The session summary
arrives 3 hours before the session. If a message suggests immediate danger, the
bot stops the normal flow and shows an emergency number.

### What the reports line up

`/week` and the session brief (`/session`) contain, besides each day's records:

- **Вечер → следующее утро:** each evening (charge, obligations, episodes,
  what kept the mind busy, pressure) next to the night and the following
  morning (sleep, falling asleep, what happened before sleep, charge), plus
  how often the morning charge was lower than the evening one.
- **Эпизоды за период:** per kind of episode, how many times it gave, took,
  did both or neither. For "both", which resources it gave and took; split by
  "по желанию / по необходимости".
- **Гипотезы:** the week-2 hypotheses as questions, each with the matching
  facts and counts side by side.

These are counts, never averages, and never "X caused Y". With fewer than
three cases the line says *данных недостаточно*.

### Evening wind-down and parked thoughts

For when the day review drifts towards bedtime and thoughts keep going late
into the night. The goal is to finish the review **at least an hour before
sleep** and to write down whatever comes up, so it can wait until later.
Late in the evening the bot deliberately stays quiet: one-line replies, no
questions, no reminders.

Set two things in `/settings`: **🌙 Время сна** (e.g. 23:00) and
**🐕 Вечерняя прогулка** (e.g. 18:45). Both are optional; without them this
behaviour is off.

| When | What happens |
|---|---|
| Before the evening walk | Only if something is parked: "можно взять с собой одну мысль, а можно просто погулять". One thought, only if you ask for it, and nothing to decide during the walk. Never in the morning |
| Evening check-in | Ends by closing the day: what to do with the walk thought (обдумано · пусть полежит · на сессию · неактуально), "что-то ещё отложить на завтра?", "День закрыт 🌙" |
| From an hour before bedtime until morning | No reminders or offers. Any message is parked with a one-line reply. `/evening` offers a short version (battery + what to park); after midnight it only offers to park |

- `/later` parks a thought (several, one per line). Without text it lists
  parked thoughts and lets you review them one at a time.
- If a thought is postponed a third time, the bot offers once to move it to the
  list for your session. "Пусть полежит" is always a fine answer.
- If the evening check-in is less than an hour before bedtime, `/settings`
  offers to move it earlier. With a 23:00 bedtime it suggests 20:45, and the
  evening reminder comes 45 minutes later, at 21:30.
- `/week` and `/session` count how many evening check-ins finished an hour or
  more before sleep, and how many thoughts were parked and dealt with. Counts
  only: no streaks, no "success rate".

### The battery

Based on a common self-care exercise: picture a phone-like battery, set part of
its charge aside as a **reserve** in your favourite colour, notice what drains and
what recharges it, and on a low morning plan the day around the charge you have
instead of blaming yourself.

- Morning, evening and `/now` ask "Заряд батарейки сейчас?" with buttons
  0%–100% in steps of 10. The answer is shown as a bar in your colour, with the
  reserve marked by `|`, for example `🟪🟪🟪|🟪⬜⬜⬜⬜⬜⬜ 40%`.
- If the charge is at or below your reserve, the bot adds one gentle note
  (for example, that the day can be lived on the charge you have).
- `/week` and `/session` list each day's charges and count how many mornings
  were at or below the reserve. Counts only, never averages.
- `/settings → 🔋 Батарейка`: turn the question on or off, set the reserve
  (10–50% or none) and the colour.

The charge is always your own estimate. The bot never computes a charge or
assigns a "cost" to activities.

## What a check-in looks like

Answers are buttons. Every question has **Пропустить** (skip) and **Закончить**
(finish early), and each answer is saved immediately. If you come back to an
unfinished check-in the same day, the bot offers **Продолжить / Начать заново /
Не сейчас** (continue / start over / not now).

Every list of options also has **Другое — опишу своими словами**. Picking it
brings one short follow-up («Что именно?»), and the answer appears in the
reports as «другое: «…»». This covers the day's episodes, what an episode
gave or took, what happened before sleep, signs of overload, pressure on
oneself and «трудно по другой причине» when falling asleep.

Questions marked 🧩 belong to optional sets that can be switched off in
`/settings → 🧩 Дополнительные вопросы`: **Как получилось заснуть** (morning)
and **Вопросы для проверки гипотез** (evening).

**Morning** (`/morning`, about a minute)

| # | Question | Answer |
|---|---|---|
| 1 | Как ты сегодня после сна? *(How are you after sleep?)* | Хорошо · Средне · Плохо |
| 2 | 🧩 Как получилось заснуть? *(How did falling asleep go?)* | Легко · Долго крутились мысли · Трудно по другой причине |
| 3 | 🧩 Было ли перед сном что-то из этого? *(Before sleep, was there any of this?)* | several of: Видео, сериалы · Покупки или каталоги · Соцсети · or Ничего из этого |
| 4 | Заряд твоей батарейки сейчас, после сна? *(Battery charge after sleep?)* | 0% … 100% |
| 5 | Насколько сейчас ощущается физическая энергия? *(Physical energy right now?)* | 0 … 10 |
| 6 | Есть ли желание заняться чем-нибудь приятным? *(Do you feel like doing something pleasant?)* | Нет · Немного · Да |
| 7 | Насколько тяжело представлять сегодняшний день? *(How hard does today look?)* | Не тяжело · Терпимо · Очень тяжело |
| 8 | Ожидается сегодня что-то особенно энергозатратное? *(Anything especially draining today?)* | free text |

**Evening** (`/evening`, about 2–3 minutes, plus an optional detailed block)

| # | Question | Answer |
|---|---|---|
| 1 | Как ты сейчас по сравнению с утром? *(Compared to the morning?)* | Лучше · Так же · Хуже |
| 2 | Заряд батарейки сейчас? *(Battery charge now?)* | 0% … 100% |
| 3 | 🧩 Сколько сегодня было обязательств? *(How many obligations today?)* | Не было · Немного · Много |
| 4 | Что было заметного сегодня? *(What was notable today?)* — the day's episodes | several of: Общение с близкими · Общение по работе или обязательное · Много людей, новые люди · Работа, умственные задачи · Быт · Прогулка, движение · Хобби · Сериалы, книги, игры · Побыть в одиночестве · Сон, отдых · Переживания · Решения · Требования к себе · or Ничего особенного |
| ↳ | *For each of the first three picked:* «… — как это было?» *(How was it?)* | Дало силы · Забрало силы · И то, и другое · Почти не повлияло |
| ↳ | *Only for "И то, и другое":* «…: что дало?» and «…: что забрало?» | several of: Эмоционально · Телу · Общение · Голове |
| ↳ | 🧩 *Social contact and chores:* «… — по своему желанию или по необходимости?» | По желанию · По необходимости |
| 5 | 🧩 Есть ли что-то, о чём постоянно думаешь, или ситуация, которая продолжает занимать? *(Anything you keep thinking about?)* | Нет · Немного · Да, сильно; if yes, an optional short note with a "📌 На сессию" button |
| 6 | Остались ли силы на что-нибудь приятное? *(Any energy left for something pleasant?)* | Да, и получилось · Хотелось, но не было сил · Ничего не хотелось · Не знаю |
| 7 | 🧩 *Every other evening:* Было ли сегодня что-то из этого? *(Any of this today?)* | several of: Казалось, что надо быть продуктивнее · Чувство вины за отдых · Казалось, что недостаточно стараюсь восстановиться · Другое — опишу своими словами (then «Что именно?») · or Ничего из этого |
| 8 | Хочешь отметить состояние подробнее? *(Add more detail? About a minute)* | Да · Нет |
| ↳ | *If yes:* physical, emotional, social and cognitive resource, overall wellbeing *(each 0–10)*; interest in things *(0–5, from "ничего не хочется" to "хочется нового")*; signs of overload *(e.g. "Хочется, чтобы никто не трогал", "Раздражительность", "Приятное ощущается как нагрузка")* | |
| 9 | Есть ли что-то важное, что стоит учесть завтра? *(Anything to keep in mind for tomorrow?)* | free text |
| 10 | *Only if a thought was taken on the walk:* Мысль с прогулки — что с ней? *(The walk thought: what about it?)* | Обдумано · Пусть полежит · На сессию · Неактуально |
| 11 | Что-то ещё крутится в голове? Можно отложить на завтра *(Anything else on your mind? Park it until tomorrow)* | free text, one thought per line · Ничего |

Day episodes replace the earlier two whole-day lists ("что забирало силы" /
"что помогало"). When the same thing showed up in both, there was no way to
tell what it gave and what it took. Now each episode says it: for example,
"общение с близкими — и то, и другое (дало эмоционально; забрало силы тела;
по желанию)". Older records keep showing as they were stored.

**Right now** (`/now`: "Отметить состояние сейчас", for when you feel bad between check-ins)

| # | Question | Answer |
|---|---|---|
| 1 | Заряд батарейки сейчас? *(Battery charge now?)* | 0% … 100% |
| 2 | Как ты сейчас в целом? *(How are you overall?)* | 0 (очень плохо) … 10 (хорошо) |
| 3 | Что сейчас происходит? *(What's going on?)* | several signs of overload, as above |
| 4 | Если хочется — опиши словами, что происходит. *(Describe it in words, if you like)* | free text |

Diagrams of every flow — which question comes after which, and what each one
depends on (settings, earlier answers, every other day) — are in
[`docs/FLOWS.md`](docs/FLOWS.md). They are generated from the code, so they
always match what the bot asks.

The questions are plain data in
[`src/recovery/conversation/flows.py`](src/recovery/conversation/flows.py), and
their wording is in [`src/recovery/texts/ru.py`](src/recovery/texts/ru.py).
Changing a question or adding an option needs no change to the logic.

## Run your own

### What you need

- **A machine that is always on**, with [Docker](https://docs.docker.com/engine/install/)
  and the Docker Compose plugin. Reminders only arrive while it runs. Any of these works:
  - a small Linux VPS (1 vCPU and 1 GB RAM is plenty);
  - a Raspberry Pi 4 or 5 with a 64-bit OS;
  - a home computer or NAS that stays on.

  The bot needs about 200 MB of RAM and Postgres about 100 MB. Disk use is under
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
`main` gets releases from `dev`. GitHub Actions runs CI on `dev` and on pull
requests, and deploys every push to `main` to the server: see
[`docs/CI_CD.md`](docs/CI_CD.md).

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
