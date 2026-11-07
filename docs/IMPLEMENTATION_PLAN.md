# Recovery Companion — Implementation Plan

Source spec: `telegram_recovery_bot_spec.md` v0.1 (Telegram bot for load and recovery self-monitoring).
This plan turns the spec into an ordered set of milestones with concrete tasks, data model, and acceptance criteria.

> Product guardrails (from the spec) that every milestone must respect:
> no single averaged score, no "energy %" , no date-based "ready to return", no diagnoses,
> no causal claims from one episode, skipping is always allowed and never treated as worsening.

---

## 0. Decisions on the open questions (spec §16)

Defaults chosen so work can start; each is cheap to revisit.

| # | Question | MVP decision | Why |
|---|----------|--------------|-----|
| 1 | Personal or multi-user? | **Multi-user schema, single-user deployment.** Access gated by `ALLOWED_TELEGRAM_IDS` allowlist. | Spec §12.1 forbids accepting data from strangers; `user_id` everywhere costs nothing and avoids a later migration. |
| 2 | Hosting | **Small VPS + Docker Compose** (app + Postgres + Caddy for HTTPS). | Cheapest, full control over sensitive data; Azure/other containers stay possible since everything is containerised. |
| 3 | LLM / STT provider | **Behind interfaces** (`LLMClient`, `SpeechToText`). Default LLM: Claude (structured output via tool use / JSON schema). Default STT: a Whisper-class API with good Russian. | Spec says choose by Russian quality, price, retention policy — interface lets us A/B on a golden set (M3). |
| 4 | Store raw transcripts? | **Yes by default, user can switch off** (`store_transcripts` setting). Audio deleted right after successful transcription. | Transcripts are the audit trail for "bot must not invent data". |
| 5 | Check-in times | Defaults `09:30` / `21:00` local, configurable in `/settings`. | |
| 6 | "I feel bad now" mode | **Yes — `/sos`-style ad-hoc check-in** (`checkin_type = ad_hoc`), short 3-question flow. Separate from crisis safety handling. | Cheap, matches "state over schedule". |
| 7 | Retention | Raw messages/transcripts: 180 days (configurable); structured check-ins/events: until user deletes. Audio: deleted immediately. | |
| 8 | Export | **Markdown (M1) + CSV (M4)**; JSON full dump for `/export_all`. | |

---

## 1. Target architecture (modular monolith)

One Python process (FastAPI + aiogram) plus Postgres. No microservices, no Redis in MVP.

```
recovery_tracker/
├── pyproject.toml              # uv/poetry; ruff, mypy, pytest config
├── Dockerfile
├── docker-compose.yml          # app, postgres, caddy
├── alembic/                    # migrations
├── src/recovery/
│   ├── main.py                 # FastAPI app factory, lifespan (bot, scheduler)
│   ├── config.py               # pydantic-settings (env vars, secrets)
│   ├── db/                     # engine, session, SQLAlchemy models
│   ├── telegram/               # adapter: routers, keyboards, middlewares — NO business logic
│   │   ├── routers/            # start, settings, checkin, free_text, voice, reports, privacy
│   │   ├── keyboards.py
│   │   └── middlewares.py      # allowlist, user loading, db session
│   ├── conversation/           # orchestrator: flow definitions, state machine, follow-up limits
│   │   ├── flows/              # morning.py, evening.py, ad_hoc.py (declarative question lists)
│   │   ├── engine.py
│   │   └── fsm_storage.py      # Postgres-backed aiogram FSM storage
│   ├── ingestion/              # input normalizer, voice handler
│   ├── llm/                    # client interface, prompts, pydantic schemas, validation
│   ├── safety/                 # crisis detection + response
│   ├── analytics/              # aggregates, rules, state modes, insights
│   ├── reports/                # daily summary, weekly report, markdown/csv export, charts
│   ├── scheduler/              # due-prompt tick job
│   ├── privacy/                # export, delete, consent
│   └── texts/ru.py             # all user-facing strings in one place (tone review)
└── tests/
```

Key architectural choices:

