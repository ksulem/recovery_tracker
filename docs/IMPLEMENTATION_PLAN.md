# Recovery Companion — Implementation Plan

Source spec: `telegram_recovery_bot_spec.md` v0.1 (Telegram bot for load and recovery self-monitoring).
This plan turns the spec into an ordered set of milestones with concrete tasks, data model, and acceptance criteria.

> Product guardrails (from the spec) that every milestone must respect:
> no single averaged score, no "energy %", no date-based "ready to return", no diagnoses,
> no causal claims from one episode, skipping is always allowed and never treated as worsening.

---

## 0. Decisions (spec §16 open questions — confirmed)

| # | Question | Decision |
|---|----------|----------|
| 1 | Personal or multi-user? | **Personal bot, one owner.** Access gated by `OWNER_USER_ID` (same pattern as watering-system's `OwnerOnlyMiddleware`). Tables still carry `user_id` (settings live in `users`), but no multi-tenant features. |
| 2 | Hosting | **Same VM and Postgres server as watering-system** (Oracle Cloud `VM.Standard.E2.1.Micro`, `timescale/timescaledb:latest-pg16` container), **separate database `recovery` with its own role**. Details in §1.1. |
| 3 | LLM / STT provider | Behind interfaces (`LLMClient`, `SpeechToText`). Default LLM: Claude (structured output). Default STT: a Whisper-class API with good Russian. Final choice by the golden eval set (M3). |
| 4 | Audio / transcripts | Audio deleted right after successful transcription. Transcripts stored by default; `store_transcripts` setting can switch it off. |
| 5 | Check-in times | Defaults `09:30` / `21:00` local, configurable in `/settings`. |
| 6 | "I feel bad now" mode | Yes — ad-hoc check-in (`checkin_type = ad_hoc`), short 3-question flow. Separate from crisis handling. |
| 7 | Retention | Raw messages/transcripts: 180 days (configurable); structured data: until deleted by the owner. |
| 8 | Export | Markdown (M1), CSV (M4), full JSON dump (`/export_all`). |
| 9 | **Psychologist session brief** (new) | Summary of the period **since the previous session**, for the weekly session with the psychologist, plus a way to mark things "to discuss" during the week. See §3.1. |

---

## 1. Target architecture

One small Python process (aiogram 3) + the existing Postgres server. No microservices, no Redis.

**Deviation from spec §8.1:** no FastAPI, no Caddy for the MVP.
- The bot uses **long polling**, so it needs no inbound port, no nginx route and no certificate — and no changes to watering-system's nginx (which resolves `proxy_pass` hosts at startup, so a route to a stopped recovery container would stop nginx booting and take the watering bot down with it).
- A tiny aiohttp `/health` endpoint (aiohttp ships with aiogram — same approach as watering-system's bot) covers the Docker healthcheck.
- This also keeps memory low on a 1 GB VM that is already running eight services.
- FastAPI/webhook can be added later (e.g. for a Mini App) as a `/recovery/webhook` location using a runtime `resolver` in nginx so the two projects stay independent.

```
recovery_tracker/
├── pyproject.toml              # deps; ruff, mypy, pytest config
├── Dockerfile
├── docker-compose.yml          # self-contained: own Postgres + migrate + bot
├── docker-compose.shared.yml   # bot + migrate, joins watering's network as external
├── alembic/                    # migrations (own version table in the recovery DB)
├── scripts/
│   ├── create_db.sql           # one-time: role + database on the shared server
│   └── backup-recovery-db.sh   # nightly encrypted dump (adapted from watering-system)
├── src/recovery/
│   ├── main.py                 # entrypoint: bot polling, scheduler, /health
│   ├── config.py               # pydantic-settings (env vars, secrets)
│   ├── db/                     # engine, session, SQLAlchemy models, time helpers
│   ├── telegram/               # adapter: routers, keyboards, middlewares — NO business logic
│   │   ├── routers/            # start, settings, checkin, free_text, voice, reports, session, privacy
│   │   ├── keyboards.py
│   │   └── middlewares.py      # owner gate (outer middleware on ALL update types), db session
│   ├── conversation/           # orchestrator: flow definitions, state machine, follow-up limits
│   │   ├── flows/              # morning.py, evening.py, ad_hoc.py (declarative question lists)
│   │   ├── engine.py
│   │   └── fsm_storage.py      # Postgres-backed aiogram FSM storage
│   ├── ingestion/              # input normalizer, voice handler
│   ├── llm/                    # client interface, prompts, pydantic schemas, validation
│   ├── safety/                 # crisis detection + response
│   ├── analytics/              # SQL aggregates, rules, planning modes, insights
│   ├── reports/                # daily summary, weekly report, session brief, md/csv/pdf export, charts
│   ├── scheduler/              # cron jobs
│   ├── privacy/                # export, delete, consent, retention purge
│   └── texts/ru.py             # all user-facing strings in one place (tone review)
└── tests/
```

Key choices:

- **Dialog state in Postgres** (custom aiogram `BaseStorage` on a `dialog_state` table) and **every answer persisted immediately** (partial check-in rows). A restart or an abandoned check-in loses nothing; the check-in can be resumed.
- **Owner gate as an outer middleware on `dp.update`**, so messages, button callbacks and voice are all covered — not only `dp.message`.
- **Texts isolated in `texts/ru.py`** so tone (spec §2.6) can be reviewed in one place.
- **Lean dependencies** for the 1 GB box: aggregates in SQL rather than pandas; matplotlib imported lazily only when a chart is rendered; PDF via `fpdf2` (pure Python, Cyrillic via a bundled DejaVu font) rather than WeasyPrint.

### 1.1 Hosting on the watering-system server

Facts from `ksulem/watering-system` (`dev` branch): Postgres runs as the `timescaledb` service (pg16, `max_connections=40`, `shared_buffers=128MB`), DB `plantmonitor`, superuser `plantuser`, compose project `plant-monitor`. The VM has 1 OCPU / 1 GB RAM.

**Rule: recovery-tracker doesn't change anything that belongs to watering-system** — not its compose file, `.env`, database, network or backup bucket. The one agreed exception is the security fix below, made in the watering-system repo itself.

**Isolation on a shared VM:**
- Own Compose project (`name: recovery`), own directory and `.env` on the VM.
- All variables are `RECOVERY_`-prefixed. watering-system uses `BOT_TOKEN` / `OWNER_USER_ID` / `DATABASE_URL`, and docker compose lets exported shell variables override `.env`, so unprefixed names could silently pick up the watering bot's values.
- The bot refuses to start if its token has a webhook configured. That would mean it's the watering bot's token, and polling would require deleting that webhook.

**Database and role** (`scripts/create_db.sql`, run once as `plantuser`). It only creates a role and a database:

```sql
CREATE ROLE recovery_app LOGIN PASSWORD :'recovery_password';
CREATE DATABASE recovery OWNER recovery_app TEMPLATE template0 ENCODING 'UTF8';
REVOKE CONNECT ON DATABASE recovery FROM PUBLIC;
GRANT  CONNECT ON DATABASE recovery TO recovery_app;
ALTER DATABASE recovery SET timezone TO 'UTC';
```

- `template0` keeps the new DB free of the `timescaledb` extension (not needed here).
- `recovery_app` owns the DB and runs Alembic itself; it is not a superuser and has no privileges on `plantmonitor` tables.

**Networking:** the bot joins watering's existing network (`plant-monitor_default`, configurable via `RECOVERY_PG_NETWORK`) as an *external* network and reaches Postgres at `timescaledb:5432`. Joining a network doesn't modify it. No ports are published by the bot.

**Resources:**
- SQLAlchemy pool `pool_size=2, max_overflow=2` (server has 40 connections total).
- `mem_limit: 160m` for the bot. Before deploying, check headroom with `docker stats` / `free -m` on the VM — existing limits already add up to more than 1 GB, so measure real usage.
- Own log rotation (`json-file`, 10 MB × 3).

**Security notes for sensitive data on a shared server:**
- watering-system published `5432:5432` on all interfaces, and Docker-published ports bypass the host's iptables `INPUT` chain. **Fixed in watering-system** (`fix/db-port-loopback-only`, merged to its `dev`): now `127.0.0.1:5432:5432`. No watering service used the host port, and psql or an SSH tunnel on the VM still work.
- `plantuser` is a superuser and can read the recovery DB. Acceptable for a single-owner box.
- Restarting watering's `timescaledb` container also restarts the recovery database — the bot survives reconnects (pool `pre_ping=True`, waits for the DB on startup).

**Backups:** `scripts/backup-recovery-db.sh`, adapted from watering's script: `pg_dump -Fc -d recovery` as `recovery_app`, `pg_restore --list` integrity check, **`age` encryption** (this data is more sensitive than sensor readings), encrypted copies kept in `/var/backups/recovery`. Off-site upload only to a bucket set in `BUCKET` — not watering's bucket. Test a restore once.

### 1.2 Scheduler — cron jobs, not a per-minute tick

The first draft used a job running every minute that checked "is it time yet?". That design suits many users with different times (one job instead of thousands) and automatically catches up after downtime. **For one user it's unnecessary**, so the plan now uses exact cron jobs:

- APScheduler `CronTrigger(hour, minute, timezone=user.tz)` for: morning check-in, evening check-in, weekly report, session brief draft, daily retention purge, next-morning effect fill. APScheduler handles DST.
- Jobs are **rebuilt from the `users` table at startup and whenever `/settings` changes** — in-memory job store, nothing to keep in sync.
- **Catch-up on startup:** if today's check-in time passed less than 2 h ago and it wasn't sent, send it now (`misfire_grace_time` covers short hiccups).
- `scheduled_prompts` table (unique `(user_id, kind, local_date)`) remains as a guard against double sends and as data for "missed check-ins" in reports.
- At most **one gentle reminder** per missed check-in; skipping a day is fine.

---

## 2. Data model

### 2.1 Time convention (UTC + local)

Every row that records *when* something happened stores both UTC and local time:

| Column | Type | Meaning |
|--------|------|---------|
| `<event>_at_utc` | `TIMESTAMPTZ` | The instant, in UTC. The DB and every app connection run with `timezone=UTC`, so values also *display* as UTC. Suffix `_utc` makes it explicit in queries. |
| `tz_name` | `TEXT` | IANA timezone of the owner **at write time** (e.g. `Europe/Tbilisi`). |
| `local_ts` | `TIMESTAMP` (no tz) | Wall-clock time in `tz_name` — for "time of day" analysis. |
| `local_date` | `DATE` | Local calendar day — **all daily/weekly aggregates group by this**. |

- `local_ts` / `local_date` are derived from `*_at_utc` + `tz_name` — preferably as `STORED` generated columns (`created_at_utc AT TIME ZONE tz_name`), otherwise computed in the app and covered by a test.
- Snapshotting `tz_name` means history stays correct after a timezone change (travel): old days aren't silently shifted.
- Dates the owner talks about (e.g. a session date) are plain `DATE` in local terms.

### 2.2 Tables

Spec §9 tables, adjusted:

| Table | Notes |
|-------|-------|
| `users` | `telegram_id` unique, `timezone`, `morning_time`, `evening_time`, `reminders_enabled`, `quiet_hours_start/end`, `store_transcripts`, `session_weekday`, `session_time`, `session_brief_lead_hours`, `created_at_utc`. |
| `checkins` | time columns (§2.1), `checkin_type` (`morning/evening/ad_hoc`), `status` (`in_progress/completed/abandoned`), `completed_at_utc`, the five 0–10 scores, `interest_score` 0–5, `sleep_quality`, `comparison_to_morning`, `day_outlook`, `pleasant_energy`, `expected_heavy_load`, `notes`. All scores nullable — **skip = NULL, never 0**. |
| `load_events` | time columns + `occurred_at_utc` (nullable, only if known) + `occurred_part_of_day` (`morning/afternoon/evening/night/unknown`), `category`, `description`, `intensity`, `duration_minutes`, `source` (`button/llm/manual`), `confidence`, `message_id`, `checkin_id`. |
| `recovery_events` | time columns, `activity`, `enjoyment` 0–5, `immediate_effect`, `next_morning_effect` (filled from the next morning check-in, never guessed), `source`, `confidence`, `message_id`, `checkin_id`. |
| `messages` | time columns, `telegram_message_id`, `input_type`, `text`, `transcript`, `transcript_status`, `raw_file_reference` (cleared after audio deletion), `checkin_id`, `purge_after_utc`. |
| `insights` | as spec; `supporting_data JSONB` holds the IDs of the rows behind each observation. |

Added tables:

| Table | Purpose |
|-------|---------|
| `checkin_symptoms (checkin_id, symptom, source)` | Overload signs (multi-select), closed set: `need_isolation, no_motivation, irritability, low_concentration, physical_fatigue, sleep_difficulty, emotional_instability, pleasant_feels_heavy, staring_blankly, other`. |
| `dialog_state (user_id, state, data JSONB, updated_at_utc)` | FSM storage. |
| `scheduled_prompts (user_id, kind, local_date, sent_at_utc, status)` | Double-send guard; missed check-ins for reports. |
| `therapy_sessions (id, user_id, session_date, brief_period_start, brief_period_end, brief_sent_at_utc, post_session_note, created_at_utc)` | One row per psychologist session; the brief covers the period since the previous row. |
| `discussion_items (id, user_id, text, message_id, created_at_utc, local_date, status, session_id)` | Things marked "обсудить на сессии"; `status` = `open/discussed/dropped`; open items carry over to the next brief. |
| `llm_runs (id, message_id, provider, model, prompt_version, latency_ms, valid, error, created_at_utc)` | Observability and evals without raw text in logs. |
| `consents (user_id, kind, granted_at_utc, revoked_at_utc)` | LLM / STT data sharing (spec §12.8–10). |

Enum-like columns: `TEXT` + `CHECK` constraints (easier to evolve than PG enums with Alembic).

### 2.3 Analytical views (M4)

- `v_daily_state` — one row per `local_date`: morning/evening scores per dimension, comparison_to_morning, symptom count, load counts by category × intensity, recovery activities, check-in coverage.
- `v_next_morning` — `v_daily_state` joined to itself on `local_date + 1`: evening load vs next-morning state (spec §4.4).
- Both export cleanly to CSV/Parquet for ad-hoc exploration in a notebook or PySpark.

---

## 3. Milestones

Estimates assume one developer part-time (~10–15 h/week). Each milestone ends with a usable bot.

### M0 — Project skeleton & deployment path (≈ 3–4 days)

- [ ] Replace the Visual Studio `.gitignore` with a Python one (`.venv`, `__pycache__`, `.env`, `*.ogg`, …).
- [ ] `pyproject.toml` (Python 3.12, aiogram 3.x, SQLAlchemy 2 async + asyncpg, Alembic, APScheduler 3.x, pydantic-settings, structlog); ruff + mypy + pytest.
- [ ] `config.py`: `RECOVERY_BOT_TOKEN` (new bot from BotFather — not the watering bot), `RECOVERY_OWNER_USER_ID`, `RECOVERY_DATABASE_URL`, provider keys.
- [ ] Compose: `migrate` (one-shot Alembic) + `bot`, watering's network as external, `mem_limit`, log rotation. Local development uses a venv and a local Postgres (no Docker locally).
- [ ] One-time server setup: `scripts/create_db.sql`, check memory headroom.
- [ ] Structured logging with a **redaction filter** (never log message text / transcripts — spec §12.5); `/health`.
- [ ] CI (GitHub Actions): ruff, mypy, pytest with a Postgres 16 service container.

**Done when:** on the VM the bot answers `/ping` from the owner only, ignores anyone else, and Alembic has created its tables in the `recovery` DB.

### M1 — Check-ins, persistence, session capture (spec Stage 1 + §17) (≈ 2–3 weeks)

1. **Models + first migration**: `users`, `checkins`, `checkin_symptoms`, `messages`, `dialog_state`, `scheduled_prompts`, `therapy_sessions`, `discussion_items`, `consents`.
2. **`/start` onboarding** (≤ 1 minute): short explanation + "не медицинский сервис" disclaimer, timezone (buttons + city/offset fallback validated via `zoneinfo`), check-in times, psychologist session day/time (optional, can skip).
3. **`/settings`**: timezone, times, quiet hours, reminders, transcript storage, session day/time.
4. **Conversation engine**: flows declared as data (question id, type `scale_0_10 | scale_0_5 | choice | multi_choice | free_text`, options, optional flag). Handles:
   - "Пропустить" on every question, "Закончить" to stop early;
   - each answer saved immediately;
   - resume: an `in_progress` check-in for today offers "Продолжить / Начать заново / Не сейчас";
   - one active flow at a time; free text during a flow goes to the current check-in's `notes`.
5. **Evening check-in on buttons first** (spec §17.5), then morning, then ad-hoc:
   - *Morning* (§4.1): sleep (good/medium/poor/skip), physical 0–10, desire for something pleasant (no/a little/yes), how heavy the day looks, optional heavy-load expectation.
   - *Evening* (§4.3): compared to morning, what drained most (multi-select load categories), what helped (multi-select recovery + free text), pleasant energy left, note for tomorrow; an optional "подробнее" block with the five dimensions 0–10 and overload signs — the default path stays ≤ 2 min.
   - *Ad-hoc / "мне сейчас плохо"*: overall state, overload signs, free text.
6. **Free text outside flows**: saved to `messages`, attached to today's ad-hoc record, acknowledged gently. (LLM extraction in M3.)
7. **"Обсудить на сессии"**: `/discuss <текст>` and a "📌 На сессию" button under acknowledgements and summaries → `discussion_items`. Starts collecting material from day one.
8. **Scheduler** (§1.2): cron jobs, startup catch-up, one reminder max, quiet hours respected.
9. **Safety handler v1** (ships with the first free-text input): keyword/regex detector for imminent-danger phrases in Russian → stop the normal flow, send a static message (local emergency number + "обратись к близкому человеку"), log only the fact of the event.
10. **`/week` Markdown export** (§17.8): last 7 days, each dimension shown separately, sleep, loads, recovery, notes; "нет записи" for missing days.
11. **Session brief v0** (`/session`): the same factual digest but for the period since the last session, plus the open "to discuss" items. Enriched in M4.
12. **`/export_all` (JSON) and `/delete_me`** (with confirmation).

**Done when:** a full week can be logged with buttons; a restart mid-check-in loses nothing and offers resume; skipping never blocks; `/week` and `/session` show exactly the stored data; morning + evening take < 3 min combined.

### M2 — Voice (spec Stage 2) (≈ 1 week)

- [ ] `SpeechToText` interface + provider (language `ru`), timeout, one retry.
- [ ] Voice handler: `file_id` → download to a temp dir → transcribe → **delete audio immediately** (on failure: one retry, then delete and ask to resend or type).
- [ ] Show the transcript with "Верно / Исправить / Отменить"; only confirmed text continues.
- [ ] Input normalizer: `text | voice | button` → `NormalizedInput(user_id, text, input_type, message_id, received_at_utc, checkin_id?)`.
- [ ] Consent on first voice message: what goes to the STT provider and its retention terms. No consent → voice not processed.
- [ ] Voice works for free-text check-in questions and for `/discuss`.

**Done when:** a Russian voice note becomes a confirmed note on the right check-in; no audio files remain on disk; logs contain no transcript text.

### M3 — LLM extraction & follow-ups (spec Stage 3, §5, §10, §11) (≈ 2 weeks)

1. **Pydantic schemas** mirroring §10 with closed enums, `null` for unknown, `confidence` per item, plus `safety_concern: bool`.
2. **Versioned prompt** from §11 (`prompt_version` in `llm_runs`). Compact context: current message, today's answers so far, follow-ups already asked today, 7-day per-dimension medians — not the full history.
3. **Validation layer**: parse → Pydantic → business rules (no `duration_minutes` unless the text contains a duration; categories in enum; ≤ 1 follow-up) → on failure retry once with the error, then fall back to storing raw text only.
4. **Confirm before saving**: show extracted events as a short list "Сохранить / Поправить / Не сохранять"; only confirmed items go to `load_events` / `recovery_events` / `checkin_symptoms` with `source='llm'`.
5. **Follow-up policy** decided by the orchestrator, not the LLM: ask only if the LLM says `needed`, the limit isn't reached (≤ 2 per check-in, ≤ 1 per message) and it wasn't asked today; always offer "не знаю / пропустить".
6. **Safety**: LLM `safety_concern` OR the M1 keyword detector triggers the crisis response.
7. **Golden eval set**: 30–50 realistic Russian diary messages with expected output. `pytest -m eval` reports category precision/recall, invented-field rate (must be 0), follow-up rate. Used to pick provider/model and to gate prompt changes.
8. **Consent** for LLM processing; without it free text is stored but not analysed.

**Done when:** the spec example ("три часа сложной работы, неприятный разговор…") yields work/high/180 min + a social/emotional event + symptoms with at most one follow-up; the eval set passes; invalid LLM output never reaches the DB.

### M4 — Reports, analytics, psychologist brief (spec Stage 4, §6, §7) (≈ 2–3 weeks)

1. **Views** `v_daily_state`, `v_next_morning` (§2.3).
2. **Daily evening summary**: built from a structured fact list; the LLM may only rephrase, never add facts. Can be switched off.
3. **Weekly report** (`/report`, optional Sunday-evening delivery): per-dimension dynamics (never averaged), interest trend, sleep, top load categories, recovery activities followed by "better", next-morning worsening after heavy days, recurring early signs, **data limitations** (days with records, missed check-ins, "данных пока недостаточно" below thresholds).
4. **Charts**: matplotlib small multiples, one panel per dimension, gaps for missing days (no interpolation).
5. **Planning-mode rules** (§6.1: stable / limited resource / pronounced exhaustion): deterministic rules over the last 1–3 days combining ratings, symptoms and dynamics; stored as `insights` with supporting row IDs; shown as a planning hint, never a diagnosis, never from one number.
6. **Early-signs nudge** (§6.2): several early signs in 2–3 days → one gentle suggestion to review what can be postponed, at most once every few days.
7. **Pattern guardrails**: ≥ 3 supporting occurrences, confidence from counts, wording only "возможно, связано" / "похоже на повторяющуюся закономерность".
8. **Psychologist session brief v1** — see §3.1.
9. **CSV export** of `v_daily_state`.

**Done when:** report numbers are reproducible from SQL on fixture data; with < 4 days of data reports say so instead of drawing conclusions; the session brief fits on 1–2 pages.

### 3.1 Psychologist session brief (detail)

**Goal:** arrive at each weekly session with a short, factual overview of the period instead of reconstructing the week from memory.

**Period:** from the previous session (`therapy_sessions`) to now; 7 days for the first one. Not a fixed calendar week, so a moved session still covers the right days.

**During the week:** `/discuss` and the "📌 На сессию" button collect `discussion_items` (text or voice). Open items carry over until marked discussed or dropped.

**Delivery:** a draft arrives `session_brief_lead_hours` (default 3 h) before the session time, or on demand via `/session`. The owner can drop sections or items with buttons before generating the final version. Output: a Telegram message (readable on the phone) + PDF/Markdown file to show or forward. It is only ever sent to the owner's chat — the owner decides what to share.

**Content (in this order):**
1. Period and data coverage (days with records / total, missed check-ins).
2. **What I want to discuss** — open `discussion_items`, including carried-over ones.
3. State by dimension: per-dimension range and direction over the period + chart; no combined score.
4. Interest/hobby trend and sleep.
5. Main load sources (by category and intensity) and the hardest days, with the owner's own short notes for those days.
6. What helped (recovery activities with immediate / next-morning effect counts).
7. Early signs and planning-mode days (how many stable / limited / exhausted days).
8. Possible patterns — only with ≥ 3 occurrences, with confidence and "это не причинная связь".
9. Since the last session — the owner's post-session note, if any.

**After the session:** an optional prompt "Что хочешь сохранить с сессии?" → `post_session_note`; mark items as discussed. The next brief shows that note in section 9.

**Rules:** observational language, no diagnoses, every number traceable to rows; LLM only rephrases (template-only mode available without LLM consent).

### M5 — Personalisation (spec Stage 5) (later, ≈ 2–3 weeks, after ≥ 3–4 weeks of real use)

- Personal baselines (rolling medians per dimension) and "notable change vs your usual level" triggers for follow-ups (§5).
- Individual early-sign profile: symptoms that most often preceded exhausted days.
- Recovery activities ranked by next-morning effect, with sample sizes.
- Adaptive question frequency (shorter morning check-in on stable streaks).
- Gradual-return assistant (§6.5): suggest increasing load in **one** direction at a time once recent days are stable and small activities didn't worsen the next morning — never date-based.
- Two-week experiment mode (§7), framed as observation; "no improvement is also a result".

---

## 4. Cross-cutting work

### Privacy & security (spec §12)
- Owner-only gate on all update types; long polling (no inbound endpoint).
- Separate DB and non-superuser role; `RECOVERY_`-prefixed config; webhook guard; watering's Postgres published on loopback only (§1.1).
- Analytics tables reference the internal `user_id` UUID, not `telegram_id`.
- Log redaction filter + a test asserting no message text appears in captured logs.
- Daily retention job: purge `messages.text/transcript` past `purge_after_utc`; verify no audio files remain.
- `/privacy`: what is stored, what goes to which provider, how to export/delete.
- Encrypted nightly backups of the `recovery` DB with a tested restore (§1.1).

### Testing strategy
- **Unit**: flow definitions, engine (skip/resume/abandon), rules, report aggregations, brief period selection, validation layer, UTC → local conversion.
- **Integration**: Postgres 16 (CI service container); aiogram updates through the dispatcher with a fake bot session; fake `LLMClient` / `SpeechToText`.
- **Time**: frozen time (`time-machine`) across timezones, DST changes and a mid-week timezone change; scheduler catch-up after a simulated restart; no double sends.
- **LLM evals**: golden set (M3), run manually/nightly, not on every PR.
- **Tone review**: checklist over `texts/ru.py` — no discipline/productivity wording, every question skippable.

### Observability
- structlog JSON logs (IDs only), Sentry or similar with `before_send` scrubbing message bodies, `llm_runs` for latency/validity.

---

## 5. Mapping to MVP success criteria (spec §13)

| Criterion | Where it is enforced |
|-----------|----------------------|
| Daily survey ≤ 3 min | Short default paths, optional "подробнее" block (M1); timed manual test |
| Voice diary | M2 |
| No data loss on restart | Per-answer persistence + Postgres FSM + startup catch-up (M1) |
| Skips never block | Skip button on every question, engine tests (M1) |
| Bot doesn't invent data | NULL-for-skip, LLM validation + confirmation, invented-field eval = 0 (M3) |
| Weekly report reflects real records | SQL-reproducible aggregates + fixture tests (M4) |
| No guilt / pressure | One reminder max, tone checklist, no streaks or "discipline" scores |

## 6. Out of scope for v1 (spec §14)
Microservices, energy-%, auto-diagnosis, recovery date prediction, ML prediction models, wearables, many notifications, auto-cancelling meetings.

## 7. First sprint backlog (≈ 2 weeks, = M0 + start of M1)

1. Python `.gitignore`, `pyproject.toml`, Dockerfile, compose, CI.
2. Server prep: `create_db.sql`, check memory headroom.
3. Config, logging with redaction, `/health`, owner gate.
4. SQLAlchemy models with the UTC/local time convention + initial Alembic migration.
5. `/start` with timezone, check-in times, session day.
6. Conversation engine with skip/finish/resume + Postgres FSM storage.
7. Evening check-in on buttons, saved per answer.
8. Free text + `/discuss` capture + safety keyword handler.
9. Cron scheduler with startup catch-up.
10. `/week` and `/session` (v0) Markdown export; deploy to the VM and smoke-test.
