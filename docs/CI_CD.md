# CI and CD

Two GitHub Actions workflows live in `.github/workflows/`.

| Workflow | Runs on | What it does |
|---|---|---|
| **CI** (`ci.yml`) | push to `dev`, every pull request | ruff, ruff format, mypy, migrations round trip (`upgrade` → `downgrade base` → `upgrade` → `alembic check`), pytest against PostgreSQL 16, and a Docker build check |
| **CD** (`deploy.yml`) | push to `main` (a release from `dev`), or by hand: Actions → CD → Run workflow | runs CI, builds the image and pushes it to GHCR, then deploys it to the server over SSH |

A release is never deployed without passing CI: CD calls the CI workflow first.

## How a deploy works

1. The image is built in GitHub Actions and pushed as
   `ghcr.io/<owner>/recovery_tracker:<commit sha>` (and `:latest`). The server
   never builds anything: on a 1 GB VM a `pip install` competes with the other
   services for memory.
2. GitHub connects to the server over SSH and, in the project directory:
   - `git fetch` and `git checkout --detach <commit>`, so the compose files and
     scripts match the image;
   - runs [`scripts/deploy.sh`](../scripts/deploy.sh) with the image name.
3. `deploy.sh`:
   - pulls the image with a short-lived, read-only registry token. The token is
     sent over stdin and used through a throwaway Docker config, so
     `~/.docker/config.json` and other projects on the server are not touched;
   - writes the image into `.env` as `RECOVERY_IMAGE`, so a manual
     `docker compose up -d` later runs the same version. The previous value is
     the rollback target. On the very first CD deploy there is none yet, so
     the image of the bot that is running at that moment (built on the server)
     is used instead;
   - runs `docker compose up -d --no-build migrate bot`: migrations first,
     then the bot;
   - waits up to 3 minutes for the bot's healthcheck. If the bot doesn't get
     healthy, it starts the previous image again and fails the workflow. The
     bot's own logs are deliberately not printed: Actions logs of a public
     repository are public, and the bot's logs contain its username and may
     contain chat ids. Read them on the server with
     `docker compose logs --tail 100 bot`;
   - removes older images of this project, keeping the current and the
     previous one.

**Database migrations are not rolled back.** They are written to be additive,
so the previous version keeps working on the newer schema. If a release ever
needs a destructive migration, deploy it in two steps.

Works with both compose variants: the server's `.env` decides
(`COMPOSE_FILE=docker-compose.shared.yml` for a shared Postgres).

## One-time setup

### On the server

1. The project must already be deployed the manual way once (README → *Run
   your own*, or `docs/DEPLOY_SHARED_POSTGRES.md`): a git checkout with a
   filled-in `.env`. Note its absolute path, for example `/home/ubuntu/recovery_tracker`.
2. Create a key pair **only for deployments** (on your computer):

   ```bash
   ssh-keygen -t ed25519 -N "" -C "recovery-deploy" -f recovery-deploy
   ```

   Add `recovery-deploy.pub` to `~/.ssh/authorized_keys` of the user that
   owns the checkout. That user must be able to run `docker` (be in the
   `docker` group).
3. Get the server's host key, **from a machine where you have already
   connected and checked it**:

   ```bash
   ssh-keyscan -t ed25519 <server address>
   ```

   Compare the fingerprint with `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`
   on the server.

### On GitHub

**Settings → Environments → New environment** `production`, then add these
**environment secrets**:

| Secret | Value |
|---|---|
| `DEPLOY_HOST` | server address (IP or domain) |
| `DEPLOY_USER` | the SSH user from step 2 |
| `DEPLOY_PATH` | absolute path of the checkout on the server |
| `DEPLOY_SSH_KEY` | contents of the private key `recovery-deploy` |
| `DEPLOY_KNOWN_HOSTS` | the `ssh-keyscan` line from step 3 |

**Settings → Secrets and variables → Actions → Variables:**

| Variable | Value |
|---|---|
| `DEPLOY_ENABLED` | `true`. Without it CD runs only CI, which keeps forks of this public repo from trying to deploy |
| `DEPLOY_PORT` | optional; SSH port if not 22 |
| `IMAGE_PLATFORM` | optional; `linux/arm64` for an ARM server (Ampere, Raspberry Pi). Default `linux/amd64` |

Optional: in the `production` environment, add yourself under **Required
reviewers** to approve each deploy by hand, and limit **Deployment branches**
to `main`.

The repository is public, so none of these values go into files: the workflow
reads them only from secrets.

## Releasing

```bash
git checkout main && git merge --no-ff dev && git push origin main
```

Then watch Actions → CD. To re-deploy the current `main` without changes:
Actions → CD → Run workflow.

## Rolling back by hand

On the server, in the project directory:

```bash
docker images ghcr.io/<owner>/recovery_tracker   # pick the previous tag
sed -i 's|^RECOVERY_IMAGE=.*|RECOVERY_IMAGE=ghcr.io/<owner>/recovery_tracker:<tag>|' .env
docker compose up -d --no-build --no-deps bot
```