- **Webhook in prod, long polling in dev.** FastAPI exposes `POST /telegram/webhook` validated with `X-Telegram-Bot-Api-Secret-Token`. A `BOT_MODE=polling|webhook` switch keeps local dev simple.
- **Dialog state in Postgres**, not memory (custom aiogram `BaseStorage` on a `dialog_state` table). Satisfies "data is not lost on restart" and "check-in can be resumed".
- **Every answer is persisted immediately** (partial check-in rows), not at the end of the flow. Abandoning a check-in loses nothing.
- **Scheduler = one tick job** (APScheduler `IntervalTrigger`, every minute) that queries users whose local time passed `morning_time`/`evening_time` and who have no `scheduled_prompts` row for `(user, kind, local_date)`. Insert with a unique constraint → idempotent, survives restarts, no per-user job bookkeeping.
- **Sync heavy work off the event loop**: STT and LLM calls are async HTTP; if latency hurts, add an in-process `asyncio` task queue first; ARQ/Redis only if really needed (spec §8).
- **Texts isolated in `texts/ru.py`** so tone (spec §2.6) can be reviewed in one file.

---

## 2. Data model

Spec §9 tables plus a few additions needed for correctness. All timestamps `TIMESTAMPTZ` (UTC); plus a **`local_date DATE`** computed at write time from the user's timezone — every daily/weekly aggregate groups by it (avoids midnight/timezone bugs).

### 2.1 Spec tables (with adjustments)

| Table | Adjustments vs spec |
|-------|---------------------|
| `users` | + `quiet_hours_start/end`, `store_transcripts BOOL`, `llm_consent_at`, `stt_consent_at`, `locale`, `deleted_at`. `telegram_id` unique. |
| `checkins` | + `local_date`, `status` (`in_progress/completed/abandoned`), `completed_at`, `day_outlook` (`easy/tolerable/very_hard/skipped`), `pleasant_energy` (evening Q4 enum), `expected_heavy_load TEXT`. All scores nullable; **skip = NULL**, never 0. |
| `load_events` | + `local_date`, `source` (`button/llm/manual`), `confidence` (`low/medium/high`), `message_id` FK, `confirmed BOOL`. |
| `recovery_events` | + `local_date`, `source`, `confidence`, `message_id`. `next_morning_effect` filled by the next morning check-in job, not by guess. |
| `messages` | + `checkin_id`, `text` (for typed input), `transcript_status`, `purge_after`. `raw_file_reference` cleared after audio deletion. |
| `insights` | as spec. |

### 2.2 Additional tables

| Table | Purpose |
|-------|---------|
| `checkin_symptoms (checkin_id, symptom, source)` | Overload signs (spec §3.1) are multi-select; normalised for weekly counts. Symptom codes are a closed enum: `need_isolation, no_motivation, irritability, low_concentration, physical_fatigue, sleep_difficulty, emotional_instability, pleasant_feels_heavy, staring_blankly, other`. |
| `dialog_state (user_id, state, data JSONB, updated_at)` | FSM storage. |
| `scheduled_prompts (user_id, kind, local_date, sent_at, status)` | Idempotent scheduling; unique `(user_id, kind, local_date)`. |
| `llm_runs (id, message_id, provider, model, prompt_version, latency_ms, valid BOOL, error, created_at)` | Observability and evals **without** storing raw text in logs. |
| `consents (user_id, kind, granted_at, revoked_at)` | Spec §12.8–10. |

Enum-like columns: `TEXT` + `CHECK` constraints (easier to evolve than PG enums with Alembic).

### 2.3 Analytical views (M4)

- `v_daily_state` — one row per `(user_id, local_date)`: morning scores, evening scores, comparison_to_morning, symptom count, load counts by category × intensity, recovery activities.
- `v_next_morning` — `v_daily_state` self-joined on `local_date + 1` to pair evening load with next-morning state (spec §4.4).

---

## 3. Milestones

Estimates assume one developer part-time (~10–15 h/week). Each milestone ends with a usable bot.

### M0 — Project skeleton (≈ 3–4 days)

