# Budgerr + playstat Deployment Runbook

**This is prep only. Nothing here has been run.** Deployment is gated on the
owner's explicit go-ahead — do not execute any step below until they say so.
When they do, both APIs (Budgerr and playstat) move together, onto one box,
in one Compose stack. This document is the runbook for that day.

Design background: `docs/superpowers/specs/2026-07-16-deployment-design.md`.

---

## 1. Scope & gate

Today both APIs run as native processes/launchd jobs on the owner's Mac. This
runbook moves them to a dedicated always-on box running the combined stack
under Docker Compose, with systemd timers replacing launchd, and the Budgerr
API reachable from anywhere via Tailscale Funnel.

- **Owner-gated.** No hardware has been bought and nothing is switched on
  without the owner's explicit approval. Treat every section below as a
  drill script, not a completed action.
- **Both APIs move together.** Budgerr and playstat share one Compose file,
  one box, one cutover. Playstat's Dockerfile, `/health`, and `mlb` chain are
  authored and owned by the playstat repo — this doc never asks you to edit
  anything under `~/dev/playstat`.
- **playstat review gate.** Before anything here goes live, the playstat
  architect reviews the `playstat-db`/`playstat-api` blocks in
  `deploy/docker-compose.yml` against their real running system.

## 2. Choose the box

The owner deferred the final hardware choice until the Pi's real retrain
time is measured. Both artifacts (Dockerfile, compose file) are already
arch-agnostic multi-arch, so this choice does not require re-authoring
anything — it only picks which OS-prep column below you follow.

| Target | Arch | Notes |
|---|---|---|
| Raspberry Pi 5 (8 GB) | ARM64 | ~10 W, silent, always-on. |
| Old laptop → Ubuntu Server | x86_64 | Free, faster CPU, more headroom as training data grows; higher power/noise, requires wiping Windows and disabling lid-sleep. |

**The Pi retrain caveat — read this before deciding.** playstat retrains an
XGBoost model on roughly 1M rows every morning as part of its `mlb` batch
chain. The Pi 5 is materially slower than the old laptop for this workload —
estimated 2-4x the x86 wall-clock time. Both the Budgerr owner and the
playstat architect have independently flagged this as the single most
important open question in the hardware decision. It is acceptable *if*
"done unattended by mid-morning" is an acceptable bar, because
`budgerr-auto-settle`/`budgerr-auto-log` are ordered `After=
playstat-mlb.service` (systemd waits for the retrain to finish rather than
racing it on a fixed clock — see §8). It is **not** acceptable if the
retrain routinely blows past the point where downstream jobs are expected to
have run.

**Do not commit to the Pi without timing the retrain on the real target
first.** Before any other hardware decision is finalized, run playstat's
`mlb` chain (or at least the retrain step) on an actual Pi 5 and record wall-
clock time. If it comes in well under budget, the Pi's power/noise/silence
advantages make it the easy default. If it does not, the laptop is the
fallback with zero redesign needed on this side.

## 3. OS prep

Steps are identical on both targets except the base OS image.

- **Laptop:** install Ubuntu Server 24.04 LTS (wipe Windows).
- **Pi:** flash Raspberry Pi OS 64-bit (Bookworm) to the SD card/SSD via
  Raspberry Pi Imager.

Then, on either target:

```bash
# Create a non-root operator user if one doesn't already exist, then:
sudo apt update && sudo apt upgrade -y

# Docker Engine + Compose plugin
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"
# log out/in (or `newgrp docker`) for the group change to take effect

# Tailscale
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Confirm Docker works without `sudo` and that `docker compose version` shows
the v2 plugin (not the standalone `docker-compose` v1 binary) before
continuing.

## 4. Get the repos

The compose file resolves build contexts relative to its own directory
(`deploy/`): `../backend` for Budgerr, `../../playstat` for playstat.
**playstat must be a sibling repo**, not nested inside Budgerr. Clone both
side by side under `~/dev/`:

```bash
mkdir -p ~/dev && cd ~/dev
git clone <budgerr-remote-url> Budgerr
git clone <playstat-remote-url> playstat
```

Resulting layout:

```
~/dev/Budgerr/
  backend/          <- ../backend from deploy/
  deploy/
    docker-compose.yml
