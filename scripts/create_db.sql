-- One-time setup of the recovery database on the shared watering-system Postgres.
--
-- Run as the superuser of that server (plantuser), passing the new role's password:
--
--   docker exec -i plant-monitor-timescaledb-1 \
--     psql -U plantuser -d plantmonitor -v ON_ERROR_STOP=1 \
--          -v recovery_password="$RECOVERY_DB_PASSWORD" < scripts/create_db.sql
--
-- Safe to inspect before running; it does not touch any plantmonitor table.

CREATE ROLE recovery_app LOGIN PASSWORD :'recovery_password';

-- template0: keeps the timescaledb extension (installed into template1 by the
-- TimescaleDB image) out of this database; it isn't needed here.
CREATE DATABASE recovery OWNER recovery_app TEMPLATE template0 ENCODING 'UTF8';

REVOKE CONNECT ON DATABASE recovery FROM PUBLIC;
GRANT CONNECT ON DATABASE recovery TO recovery_app;

-- Keep recovery_app out of the plant database. Every watering-system service
-- connects as plantuser (the owner/superuser), so this changes nothing for them.
REVOKE CONNECT ON DATABASE plantmonitor FROM PUBLIC;

ALTER DATABASE recovery SET timezone TO 'UTC';