- [ ] Replace the Visual Studio `.gitignore` with a Python one (`.venv`, `__pycache__`, `.env`, `*.ogg`, etc.).
- [ ] `pyproject.toml` (Python 3.12, aiogram 3.x, FastAPI, SQLAlchemy 2 async + asyncpg, Alembic, APScheduler 3.x, pydantic-settings, structlog); ruff + mypy + pytest.
- [ ] `config.py` with env vars: `BOT_TOKEN`, `WEBHOOK_SECRET`, `DATABASE_URL`, `ALLOWED_TELEGRAM_IDS`, `BOT_MODE`, provider keys.
- [ ] Docker Compose: `app`, `postgres:16`, `caddy` (auto TLS). Dev override with polling and no Caddy.
- [ ] Structured logging with a **redaction filter** (never log message text / transcripts — spec §12.5); `/healthz` endpoint.
- [ ] CI (GitHub Actions): ruff, mypy, pytest with Postgres service container.
- [ ] Create bot via BotFather; document setup in README.

**Done when:** `docker compose up` starts the app, `/healthz` is green, bot answers `/ping` in polling mode, CI is green.

### M1 — Basic bot: onboarding, check-ins, persistence (spec Stage 1 + §17) (≈ 2–3 weeks)

1. **Models + first Alembic migration**: `users`, `checkins`, `checkin_symptoms`, `messages`, `dialog_state`, `scheduled_prompts`, `consents`.
2. **Middlewares**: allowlist (silently ignore others), user loader, DB session per update.
3. **`/start` onboarding** (≤ 1 minute): short explanation + disclaimer "не медицинский сервис", timezone selection (buttons for common zones + "send city/offset" fallback, validated via `zoneinfo`), morning/evening times, reminders on/off.
4. **`/settings`**: change timezone, times, quiet hours, reminders, transcript storage.
5. **Conversation engine**: flows declared as data (question id, type `scale_0_10 | scale_0_5 | choice | multi_choice | free_text`, options, optional flag). Engine handles:
   - "Пропустить" button on every question, "Закончить" to end early;
   - persisting each answer immediately;
   - resume: if an `in_progress` check-in exists for today, offer "Продолжить / Начать заново / Не сейчас";
   - one active flow at a time; free text during a flow is attached to the current check-in as `notes`.
6. **Evening check-in on buttons first** (spec §17.5), then morning, then ad-hoc:
   - *Morning* (§4.1): sleep (good/medium/poor/skip), physical 0–10, desire for something pleasant (no/a little/yes → mapped to `interest_score` coarse), day outlook, optional heavy-load expectation.
   - *Evening* (§4.3): compared to morning, what drained most (multi-select load categories), what helped (multi-select recovery activities + free text), pleasant energy left, note for tomorrow; optional 5-dimension block (physical / emotional / social / cognitive / overall 0–10) and overload signs multi-select — offered as "подробнее" so the default path stays ≤ 2 min.
   - *Ad-hoc / "мне сейчас плохо"*: overall state, overload signs, free text.
