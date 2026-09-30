# Budgerr + playstat Deployment Runbook

**This is a live runbook.** The owner gave the go-ahead on 2026-09-29, so the
steps below are meant to be executed, not rehearsed. The target is an Oracle
Cloud Always Free instance (`VM.Standard.A1.Flex`, Ubuntu 24.04 ARM64) with
managed Supabase Postgres. Budgerr deploys first and alone; playstat follows in
a later session, and this document says where that fits.

Design rationale: `docs/superpowers/specs/2026-09-29-cloud-deploy-design.md`.
That spec supersedes the hardware half of
`docs/superpowers/specs/2026-07-16-deployment-design.md`; the July design is
otherwise still correct (compose topology, systemd units, Funnel exposure, the
API-key security model all carry over unchanged). Current state, including what
deployment is still waiting on: `HANDOVER.md`.

---

## 1. Scope & gate

Today both APIs run as native processes/launchd jobs on the owner's Mac. This
runbook moves Budgerr to a dedicated always-on box running its Compose stack,
with systemd timers replacing launchd, and the Budgerr API reachable from
anywhere via Tailscale Funnel.

- **Budgerr goes first, alone.** Budgerr and playstat share one Compose file and
  will eventually share one box, but they no longer share a cutover. Budgerr
  ships now; playstat is a later session.
- **Why that is safe.** The two call sites already degrade rather than fail
  (spec §6): `PLAYSTAT_BASE_URL` is a plain env var, the proxy returns a clean
  `502` on any upstream `httpx.RequestError`, and auto-settle catches
  `httpx.HTTPError` per game date and leaves bets pending instead of failing the
  job. So Budgerr runs in the cloud with `PLAYSTAT_BASE_URL` pointing at nothing
  reachable, and everything except playstat-backed features works.
- **playstat review gate.** Before playstat actually moves onto this box, the
  playstat architect reviews the `playstat-db`/`playstat-api` blocks in
  `deploy/docker-compose.yml` against their real running system. This gate does
  not block the Budgerr deploy.
- **playstat is owned elsewhere.** Playstat's Dockerfile, `/health`, and `mlb`
  chain are authored and owned by the playstat repo. This doc never asks you to
  edit anything under `~/dev/playstat`.

## 2. Choose the box

The box is an **Oracle Cloud Always Free** instance:

| Target | Arch | Notes |
|---|---|---|
| Oracle Cloud Always Free `VM.Standard.A1.Flex` | ARM64 | 4 ARM OCPUs, 24 GB RAM, Ubuntu 24.04 ARM64. Free, always-on. |

This has materially more CPU and roughly 3x the RAM of the Raspberry Pi 5 that
prompted the old retrain caveat, so playstat's ~1M-row XGBoost retrain stops
being a gate rather than being answered. Both artifacts (Dockerfile, compose
file) are already arch-agnostic multi-arch, so nothing needs re-authoring.

**Oracle reclaims idle instances.** Always Free compute instances judged idle
are reclaimed (roughly: 95th-percentile CPU, network and memory all under 20%
across 7 days). A single-user API is a plausible candidate, so treat this as a
real risk rather than a surprise. It is tolerable here because the box is
stateless: Budgerr's Postgres is managed (Supabase), so the box holds nothing
that cannot be rebuilt from this runbook. A reclaim means rebuilding, not losing
data. Keep the secrets recoverable off-box (a password manager) and a reclaim
becomes an inconvenience measured in minutes.

## 3. OS prep

Install Ubuntu 24.04 LTS ARM64 on the Oracle instance. The default user on
Oracle's Ubuntu image is `ubuntu`; the commands below assume you are logged in
as that user (or another non-root operator user).

