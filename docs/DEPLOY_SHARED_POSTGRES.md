# Deploying next to an existing Postgres

Use this instead of the default setup when the server **already runs Postgres
in another Docker Compose project** and is short on memory, for example a 1 GB
VPS. The bot then gets its own database and role on that existing server
instead of a second Postgres container.

This is how the author runs it: on a VM shared with a watering-system stack,
whose Postgres container is `plant-monitor-timescaledb-1` on the Docker network
`plant-monitor_default`. Substitute your own names below.

**Nothing that belongs to the other project is changed:** not its compose file,
`.env`, databases, network or backups. The bot joins the other project's
network as an external network, which doesn't modify that network.

Two safeguards keep the projects apart:

- Every variable is prefixed with `RECOVERY_`. Docker Compose lets exported
  shell variables override `.env`, so unprefixed names such as `BOT_TOKEN` or
  `DATABASE_URL` could pick up the other project's values.
- The bot refuses to start if its token has a webhook configured. That would
  mean it's another bot's token, and polling with it would delete that bot's
  webhook.

## Steps

1. **Configure.** Copy `.env.example` to `.env`, fill in the required values,
   and enable the shared variant:

   ```bash
   COMPOSE_FILE=docker-compose.shared.yml
   RECOVERY_PG_NETWORK=plant-monitor_default   # docker network ls
   RECOVERY_PG_HOST=timescaledb                # the Postgres service name on that network
   ```

2. **Database and role** (once). This only creates the `recovery_app` role and
   the `recovery` database. Run it as the existing server's superuser:

   ```bash
   PW=$(grep '^RECOVERY_DB_PASSWORD=' .env | cut -d= -f2-)
   docker exec -i plant-monitor-timescaledb-1 psql -U plantuser -d postgres \
     -v ON_ERROR_STOP=1 -v recovery_password="$PW" < scripts/create_db.sql
   ```

3. **Memory check.** Look at real usage before adding the bot
   (`mem_limit: 160m`, startup peak about 120 MB):

   ```bash
   docker stats --no-stream && free -m
   ```

4. **Start.** This runs migrations, then starts the bot:

   ```bash
   docker compose up -d --build
   ```

5. **Backups.** Use [`scripts/backup-recovery-db.sh`](../scripts/backup-recovery-db.sh).
   It defaults to the container `plant-monitor-timescaledb-1` and dumps only the
   `recovery` database. It uploads off-site only if you set `BUCKET` to a bucket
   of your own; never reuse the other project's bucket.

## Connection budget

The bot uses at most 4 connections (`db_pool_size=2`, `db_max_overflow=2` in
`src/recovery/config.py`). Check that the shared server's `max_connections`
leaves room for that.