~/dev/playstat/      <- ../../playstat from deploy/
```

`playstat` is owned and reviewed by another team. Budgerr never writes into
it — this runbook only ever reads from it (its Dockerfile, its `.env`).

## 5. Secrets

Create `backend/.env` and `playstat/.env` from their respective examples.
None of these values are committed; fill them in on the box only.

```bash
cd ~/dev/Budgerr
cp backend/.env.example backend/.env
cd ~/dev/playstat
cp .env.example .env   # or however playstat documents its own example
```

**`backend/.env`** (see `deploy/.env.example` for the compose-specific
annotations):

| Var | Notes |
|---|---|
| `AUTH_ENABLED` | Set `true` on the box. |
| `BUDGERR_API_KEYS` | `web:<key>,mobile:<key>,cron:<key>` — three long random keys, one per consumer. Generate each with e.g. `openssl rand -hex 32`. The `cron` key is what systemd timers use (§8) and also goes in `/etc/budgerr/cron.env`. |
| `DATABASE_URL` | Ignored — compose overrides this to point at `budgerr-db`. |
| `PLAYSTAT_BASE_URL` | Ignored — compose overrides this to `http://playstat-api:8000`. |
| `PLAYSTAT_API_KEY` | Must match a `budgerr:<key>` entry in playstat's `PLAYSTAT_API_KEYS`. Coordinate the actual value with the playstat side. |
| `PLAID_CLIENT_ID` / `PLAID_SECRET` / `PLAID_ENV` | Same values as the Mac's current config. |
| `CORS_ORIGINS` | Set to `https://<your-vercel-domain>` (see §10) once known — the box's own CORS list, not playstat's. |
| `NTFY_TOPIC` / `ANTHROPIC_API_KEY` | Optional; carry over from the Mac if used. |

**`playstat/.env`** (owned by the playstat session; listed here only so you
know it must exist before `docker compose up`):

| Var | Notes |
|---|---|
| `DATABASE_URL` | Ignored — compose overrides this to point at `playstat-db` (psycopg2 format). |
| `PLAYSTAT_API_KEYS` | Must contain the `budgerr:<key>` entry matching Budgerr's `PLAYSTAT_API_KEY` above. |
| `AUTH_ENABLED` | `true`. |
| `API_BASKETBALL_KEY` | **Required at import time** — the process refuses to start without it. |
| `ODDS_API_KEY` | Required. |
| `CORS_ORIGINS` | Optional; playstat-api is never exposed publicly (§9), so this rarely matters. |

## 6. Build & start

If playstat's Dockerfile is already present in `~/dev/playstat`, build and
start the full stack:

```bash
cd ~/dev/Budgerr/deploy
docker compose build
docker compose up -d
```

If it isn't present yet (coordinate timing with the playstat session — see
§10), scope both commands to the Budgerr side only:

```bash
docker compose build budgerr-api
docker compose up -d budgerr-db budgerr-api
```

and run `docker compose build playstat-api && docker compose up -d
playstat-db playstat-api` once their Dockerfile lands. Check status with
`docker compose ps` — both `budgerr-db` and `playstat-db` should show
`healthy` before their API containers report `Up` (compose gates on
`condition: service_healthy`).

**Validating the compose file:** if you ever want to check the file parses
without errors, use:

```bash
docker compose config --quiet
```

**Never run a bare `docker compose config`** — the full rendered dump prints
every resolved environment variable to stdout, including playstat's
secrets (`API_BASKETBALL_KEY`, `ODDS_API_KEY`, etc.) from `playstat/.env`.
`--quiet` validates and prints nothing on success.

## 7. Database migration

Do this once, per database, before relying on the box for real traffic.
Both need a verification step and both have a rollback path.

### Budgerr

