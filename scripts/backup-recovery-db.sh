#!/usr/bin/env bash
# Nightly encrypted backup of the recovery database.
#
# Adapted from watering-system's scripts/backup-db.sh (integrity + MD5 checks),
# but it touches nothing of watering-system:
#   * it dumps only the "recovery" database, as the recovery_app role;
#   * by default it keeps the encrypted dumps locally (LOCAL_DIR);
#   * an off-site copy is uploaded only if you set BUCKET to a bucket of your
#     own. Don't reuse watering's plant-monitor-backups bucket: its IAM policy
#     and lifecycle rule belong to that project.
#
# The dump is ENCRYPTED with age before it is written anywhere persistent —
# this database holds health and emotional notes. AGE_RECIPIENT is the PUBLIC
# key (age1...); keep the private key off this VM (e.g. in your password
# manager), so a compromised VM can't read old backups.
#
# CONTAINER is the Postgres container: recovery-db-1 with the default
# docker-compose.yml, plant-monitor-timescaledb-1 (the default here) with
# docker-compose.shared.yml.
#
# RESTORE into a scratch database, then compare it before swapping anything.
# Decrypt on a machine that has the private key and stream the dump, so the
# plaintext is never written to disk:
#   oci os object get --bucket-name <bucket> --name recovery/recovery-<stamp>.dump.age --file backup.age
#   docker exec <CONTAINER> createdb -U recovery_app -T template0 recovery_restore
#   age -d -i key.txt backup.age | ssh <server> \
#     docker exec -i <CONTAINER> pg_restore -U recovery_app -d recovery_restore --no-owner --no-acl --exit-on-error
#
# Cron example (03:17 every night, logged to the journal):
#   # /etc/cron.d/recovery-backup
#   17 3 * * * <user> CONTAINER=recovery-db-1 AGE_RECIPIENT=age1... BUCKET=<bucket> OCI=/usr/bin/oci /path/to/scripts/backup-recovery-db.sh 2>&1 | logger -t recovery-backup
#
# Off-site bucket: give the VM's dynamic group only OBJECT_CREATE, OBJECT_READ
# and OBJECT_INSPECT on it (no delete/overwrite), so a compromised VM can't
# wipe the backups; expire old dumps with a lifecycle rule on the bucket.
set -euo pipefail

CONTAINER=${CONTAINER:-plant-monitor-timescaledb-1}
DB_NAME=${DB_NAME:-recovery}
DB_USER=${DB_USER:-recovery_app}
LOCAL_DIR=${LOCAL_DIR:-/var/backups/recovery}
LOCAL_KEEP=${LOCAL_KEEP:-7}
AGE=${AGE:-age}
AGE_RECIPIENT=${AGE_RECIPIENT:?AGE_RECIPIENT (age public key) is required}
# Optional off-site upload (Oracle Object Storage, instance principal auth).
BUCKET=${BUCKET:-}
PREFIX=${PREFIX:-recovery}
OCI=${OCI:-/usr/local/bin/oci}
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
[ "$size" -ge "$MIN_BYTES" ] || fail "dump is only ${size}B (min ${MIN_BYTES}B) — refusing to keep it"

docker exec -i "$CONTAINER" pg_restore --list > /dev/null < "$TMP/$DUMP" \
    || fail "dump failed its pg_restore --list integrity check"
log "dump OK (${size} bytes, archive readable)"

"$AGE" -r "$AGE_RECIPIENT" -o "$TMP/$FILE" "$TMP/$DUMP" || fail "encryption failed"
rm -f "$TMP/$DUMP"   # never keep the plaintext dump around

mkdir -p "$LOCAL_DIR"
chmod 700 "$LOCAL_DIR"
cp "$TMP/$FILE" "$LOCAL_DIR/$FILE"
ls -1t "$LOCAL_DIR"/recovery-*.dump.age 2>/dev/null | tail -n +$((LOCAL_KEEP + 1)) | xargs -r rm -f
log "kept $LOCAL_DIR/$FILE"

if [ -z "$BUCKET" ]; then
    log "BUCKET not set — local copy only"
    exit 0
fi

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
log "upload verified (md5 $local_md5): $PREFIX/$FILE"
