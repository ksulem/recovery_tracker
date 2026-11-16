#!/usr/bin/env bash
# Deploy a pre-built image on the server. Run by the CD workflow over SSH from
# the project directory, after it has checked out the matching commit:
#
#   ./scripts/deploy.sh ghcr.io/<owner>/<repo>:<sha>
#
# If a registry token is piped on stdin (with GHCR_USER set), it is used for
# this pull only, through a throwaway Docker config: ~/.docker/config.json and
# any other project on the machine are left alone.
#
# Steps: pull → remember the image in .env (RECOVERY_IMAGE) → `docker compose
# up` (migrations run first, then the bot) → wait until the bot is healthy.
# If it doesn't get healthy, the previous image is started again. Database
# migrations are NOT rolled back: they are written to be backwards-compatible
# (additive), so the previous version keeps working on the newer schema.
#
# Works with both docker-compose.yml and docker-compose.shared.yml (whichever
# COMPOSE_FILE in .env selects). Touches nothing outside this directory except
# old images of this project.
set -euo pipefail

IMAGE=${1:?usage: deploy.sh <image>}
HEALTH_TIMEOUT=${HEALTH_TIMEOUT:-180}

log() { echo "$(date -u +%H:%M:%S) $*"; }
fail() { log "ERROR: $*"; exit 1; }

[ -f .env ] || fail ".env not found in $(pwd) — is this the project directory?"
[ -f docker-compose.yml ] || fail "docker-compose.yml not found in $(pwd)"

# --- registry login (optional, isolated) ------------------------------------------
TOKEN=""
if [ ! -t 0 ]; then
    IFS= read -r TOKEN || true
fi
if [ -n "$TOKEN" ]; then
    DOCKER_CONFIG=$(mktemp -d)
    export DOCKER_CONFIG
    trap 'rm -rf "$DOCKER_CONFIG"' EXIT
    printf '%s' "$TOKEN" | docker login "${IMAGE%%/*}" -u "${GHCR_USER:-deploy}" --password-stdin >/dev/null \
        || fail "registry login failed"
fi

# --- helpers ----------------------------------------------------------------------
current_image() {
    grep -E '^RECOVERY_IMAGE=' .env | tail -n1 | cut -d= -f2- || true
}

set_image() {
    if grep -qE '^RECOVERY_IMAGE=' .env; then
        sed -i "s|^RECOVERY_IMAGE=.*|RECOVERY_IMAGE=$1|" .env
    else
        printf '\n# Set by scripts/deploy.sh: the image the bot runs.\nRECOVERY_IMAGE=%s\n' "$1" >> .env
    fi
}

bot_health() {
    local id
    id=$(docker compose ps -q bot 2>/dev/null || true)
    [ -n "$id" ] || { echo "missing"; return; }
    docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$id"
}

wait_healthy() {
    local waited=0 status
    while [ "$waited" -lt "$HEALTH_TIMEOUT" ]; do
        status=$(bot_health)
        case "$status" in
            healthy) return 0 ;;
            unhealthy|exited|dead) break ;;
        esac
        sleep 5
        waited=$((waited + 5))
    done
    log "bot status after ${waited}s: $(bot_health)"
    return 1
}

# --- deploy -----------------------------------------------------------------------
PREVIOUS=$(current_image)
log "deploying $IMAGE (previous: ${PREVIOUS:-none})"

RECOVERY_IMAGE="$IMAGE" docker compose pull --quiet migrate bot || fail "pull failed"
set_image "$IMAGE"

# --no-build: never build on the server; the image comes from the registry.
if RECOVERY_IMAGE="$IMAGE" docker compose up -d --no-build migrate bot && wait_healthy; then
    log "bot is healthy"
else
    # Bot logs are NOT printed here: this output ends up in the Actions log,
    # which is public for a public repo, and the bot's logs contain its
    # username and may contain chat ids. Read them on the server instead.
    log "deploy failed; see the logs on the server: docker compose logs --tail 100 bot"
    if [ -n "$PREVIOUS" ] && [ "$PREVIOUS" != "$IMAGE" ]; then
        log "rolling back to $PREVIOUS"
        set_image "$PREVIOUS"
        # --no-deps: don't run the old image's migrate: it doesn't know the
        # newer schema revision and would fail, keeping the bot down.
        RECOVERY_IMAGE="$PREVIOUS" docker compose up -d --no-build --no-deps bot || true
        if wait_healthy; then
            log "rolled back; bot is healthy"
        else
            log "rollback did not get healthy either"
        fi
    fi
    exit 1
fi

# --- tidy up: keep only the current and the previous image (the disk is small) ----
REPO=${IMAGE%:*}
docker images "$REPO" --format '{{.Repository}}:{{.Tag}}' \
    | grep -vxF "$IMAGE" | grep -vxF "${PREVIOUS:-none}" \
    | xargs -r docker rmi >/dev/null 2>&1 || true

log "done"