Two options — pick one:

**A. Restore from the Mac's encrypted backup** (use this if you want the
Mac's real history on day one — follow `backend/ops/restore.md`'s "Real
disaster recovery" procedure, but target the box's container name):

```bash
# Copy the latest backup + the age private key to the box first, then:
age -d -i ~/.config/budgerr/backup-age.key <backup>.dump.age > /tmp/budgerr-restore.dump
docker compose stop budgerr-api   # nothing may hold a connection during the drop/restore
docker exec budgerr-stack-budgerr-db-1 psql -U budgerr -d postgres -c "DROP DATABASE IF EXISTS budgerr;"
docker exec budgerr-stack-budgerr-db-1 psql -U budgerr -d postgres -c "CREATE DATABASE budgerr;"
docker exec -i budgerr-stack-budgerr-db-1 pg_restore -U budgerr -d budgerr < /tmp/budgerr-restore.dump
docker compose start budgerr-api
rm -f /tmp/budgerr-restore.dump
```

**B. Fresh install** (empty DB, only if you don't need the Mac's history):

```bash
docker compose exec budgerr-api alembic upgrade head
```

**Verify (either path):** compare row counts against the Mac before treating
the box as authoritative:

```bash
docker exec budgerr-stack-budgerr-db-1 psql -U budgerr -d budgerr -c "SELECT count(*) FROM bets;"
# compare against the same query on the Mac's Postgres container
```

**Rollback:** the Mac's launchd stack and its own Postgres volume are
untouched by any of this — if the box's restore looks wrong, stop, fix, and
retry; nothing on the Mac is at risk.

### playstat (~701 MB)

playstat's own `pg_dump`/`pg_restore` process, coordinated with the playstat
session — this is their schema and their data, Budgerr does not touch it
directly. Broad shape:

```bash
# On the source (wherever playstat currently runs):
docker exec <playstat-source-container> pg_dump -U playstat -Fc playstat > playstat.dump
# Copy playstat.dump to the box, then:
docker compose stop playstat-api   # nothing may hold a connection during the restore
docker exec -i budgerr-stack-playstat-db-1 pg_restore -U playstat -d playstat < playstat.dump
docker compose start playstat-api
```

**Coordinate timing around playstat migration `005`, merging ~2026-07-18.
Do not snapshot playstat's schema before it lands** — a dump taken before
migration 005 merges will not match the schema the box's playstat-api
expects to run against. Confirm with the playstat session that 005 has
merged before pulling the snapshot.

**Verify:** row-count parity on playstat's key tables vs. the source,
confirmed with the playstat session.

**Rollback:** keep the source `playstat.dump` until the box's playstat-api
has been smoke-tested (§11) and the playstat architect has signed off (§1).

## 8. systemd

Install the unit files and enable the timers:

```bash
sudo cp ~/dev/Budgerr/deploy/systemd/*.service ~/dev/Budgerr/deploy/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now budgerr-plaid-sync.timer budgerr-auto-settle.timer \
    budgerr-auto-log.timer budgerr-backup.timer
```

Create `/etc/budgerr/cron.env` (mode 600, root-owned) — this is how every
unit gets its secrets and how the backup script gets pointed at the box's
container, without editing `backend/ops/backup.sh` at all:

```bash
sudo mkdir -p /etc/budgerr
sudo tee /etc/budgerr/cron.env > /dev/null <<'EOF'
BUDGERR_CRON_KEY=<the cron key from BUDGERR_API_KEYS>
BUDGERR_HOME=/home/<user>/dev/Budgerr
BUDGERR_DB_CONTAINER=budgerr-stack-budgerr-db-1
BUDGERR_AGE_RECIPIENTS=/etc/budgerr/backup-age.pub
BUDGERR_BACKUP_DIR=/var/backups/budgerr
EOF
sudo chmod 600 /etc/budgerr/cron.env
```

