#!/usr/bin/env bash
# Nightly backup of the recovery database to Oracle Object Storage.
#
# Adapted from watering-system's scripts/backup-db.sh (same bucket, instance
# principal auth, integrity + MD5 checks) with two differences:
#   * the dump is ENCRYPTED with age before it leaves the box — this database
#     holds health and emotional notes, not sensor readings;
#   * objects go under the recovery/ prefix. Add an Object Storage lifecycle
#     rule for that prefix (e.g. delete after 30 days) — the existing rule only
#     covers daily/.
#
# AGE_RECIPIENT is the PUBLIC key (age1...). Keep the private key off this VM
# (e.g. in your password manager): a stolen VM then can't read old backups.
#
# RESTORE (plain Postgres, no TimescaleDB steps needed):
#   age -d -i key.txt recovery-<stamp>.dump.age > recovery.dump
#   createdb -U plantuser -O recovery_app -T template0 recovery_restore
#   pg_restore -U plantuser -d recovery_restore --no-owner --role=recovery_app recovery.dump
#
# Cron example (03:17 every night):
#   17 3 * * * AGE_RECIPIENT=age1... /opt/recovery/scripts/backup-recovery-db.sh >> /var/log/recovery-backup.log 2>&1
set -euo pipefail

CONTAINER=${CONTAINER:-plant-monitor-timescaledb-1}
DB_NAME=${DB_NAME:-recovery}
DB_USER=${DB_USER:-recovery_app}
BUCKET=${BUCKET:-plant-monitor-backups}
PREFIX=${PREFIX:-recovery}
LOCAL_DIR=${LOCAL_DIR:-/var/backups/recovery}
LOCAL_KEEP=${LOCAL_KEEP:-3}
OCI=${OCI:-/usr/local/bin/oci}
AGE=${AGE:-age}
AGE_RECIPIENT=${AGE_RECIPIENT:?AGE_RECIPIENT (age public key) is required}
# An empty-but-valid dump of this schema is a few KB.
MIN_BYTES=${MIN_BYTES:-2000}

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }
fail() { log "ERROR: $*"; exit 1; }

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
DUMP="recovery-${STAMP}.dump"
FILE="${DUMP}.age"
TMP=$(mktemp -d)
chmod 700 "$TMP"
trap 'rm -rf "$TMP"' EXIT

log "dumping $DB_NAME from $CONTAINER"
docker exec "$CONTAINER" pg_dump -U "$DB_USER" -d "$DB_NAME" \
    -Fc --no-owner --no-acl > "$TMP/$DUMP" || fail "pg_dump failed"

size=$(stat -c%s "$TMP/$DUMP")
[ "$size" -ge "$MIN_BYTES" ] || fail "dump is only ${size}B (min ${MIN_BYTES}B) — refusing to upload"

docker exec -i "$CONTAINER" pg_restore --list > /dev/null < "$TMP/$DUMP" \
    || fail "dump failed its pg_restore --list integrity check"
log "dump OK (${size} bytes, archive readable)"

"$AGE" -r "$AGE_RECIPIENT" -o "$TMP/$FILE" "$TMP/$DUMP" || fail "encryption failed"
rm -f "$TMP/$DUMP"   # never keep the plaintext dump around

log "uploading $PREFIX/$FILE to $BUCKET"
"$OCI" os object put --auth instance_principal --bucket-name "$BUCKET" \
    --name "$PREFIX/$FILE" --file "$TMP/$FILE" --no-multipart >/dev/null \
    || fail "upload failed"

# Headers are at the top level of the response, not under data.
remote_md5=$("$OCI" os object head --auth instance_principal --bucket-name "$BUCKET" \
    --name "$PREFIX/$FILE" --query '"content-md5"' --raw-output) \
    || fail "could not stat the uploaded object"
local_md5=$(openssl md5 -binary "$TMP/$FILE" | base64)
[ -n "$remote_md5" ] || fail "object head returned no content-md5"
[ "$remote_md5" = "$local_md5" ] || fail "checksum mismatch: local $local_md5, remote $remote_md5"
log "upload verified (md5 $local_md5)"

mkdir -p "$LOCAL_DIR"
chmod 700 "$LOCAL_DIR"
cp "$TMP/$FILE" "$LOCAL_DIR/$FILE"
ls -1t "$LOCAL_DIR"/recovery-*.dump.age 2>/dev/null | tail -n +$((LOCAL_KEEP + 1)) | xargs -r rm -f

log "done: $PREFIX/$FILE"