```bash
sudo apt update && sudo apt upgrade -y

# Docker Engine + Compose plugin
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"
# log out/in (or `newgrp docker`) for the group change to take effect

# Tailscale
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Install the Postgres client. `backup.sh` now runs `pg_dump` on the box itself
instead of inside a container, so the client must exist, and its major version
must be at least the server's (Supabase runs Postgres 16):

```bash
sudo apt install -y postgresql-client-16
```

Set the box's timezone so the systemd timers (03:00 / 07:00 / 09:15 / 09:45,
§8) fire at the intended local time. Ubuntu Server defaults to UTC:

```bash
sudo timedatectl set-timezone <your-timezone>   # e.g. America/New_York
```

Ensure Docker starts on boot so the timer-driven jobs (which curl the container
or run `docker exec`, §8) find the daemon already up:

```bash
sudo systemctl enable --now docker
```

Confirm Docker works without `sudo` and that `docker compose version` shows
the v2 plugin (not the standalone `docker-compose` v1 binary) before
continuing.

**Do not open any inbound port.** OCI Ubuntu images ship restrictive `iptables`
rules, persisted in `/etc/iptables/rules.v4`, and the VCN has its own security
lists on top. That means inbound ports are blocked in two independent places,
and people lose hours to this. You do not need to touch either one: Tailscale
Funnel is outbound-initiated and needs no inbound port, so leave both the
`iptables` rules and the VCN security lists fully closed.

## 4. Get the repos

The compose file resolves build contexts relative to its own directory
(`deploy/`): `../backend` for Budgerr, `../../playstat` for playstat. Only
Budgerr is cloned now:

```bash
mkdir -p ~/dev && cd ~/dev
git clone <budgerr-remote-url> Budgerr
```

Resulting layout:

```
~/dev/Budgerr/
  backend/          <- ../backend from deploy/
  deploy/
    docker-compose.yml
```

**playstat must be a sibling repo** (`~/dev/playstat`), not nested inside
Budgerr, because the compose build context is `../../playstat`. That constrains
the layout today even though playstat is not cloned yet: when it is added later,
clone it side by side under `~/dev/`.

`playstat` is owned and reviewed by another team. Budgerr never writes into
it. This runbook only ever reads from it (its Dockerfile, its `.env`).

## 5. Secrets

Create `backend/.env` from its example. None of these values are committed; fill
them in on the box only.

```bash
cd ~/dev/Budgerr
cp backend/.env.example backend/.env
```

**`backend/.env`** (see `deploy/.env.example` for the compose-specific
annotations):

| Var | Notes |
|---|---|
| `AUTH_ENABLED` | Set `true` on the box. |
| `BUDGERR_API_KEYS` | `web:<key>,mobile:<key>,cron:<key>`, three long random keys, one per consumer. Generate each with e.g. `openssl rand -hex 32`. The `cron` key is what systemd timers use (§8) and also goes in `/etc/budgerr/cron.env`. |
| `DATABASE_URL` | **Required, and owned by this file**: compose no longer overrides it. Must be Supabase's **session pooler**: port 5432 on the `...pooler.supabase.com` host. Rewrite the scheme Supabase gives you from `postgresql://` to `postgresql+psycopg://`, or the app fails at startup with `No module named psycopg2`: SQLAlchemy's bare `postgresql://` dialect means psycopg2, and this backend installs psycopg3 (`psycopg[binary]`). See the two traps below. |
| `PLAYSTAT_BASE_URL` | Ignored: compose overrides this to `http://playstat-api:8000`. |
| `PLAYSTAT_API_KEY` | Must match a `budgerr:<key>` entry in playstat's `PLAYSTAT_API_KEYS`. Coordinate the actual value with the playstat side. |
| `PLAID_CLIENT_ID` / `PLAID_SECRET` / `PLAID_ENV` | Same values as the Mac's current config. |
| `CORS_ORIGINS` | Set to `https://<your-vercel-domain>` (see §10) once known: the box's own CORS list, not playstat's. |
| `NTFY_TOPIC` / `ANTHROPIC_API_KEY` | Optional; carry over from the Mac if used. |

**The two Supabase connection traps.** Both are configuration-only, and neither
is obvious from the error you get:

1. **The direct connection is IPv6-only** on projects created recently. An
   IPv4-only host resolves it and then times out, which reads like a firewall
   problem rather than an address-family problem.
2. **The transaction pooler (port 6543) breaks psycopg3's prepared statements.**
   You get intermittent `prepared statement "_pg_..." already exists` errors
   under reuse, not a clean failure at startup.

Use the **session pooler** (port 5432 on the `...pooler.supabase.com` host). It
is IPv4-reachable and holds a real session, so prepared statements work.

**`playstat/.env`** (owned by the playstat session; listed here only so you
know it must exist before starting `playstat-api`. Its `env_file` is optional in
compose, so the Budgerr-only start in §6 works even if this file doesn't exist
yet):