**Do not hand-edit `backend/ops/backup.sh` on the box.** The script already
reads `BUDGERR_DB_CONTAINER`, `BUDGERR_AGE_RECIPIENTS`, and
`BUDGERR_BACKUP_DIR` as environment overrides (defaulting to the macOS/
launchd values so the Mac's authoritative 03:00 backup keeps running
unchanged until cutover is verified). `budgerr-backup.service` already loads
`/etc/budgerr/cron.env` via `EnvironmentFile=`, so setting the three vars
above is the entire integration — no script changes, no `docker compose
exec` rewrite needed.

Also copy the age **public** recipients file to `/etc/budgerr/backup-age.pub`
on the box (this is what backups get encrypted to). Separately, copy the age
**private** key (`~/.config/budgerr/backup-age.key` on the Mac) to the box
as well — the backup script never needs it, but restores do (per
`backend/ops/restore.md`), and it is not stored alongside the backups on
purpose. Keep both copies (Mac and box) safe; if that private key is lost,
every encrypted backup becomes permanently unrecoverable.

Verify the units and check the timers landed:

```bash
systemd-analyze verify /etc/systemd/system/budgerr-*.service /etc/systemd/system/budgerr-*.timer
systemctl list-timers 'budgerr-*'
```

**playstat ordering:** `budgerr-auto-settle.service` and
`budgerr-auto-log.service` declare `After=playstat-mlb.service` (authored on
the playstat side) so they queue behind the morning retrain instead of
racing it on a fixed clock — see §2's Pi caveat for why this matters more
the slower the box is. `After=` without `Wants=` is a soft ordering: if
`playstat-mlb.service` doesn't exist yet, these jobs simply run at their
scheduled time (09:15/09:45) with no error. Confirm the real retrain
duration once measured (§2) and retune those times if the retrain routinely
runs long.

## 9. Tailscale exposure

```bash
sudo tailscale up
tailscale funnel 8001
```

This publishes `budgerr-api` (which compose already binds to host `:8001`)
at a public `https://<box-name>.<tailnet>.ts.net` URL — Tailscale-managed
TLS, no inbound firewall ports opened (the tunnel is outbound-initiated).
**Record this URL**; it is needed in §10 and §11.

If the owner later decides they want zero public surface instead, the
fallback is `tailscale serve 8001` (tailnet-only, no public URL) plus moving
the web frontend to a tailnet-reachable host — not the chosen path today,
just noted as the escape hatch.

**playstat is never exposed via Funnel or Serve.** `playstat-api` has no
`ports:` entry in the compose file at all — it is reachable only from
`budgerr-api` over the internal Compose network, at
`http://playstat-api:8000`.

## 10. Repoint clients

Once the Funnel URL is live and smoke-tested (§11), point both frontends at
it and allow it through Budgerr's CORS:

- **Vercel (`budgerr-web`):** set the API base-URL environment variable to
  `https://<box-name>.<tailnet>.ts.net` in the Vercel project settings, then
  redeploy.
- **Mobile:** set `EXPO_PUBLIC_API_URL=https://<box-name>.<tailnet>.ts.net`.
- **Budgerr CORS:** add the Vercel origin to `CORS_ORIGINS` in
  `backend/.env` on the box (e.g. `CORS_ORIGINS=https://<your-vercel-
  domain>`), then `docker compose up -d budgerr-api` to pick it up.

## 11. Smoke tests

Run these against the Funnel URL (or `http://127.0.0.1:8001` if testing
locally on the box itself — see the Mac caveat under §14 Troubleshooting for
why *not* to test against `:8001` on the Mac).