7. **Free text outside flows** (§17.7): stored in `messages` and attached to today's ad-hoc record; acknowledged gently. (LLM extraction comes in M3.)
8. **Scheduler tick job** with quiet hours, reminders toggle, and **at most one gentle reminder** per missed check-in (no nagging; missing data is fine).
9. **Safety handler v1** (must ship with any free-text input): keyword/regex detector for imminent self-harm phrases in Russian → stop normal flow, send static crisis message (emergency number for the user's country + "обратись к близкому человеку"), log only the fact of the event. Text reviewed carefully; no diagnosis.
10. **`/week` Markdown export** (§17.8): last 7 days as a Markdown file — per-day scores shown **separately per dimension**, sleep, loads, recovery, notes; explicit "нет записи" for missing days.
11. **`/export_all` (JSON) and `/delete_me`** (with confirmation) — cheap now, mandatory per §12.7.

**Done when:** a full week can be logged via buttons; restart mid-check-in loses nothing and offers resume; skipping any question never blocks; Markdown export reflects exactly the stored data; morning+evening take < 3 min combined in a timed manual run.

### M2 — Voice (spec Stage 2) (≈ 1 week)

- [ ] `SpeechToText` interface + provider implementation (language `ru`), timeouts and one retry.
- [ ] Voice handler: `file_id` → download via Bot API to a temp dir → transcribe → **delete audio immediately on success** (on failure: retry once, then delete and ask to resend or type).
- [ ] Show transcript with buttons "Верно / Исправить / Отменить"; "Исправить" accepts typed correction. Only confirmed text continues down the pipeline.
- [ ] Input normalizer: unify `text | voice | button` into one `NormalizedInput(user_id, text, input_type, message_id, received_at, checkin_id?)`.
- [ ] Consent prompt on first voice message: what is sent to the STT provider and its retention terms (§12.8–10). No consent → voice not processed, user can type instead.
- [ ] Voice answers allowed for free-text questions inside check-ins.

**Done when:** a voice note in Russian becomes a confirmed note attached to the right check-in; no audio files remain on disk after processing; logs contain no transcript text.

### M3 — LLM extraction & follow-ups (spec Stage 3, §5, §10, §11) (≈ 2 weeks)

1. **Pydantic schemas** mirroring §10: `LoadEventOut`, `RecoveryActivityOut`, `FollowUp`, `AnalysisResult` with closed enums for categories, intensities, symptoms, activities; `null` for unknown; `confidence` on every extracted item; plus `safety_concern: bool`.
2. **Prompt** from §11, versioned (`prompt_version` stored in `llm_runs`). Context passed to the model is compact: current message, today's check-in answers so far, list of already-asked follow-ups today, user's recent baseline (e.g. 7-day medians per dimension) — not full history.
3. **Validation layer**: parse → Pydantic validate → business rules (no invented durations: `duration_minutes` must be null unless a number/time phrase is present in the text; categories within enum; max 1 follow-up) → on failure retry once with the error, then fall back to "store raw text only".
4. **Confirmation instead of silent writes**: show the extracted events as a short list with "Сохранить / Поправить / Не сохранять". Only confirmed items are written to `load_events` / `recovery_events` / `checkin_symptoms` with `source='llm'`. This enforces "no unconfirmed facts" (§8.2.6).
5. **Follow-up policy** (orchestrator, not the LLM, has the final say): ask only if LLM says `needed` **and** limit not reached (≤ 2 per check-in, ≤ 1 per message) **and** not asked before today; always offer "не знаю / пропустить".
6. **Safety**: LLM `safety_concern` flag combined with the M1 keyword detector (either triggers the crisis response).
7. **Golden evaluation set**: 30–50 realistic Russian diary messages with expected structured output (written by hand from the spec's examples). `pytest -m eval` reports category precision/recall, invented-field rate (must be 0), follow-up rate. Used to choose provider/model and to gate prompt changes.
8. **Data-sharing consent** for LLM (same mechanism as STT); without it, free text is stored but not analysed.

**Done when:** the spec example ("три часа сложной работы, неприятный разговор…") yields work/high/180 min + social-or-emotional event + symptoms, with at most one follow-up; eval set passes thresholds; invalid LLM output never reaches the DB.

### M4 — Reports & analytics (spec Stage 4, §6, §7) (≈ 2 weeks)

1. **Analytical views** `v_daily_state`, `v_next_morning` (§2.3), loaded into pandas for report generation (data is tiny; no Spark needed — but the same views export cleanly to CSV/Parquet if you want to explore in a notebook/PySpark).
2. **Daily evening summary** (§7): template-driven, LLM only rephrases a structured fact list (facts in → text out; the LLM may not add facts). Sent after evening check-in completion, skippable via settings.
3. **Weekly report** (`/report`, plus optional Sunday-evening delivery):
   - per-dimension dynamics (never averaged across dimensions), interest/hobby trend, sleep quality distribution;
   - top load categories by count × intensity;
   - recovery activities followed by "better" (immediate and next morning);
   - next-morning worsening after high-load days;
   - recurring early signs;
   - **data limitations block**: number of days with records, missing check-ins, explicit "данных пока недостаточно" when below thresholds.
4. **Charts**: matplotlib PNG small multiples — one line per dimension in separate panels, gaps for missing days (no interpolation).
5. **Rules engine for planning modes** (§6.1) — *stable / limited resource / pronounced exhaustion*. Deterministic, explainable rules over the last 1–3 days combining self-ratings, symptoms and dynamics (e.g. "no desire + low concentration + pleasant feels heavy" → exhaustion). Output stored as an `insight` with the triggering record IDs in `supporting_data`. Shown as a planning hint ("стоит упростить день"), never as a diagnosis, never from a single number.
6. **Early-signs nudge** (§6.2): ≥ N early signs within the last 2–3 days → one gentle suggestion to review what can be postponed, at most once per few days.
7. **Pattern wording guardrails**: patterns require ≥ 3 supporting occurrences; confidence `low/medium/high` from counts; text templates use "возможно, связано" / "похоже на повторяющуюся закономерность" only.
8. **CSV export** of daily state.

**Done when:** weekly report numbers are reproducible from SQL against the raw tables (unit-tested on fixture data); with < 4 days of data the report says so instead of drawing conclusions.

### M5 — Personalisation (spec Stage 5) (later, ≈ 2–3 weeks, after ≥ 3–4 weeks of real use)

- Personal baselines (rolling medians per dimension) and "notable change vs your usual level" triggers for follow-ups (§5).
- Individual early-sign profile: symptoms that most often preceded "exhaustion" days.
- Ranking of recovery activities by next-morning effect, with sample sizes shown.
- Adaptive question frequency (shorten morning check-in on stable streaks).
- Gradual-return assistant (§6.5): suggests increasing load in **one** direction at a time once recent days are stable and small activities didn't worsen the next morning — never date-based.
- Two-week experiment mode (§7): guided phases with day counter, explicitly framed as observation, "no improvement is also a result".

---

## 4. Cross-cutting work

### Privacy & security (spec §12)
- Allowlist middleware; webhook secret token; HTTPS via Caddy; Postgres not exposed publicly; secrets only via env / Docker secrets.
- Separate `telegram_id` from analytics: analytics tables reference internal `user_id` UUID only.
- Log redaction filter + test that asserts no message text appears in captured logs.
- Retention job (daily): purge `messages.text/transcript` past `purge_after`; verify no audio files remain.
- `/privacy` command: what is stored, what goes to which provider, how to export/delete.
- Nightly `pg_dump` backups, encrypted, with a tested restore.

### Testing strategy
- **Unit**: flow definitions, engine transitions (skip/resume/abandon), rules engine, report aggregations, validation layer.
- **Integration**: real Postgres via testcontainers / CI service; aiogram updates fed through the dispatcher with a fake bot session; fake `LLMClient`/`SpeechToText`.
- **Scheduler**: frozen time (`time-machine`) across timezones and DST changes; idempotency on double tick.
- **LLM evals**: golden set (M3), run manually / nightly, not on every PR.
- **Tone review**: checklist over `texts/ru.py` — no words about discipline/productivity/"должна", every question has a skip option.

### Observability
- structlog JSON logs (IDs only), Sentry for exceptions (with `before_send` scrubbing message bodies), `llm_runs` table for latency/validity stats.

---

## 5. Mapping to MVP success criteria (spec §13)

| Criterion | Where it is enforced |
|-----------|----------------------|
| Daily survey ≤ 3 min | Short default paths, optional "подробнее" block (M1); timed manual test |
| Voice diary | M2 |
| No data loss on restart | Per-answer persistence + Postgres FSM + idempotent scheduler (M1) |
| Skips never block | Skip button on every question, engine tests (M1) |
| Bot doesn't invent data | NULL-for-skip, LLM validation + user confirmation, invented-field eval = 0 (M3) |
| Weekly report reflects real records | SQL-reproducible aggregates + fixture tests (M4) |
| No guilt / pressure | Max one reminder, tone checklist, no streaks/scores of "discipline" |

## 6. Explicitly out of scope for v1 (spec §14)
Microservices, energy-%, auto-diagnosis, recovery date prediction, ML prediction models, wearables, many notifications, auto-cancelling meetings.

## 7. First sprint backlog (≈ 2 weeks, = M0 + start of M1)

1. Python `.gitignore`, `pyproject.toml`, Docker Compose, CI.
2. Config, logging with redaction, `/healthz`.
3. SQLAlchemy models + initial Alembic migration (`users`, `checkins`, `checkin_symptoms`, `messages`, `dialog_state`, `scheduled_prompts`).
4. Allowlist + user middlewares.
5. `/start` with timezone and check-in times.
6. Conversation engine with skip/finish/resume + Postgres FSM storage.
7. Evening check-in on buttons, saved per answer.
8. Free-text capture + safety keyword handler.
9. `/week` Markdown export.
10. Webhook mode behind Caddy on the VPS; smoke test end-to-end.