| Var | Notes |
|---|---|
| `DATABASE_URL` | Ignored: compose overrides this to point at `playstat-db` (psycopg2 format). |
| `PLAYSTAT_API_KEYS` | Must contain the `budgerr:<key>` entry matching Budgerr's `PLAYSTAT_API_KEY` above. |
| `AUTH_ENABLED` | `true`. |
| `API_BASKETBALL_KEY` | **Required at import time**: the process refuses to start without it. |
| `ODDS_API_KEY` | Required. |
| `CORS_ORIGINS` | Optional; playstat-api is never exposed publicly (§9), so this rarely matters. |

## 6. Build & start

There is no `budgerr-db` to wait on any more, so this is a single service:

```bash
cd ~/dev/Budgerr/deploy
docker compose build budgerr-api
docker compose up -d budgerr-api
```

Check status with `docker compose ps`. When playstat is added later, it is
`docker compose up -d playstat-db playstat-api` on the same stack, and only then
does the `condition: service_healthy` gating on `playstat-db` come into play.

**Validating the compose file:** if you ever want to check the file parses
without errors, use:

```bash
docker compose config --quiet
```

**Never run a bare `docker compose config`**: the full rendered dump prints
every resolved environment variable to stdout, including playstat's
secrets (`API_BASKETBALL_KEY`, `ODDS_API_KEY`, etc.) from `playstat/.env`.
`--quiet` validates and prints nothing on success.

## 7. Database setup

Do this once, before relying on the box for real traffic. The chosen path is an
**empty start**: the cloud database is created fresh by Alembic, and the Mac's
data is not migrated.

```bash
docker compose exec budgerr-api alembic upgrade head
```

**Read the consequences before you run that.** They are easy to under-read:

- **Every bank account must be re-linked through `/link-bank`.** This is a
  required step, not a footnote. Plaid access tokens are rows in the
  `plaid_items` table, and an empty database has none, so the cloud backend
  cannot sync any account until each institution is re-linked.
- **Transaction history does not come back.** Plaid's sync only backfills a
  limited window, so months of categorized transactions and any budget periods
  built on them are gone from the cloud instance.
- **Bet history and bankroll start at zero.** Everything under `bets` is
  Mac-only.
- **The Mac's database and the cloud database diverge permanently** the moment
  the cloud one is used, with no merge path afterward.

For future disaster recovery, `backend/ops/restore.md` documents the decrypt and
restore procedure. Restoring the Mac's latest encrypted backup is the escape
hatch if the history turns out to matter, but only before real data accumulates
in the cloud instance: once you start entering data there, the two databases
have diverged and there is no merge.

**Rollback:** the Mac's launchd stack and its own Postgres volume are
untouched by any of this. If the cloud setup looks wrong, stop, fix, and retry;
nothing on the Mac is at risk.

## 8. systemd

Install the unit files and enable the timers:

```bash
sudo cp ~/dev/Budgerr/deploy/systemd/*.service ~/dev/Budgerr/deploy/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now budgerr-plaid-sync.timer budgerr-auto-settle.timer \
    budgerr-auto-log.timer budgerr-backup.timer
```

Create `/etc/budgerr/cron.env` (mode 600, root-owned). This is how every
unit gets its secrets and how the backup script gets pointed at Supabase,
without editing `backend/ops/backup.sh` at all:

```bash
sudo mkdir -p /etc/budgerr
sudo tee /etc/budgerr/cron.env > /dev/null <<'EOF'
BUDGERR_CRON_KEY=<the cron key from BUDGERR_API_KEYS>
BUDGERR_HOME=/home/ubuntu/dev/Budgerr
BUDGERR_DB_URL=<the same Supabase session-pooler URL as DATABASE_URL>
BUDGERR_ICLOUD_DEST=""
BUDGERR_AGE_RECIPIENTS=/etc/budgerr/backup-age.pub
BUDGERR_BACKUP_DIR=/var/backups/budgerr
EOF
sudo chmod 600 /etc/budgerr/cron.env
```

Three notes on those variables:

- **`BUDGERR_DB_CONTAINER` is gone.** There is no local Postgres container to
  exec into, so the variable has no meaning here.
- **`BUDGERR_DB_URL`** is what makes the backup work. When it is set, `backup.sh`
  switches from `docker exec ... pg_dump` to a direct `pg_dump` at that URL. The
  script strips the SQLAlchemy `+psycopg` suffix itself, so you can copy the
  value across from `DATABASE_URL` verbatim.