The `budgerr-api` container needs roughly **9 seconds** after `docker
compose up` before `/health` starts returning 200 (app startup +
`HEALTHCHECK`'s `start_period`). **Poll for readiness — don't `sleep` a
fixed short interval and assume it's up:**

```bash
url="https://<box-name>.<tailnet>.ts.net"
until curl -fsS -o /dev/null -w '%{http_code}' "$url/health" | grep -q 200; do
  sleep 1
done
echo "budgerr-api is up"
```

Then:

```bash
# 1. Unauthenticated health check -> 200 (auth-exempt by design)
curl -i "$url/health"

# 2. Authenticated call with a real key -> 200
curl -i -H "X-API-Key: <web key>" "$url/bets/bankroll"

# 3. No key on a normal route -> 401
curl -i "$url/openapi.json"

# 4. Proxied playstat call, through Budgerr's proxy, with a Budgerr key -> 200
curl -i -H "X-API-Key: <web key>" "$url/playstat/edges"

# 5. Run one systemd job manually and check it succeeds
sudo systemctl start budgerr-plaid-sync.service
sudo systemctl status budgerr-plaid-sync.service
```

`/openapi.json` and `/health` both resolve at the URL root with no path
prefix — Tailscale Funnel publishes the service at the root, it does not
inject a prefix.

## 12. Security note

**The Funnel URL is public on the open internet.** Anyone who discovers or
guesses it can reach the Budgerr API. The `X-API-Key` header is the **sole**
gate protecting personal financial and betting data — there is no additional
network-layer restriction once Funnel is on.

- Use long, random, per-consumer keys (`web`, `mobile`, `cron`) — never
  reuse one key across consumers.
- Rotate any key immediately on suspicion of exposure (e.g. accidentally
  committed, logged, or shared).
- **Rotating the `cron` key requires two edits, not one:** update
  `BUDGERR_API_KEYS` in `backend/.env` (then `docker compose up -d
  budgerr-api`) **and** `BUDGERR_CRON_KEY` in `/etc/budgerr/cron.env` — the
  systemd timers read the key from the latter, not from the container's env.
  Forgetting the second edit leaves the cron jobs silently failing with 401.
- If the owner ever wants zero public surface, fall back to `tailscale
  serve` (§9) instead of Funnel.

## 13. Rollback

If anything about the box's stack looks wrong after cutover:

```bash
cd ~/dev/Budgerr/deploy
docker compose down
```

This stops and removes the containers but leaves the named volumes
(`budgerr_pgdata`, `playstat_pgdata`) intact, so nothing is lost by doing
this.

**The Mac's launchd services remain the source of truth until cutover is
verified.** Do not stop or disable anything on the Mac (`com.budgerr.*`
launchd jobs, the native backend process) until the box has been observed
running correctly — smoke tests passing, the scheduled jobs firing on time,
backups landing — for a real stretch of days, not just the first smoke test.
**Nothing is deleted from the Mac** (backups, launchd plists, the local
Postgres volume) until parity between the box and the Mac is confirmed by
the owner.

## 14. Troubleshooting

- **Docker daemon not running:** on the Mac, `open -a Docker` starts Docker
  Desktop. On the box there is no Docker Desktop — Docker Engine runs as a
  systemd service; check with `sudo systemctl status docker`.
- **Testing against `:8001` on the Mac itself:** the Mac still runs a native
  launchd Budgerr process holding host port `:8001`. If you spin up the
  compose stack on the Mac for a local smoke test, its container's `:8001`
  publish will contend with that process, and a `curl localhost:8001` may
  hit the *old* launchd code instead of the container. This is a Mac-only
  quirk — on the box there is no launchd, so no such conflict exists there.
  If you need to smoke-test compose locally on the Mac, stop the launchd
  backend first or use a different published port.
- **`playstat-api` won't start / exits immediately:** check
  `API_BASKETBALL_KEY` is set in `playstat/.env` — playstat's process
  refuses to start without it (required at import, not just at request
  time).
- **Considering the Pi and haven't verified arm64 yet:** playstat's
  Dockerfile is currently build-verified only for `linux/amd64`. All of its
  key dependencies (xgboost, scipy, numpy, psycopg2-binary) publish arm64
  wheels, so arm64 is expected-good — but unproven. If the Pi is the chosen
  target, do a `docker buildx build --platform linux/arm64 ...` against
  `~/dev/playstat` **early**, well before the actual deploy day, so any gap
  surfaces with time to fix it rather than during cutover.
