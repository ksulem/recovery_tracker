-- One-time setup of the recovery database on the shared watering-system Postgres.
--
-- Run as the superuser of that server (plantuser), passing the new role's password:
--
--   docker exec -i plant-monitor-timescaledb-1 \
--     psql -U plantuser -d postgres -v ON_ERROR_STOP=1 \
--          -v recovery_password="$RECOVERY_DB_PASSWORD" < scripts/create_db.sql
--
-- It only creates a new role and a new database. Nothing that belongs to
-- watering-system (the plantmonitor database, its roles, settings) is changed.

CREATE ROLE recovery_app LOGIN PASSWORD :'recovery_password';

-- template0: keeps the timescaledb extension (installed into template1 by the
-- TimescaleDB image) out of this database; it isn't needed here.
CREATE DATABASE recovery OWNER recovery_app TEMPLATE template0 ENCODING 'UTF8';

REVOKE CONNECT ON DATABASE recovery FROM PUBLIC;
GRANT CONNECT ON DATABASE recovery TO recovery_app;

-- Note: recovery_app can technically open a connection to plantmonitor (PUBLIC
-- has CONNECT by default) but has no privileges on its tables. Tightening that
-- would mean changing watering-system's database, which this project doesn't do.

ALTER DATABASE recovery SET timezone TO 'UTC';