- **`BUDGERR_ICLOUD_DEST=""`** disables the off-machine leg explicitly. The
  script's default is a macOS iCloud path, which on Linux is merely a junk
  directory the script would create. Empty is the honest setting.

**Do not hand-edit `backend/ops/backup.sh` on the box.** The script already
reads `BUDGERR_DB_URL`, `BUDGERR_DB_CONTAINER`, `BUDGERR_AGE_RECIPIENTS`,
`BUDGERR_BACKUP_DIR` and `BUDGERR_ICLOUD_DEST` as environment overrides
(defaulting to the macOS/launchd values so the Mac's authoritative 03:00 backup
keeps running unchanged until cutover is verified). `budgerr-backup.service`
already loads `/etc/budgerr/cron.env` via `EnvironmentFile=`, so setting the
variables above is the entire integration: no script changes, no
`docker compose exec` rewrite needed.

Also copy the age **public** recipients file to `/etc/budgerr/backup-age.pub`
on the box (this is what backups get encrypted to). Separately, copy the age
**private** key (`~/.config/budgerr/backup-age.key` on the Mac) to the box
as well: the backup script never needs it, but restores do (per
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
racing it on a fixed clock. `After=` without `Wants=` is a soft ordering: since
`playstat-mlb.service` does not exist yet, these jobs simply run at their
scheduled time (09:15/09:45) with no error. That is the expected state until
playstat moves onto the box.

## 9. Tailscale exposure

```bash
sudo tailscale up
tailscale funnel 8001
```

This publishes `budgerr-api` (which compose already binds to host `:8001`)
at a public `https://<box-name>.<tailnet>.ts.net` URL: Tailscale-managed
TLS, no inbound firewall ports opened (the tunnel is outbound-initiated).
**Record this URL**; it is needed in §10 and §11.

Funnel is carrying more weight than it did in the July design: it is what lets
the OCI `iptables` rules and the VCN security lists stay fully closed (§3), so
there is no inbound port to open and no firewall rule to maintain.

If the owner later decides they want zero public surface instead, the
fallback is `tailscale serve 8001` (tailnet-only, no public URL) plus moving
the web frontend to a tailnet-reachable host. Not the chosen path today,
just noted as the escape hatch.

**playstat is never exposed via Funnel or Serve.** `playstat-api` has no
`ports:` entry in the compose file at all: it is reachable only from
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
locally on the box itself, see the Mac caveat under §14 Troubleshooting for
why *not* to test against `:8001` on the Mac).

The `budgerr-api` container needs roughly **9 seconds** after `docker
compose up` before `/health` starts returning 200 (app startup +
`HEALTHCHECK`'s `start_period`). **Poll for readiness, don't `sleep` a
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

# 4. Proxied playstat call, through Budgerr's proxy, with a Budgerr key -> 502
curl -i -H "X-API-Key: <web key>" "$url/playstat/edges"

# 5. Run one systemd job manually and check it succeeds
sudo systemctl start budgerr-plaid-sync.service
sudo systemctl status budgerr-plaid-sync.service
```

**Test 4 expecting `502` is a PASS, not a failure.** playstat is not running
yet, so `PLAYSTAT_BASE_URL` fails DNS, and the proxy converts that
`httpx.RequestError` into a clean `502 {"detail": "playstat upstream
unavailable"}` by design. Without this note the next reader will chase a
non-bug. It becomes a `200` once playstat is deployed onto the box.

`/openapi.json` and `/health` both resolve at the URL root with no path
prefix: Tailscale Funnel publishes the service at the root, it does not
inject a prefix.

## 12. Security note

**The Funnel URL is public on the open internet.** Anyone who discovers or
guesses it can reach the Budgerr API. The `X-API-Key` header is the **sole**
gate protecting personal financial and betting data: there is no additional
network-layer restriction once Funnel is on.

- Use long, random, per-consumer keys (`web`, `mobile`, `cron`), never
  reuse one key across consumers.
- Rotate any key immediately on suspicion of exposure (e.g. accidentally
  committed, logged, or shared).
- **Rotating the `cron` key requires two edits, not one:** update
  `BUDGERR_API_KEYS` in `backend/.env` (then `docker compose up -d
  budgerr-api`) **and** `BUDGERR_CRON_KEY` in `/etc/budgerr/cron.env`: the
  systemd timers read the key from the latter, not from the container's env.
  Forgetting the second edit leaves the cron jobs silently failing with 401.
- **The Supabase database password now lives in `backend/.env`.** Rotating it
  means rotating it in the Supabase dashboard, then updating **both**
  `DATABASE_URL` in `backend/.env` and `BUDGERR_DB_URL` in
  `/etc/budgerr/cron.env`. That is the same two-place trap as the cron key:
  miss the second one and the nightly backup starts failing while the API
  keeps working.
- If the owner ever wants zero public surface, fall back to `tailscale
  serve` (§9) instead of Funnel.

## 13. Rollback

If anything about the box's stack looks wrong after cutover:

```bash
cd ~/dev/Budgerr/deploy
docker compose down
```

This stops and removes the containers but leaves the named volume
(`playstat_pgdata`) intact, so nothing is lost by doing this. Budgerr's data
lives in Supabase, not on the box, so there is no Budgerr volume to leave
behind.

**The Mac's launchd services remain the source of truth until cutover is
verified.** With an empty start, the Mac's data is not migrated at all, so the
Mac is the only place that transaction and bet history exists. Do not stop or
disable anything on the Mac (`com.budgerr.*` launchd jobs, the native backend
process) until the box has been observed running correctly (smoke tests
passing, the scheduled jobs firing on time, backups landing) for a real
stretch of days, not just the first smoke test. **Nothing is deleted from the
Mac** (backups, launchd plists, the local Postgres volume) until parity between
the box and the Mac is confirmed by the owner.

## 14. Troubleshooting

- **Docker daemon not running:** on the Mac, `open -a Docker` starts Docker
  Desktop. On the box there is no Docker Desktop: Docker Engine runs as a
  systemd service; check with `sudo systemctl status docker`.
- **Testing against `:8001` on the Mac itself:** the Mac still runs a native
  launchd Budgerr process holding host port `:8001`. If you spin up the
  compose stack on the Mac for a local smoke test, its container's `:8001`
  publish will contend with that process, and a `curl localhost:8001` may
  hit the *old* launchd code instead of the container. This is a Mac-only
  quirk: on the box there is no launchd, so no such conflict exists there.
  If you need to smoke-test compose locally on the Mac, stop the launchd
  backend first or use a different published port.
- **Inbound port still blocked / Funnel URL unreachable:** OCI blocks inbound
  traffic in two independent places, the instance's `iptables` rules
  (persisted in `/etc/iptables/rules.v4`) and the VCN security lists. If you
  are debugging a connection, check both. The correct fix is usually neither:
  Funnel is outbound-initiated and needs no inbound port, so leave both closed
  (§3).
- **Supabase connection times out with no clear error:** you are probably on
  the direct connection, which is IPv6-only on recent projects. An IPv4-only
  host resolves it and then times out, which reads like a firewall problem.
  Switch `DATABASE_URL` to the session pooler (§5).
- **Intermittent `prepared statement "_pg_..." already exists`:** you are on
  the transaction pooler (port 6543), which breaks psycopg3's prepared
  statements under reuse. Switch to the session pooler on port 5432 (§5).
- **The instance disappears or is stopped by Oracle:** Always Free instances
  judged idle are reclaimed (roughly: 95th-percentile CPU, network and memory
  all under 20% across 7 days). The box is stateless, so this is a rebuild
  from this runbook, not data loss. Keep the secrets recoverable off-box (§2).
- **`pg_dump: server version mismatch`:** the box's `postgresql-client` is
  older than the Supabase server (Postgres 16). Install
  `postgresql-client-16` (§3).
- **`playstat-api` won't start / exits immediately:** check
  `API_BASKETBALL_KEY` is set in `playstat/.env`: playstat's process
  refuses to start without it (required at import, not just at request
  time).
- **Building playstat for arm64:** playstat's Dockerfile is currently
  build-verified only for `linux/amd64`. All of its key dependencies (xgboost,
  scipy, numpy, psycopg2-binary) publish arm64 wheels, so arm64 is
  expected-good, but unproven. This matters more than it used to, not less:
  the box is now definitely ARM64, so do a `docker buildx build --platform
  linux/arm64 ...` against `~/dev/playstat` **early**, well before playstat's
  deploy session, so any gap surfaces with time to fix it rather than during
  cutover.
