# Budgerr + playstat Deployment — Design Spec

**Date:** 2026-07-16
**Status:** Approved design; **prep-only** (author artifacts, commit; nothing goes live).
**Deployment itself is gated on the owner** — no hardware is bought and nothing is switched on without their explicit go-ahead. Both the Budgerr and playstat architect sessions have confirmed this gate.

---

## 1. Goal & scope

Move the two personal-use APIs off the owner's daily-driver Mac onto a dedicated always-on box (README §15.2, "get off the laptop"), so cron-style jobs and the API run 24/7 without the Mac, and the phone can reach the API from anywhere.

**This pass produces artifacts only:**
- `backend/Dockerfile` (Budgerr API) — authored here.
- `deploy/docker-compose.yml` — the combined stack — authored here.
- systemd unit + timer files (replacing launchd jobs) — authored here.
- `docs/DEPLOY.md` — end-to-end runbook for both hardware targets — authored here.
- A small backend change: exempt `/health` from the API-key guard (see §5).

**Explicitly out of scope this pass:** running any of it, buying hardware, migrating real data, touching the playstat repo.

## 2. Hardware target — design for both

Owner deferred the final box until the Pi's real retrain time is known. Design is **arch-agnostic**: all base images are multi-arch, so the same artifacts run on either target.

| Target | Arch | Notes |
|---|---|---|
| Raspberry Pi 5 (8 GB) | ARM64 | ~10 W, silent, always-on. **Caveat:** playstat retrains XGBoost on ~1M rows daily; the Pi is materially slower (est. 2–4× the x86 wall-clock). Acceptable if "done unattended by mid-morning" is fine. |
| Old laptop → Ubuntu Server | x86_64 | Free, much faster retrain, more headroom as training data grows; higher power/noise, must wipe Windows, handle lid-sleep. |

`DEPLOY.md` documents both; the Pi's slower-retrain caveat is called out there.

## 3. Topology — one combined Compose stack

A new `deploy/docker-compose.yml` (kept separate from the dev-only root `docker-compose.yml`, which only spins up a dev Postgres).

| Service | Image / build | Exposed | Purpose |
|---|---|---|---|
| `budgerr-db` | `postgres:16-alpine` | internal only | volume `budgerr_pgdata`, db `budgerr` |
| `playstat-db` | `postgres:16-alpine` | internal only | volume `playstat_pgdata`, db `playstat` (~701 MB) |
| `budgerr-api` | build `backend/Dockerfile` (context = repo root) | **public via Funnel** | `uvicorn app.main:app --host 0.0.0.0 --port 8001` |
| `playstat-api` | build from playstat's own Dockerfile (**they author**) | internal only | `uvicorn api.main:app --host 0.0.0.0 --port 8000` |
| `playstat-dashboard` | playstat `web/` `next start` :3000 | *(optional, compose profile, off by default)* | playstat's own dashboard, only if wanted remotely |

**Two separate Postgres services**, not one shared instance: respects playstat's ownership boundary (its own schema/dump/restore), keeps backups independent, and 8 GB easily absorbs two small Postgres containers.

**Cross-repo layout on the box:** `~/dev/Budgerr` and `~/dev/playstat` sit side by side; the compose file references playstat via a relative build context (`../playstat`). `DEPLOY.md` documents the required directory layout. **Budgerr never writes into the playstat repo.**

## 4. Networking & exposure

- **Internal Compose network** joins all services. Budgerr's playstat proxy switches its upstream from `localhost:8000` → `http://playstat-api:8000` (Compose service DNS). The playstat `X-API-Key` continues to be injected server-side; it never leaves the box.
- **Only `budgerr-api` is exposed to the internet, via Tailscale Funnel** (public HTTPS URL, Tailscale-managed TLS, no inbound firewall ports — the tunnel is outbound-initiated). Required because `budgerr-web` is hosted publicly (Vercel) and cannot reach a tailnet-only address.
- **`playstat-api` is never publicly exposed.** It is consumed only server-side by Budgerr's proxy over the internal network. This keeps playstat at zero public attack surface.
- **Clients:** both `budgerr-web` (Vercel) and the mobile app point at the Funnel URL and attach their `X-API-Key`. The phone therefore needs **no** Tailscale app.
- **Security tradeoff (call out prominently in DEPLOY.md):** the public Funnel URL means the Budgerr `X-API-Key` is the *sole* gate to personal financial + betting data. Mitigations: keys are per-consumer and constant-time-compared already; use long random keys, rotate on any suspicion, and remember rotating the `cron` key means updating the cron unit files too. If the owner later wants zero public surface, the fallback is Tailscale **Serve** (tailnet-only) for the API + a tailnet-reachable web host — documented but not the chosen path.

## 5. `/health` auth exemption (small backend change, owned here)

Today Budgerr's `/health` is behind the global `require_api_key` dependency (returns 401 without a key), which makes a clean container healthcheck awkward. Change: exempt `/health` from the guard so orchestration can probe it unauthenticated (it leaks only `{"status":"ok"}`). This mirrors the auth-exempt `/health` playstat is adding on their side.

- **Compose healthchecks:** `budgerr-api` → `GET /health` expects 200; `playstat-api` → `GET /health` expects 200 (playstat is adding it; **fallback** until then: assert an unauthenticated request returns 401, e.g. `curl -s -o /dev/null -w '%{http_code}' localhost:8000/edges | grep -q 401`, which proves the process is up and auth-enforcing).
- `depends_on: condition: service_healthy` gates each API on its DB.

## 6. Scheduled jobs — launchd → systemd timers

All four Budgerr launchd jobs plus playstat's daily chain become systemd `.timer` + `.service` units on the host.

| Job | Today | On the box |
|---|---|---|
| `plaid-sync` | launchd 7:00, `POST /plaid/sync-all` | `budgerr-plaid-sync.timer` (7:00) → curl the API with cron key |
| `auto-settle` | launchd 8:30, `POST /bets/auto-settle` | `budgerr-auto-settle.service`, ordered **`After=playstat-mlb.service`** |
| `auto-log-parlays` | launchd 9:00, `POST /bets/auto-log-recommendations` | `budgerr-auto-log.service`, ordered **`After=playstat-mlb.service`** |
| `backup` | launchd 3:00, `backend/ops/backup.sh` | `budgerr-backup.timer` (3:00) → runs backup script adjusted to `docker compose exec budgerr-db pg_dump` |
| playstat `mlb` | launchd 8:30 ET, ~11 `python -m` steps | `playstat-mlb.timer` (8:30 ET) → `docker compose run --rm` into the playstat container (playstat owns the exact chain) |
| playstat `backfill` | NBA backfill, self-disables | skip if already complete at deploy time (check first) |

**Key win over launchd:** the morning race is fixed. Budgerr's auto-settle/auto-log *consume* playstat's freshly-retrained output; systemd `After=`/`Wants=` ordering makes them wait for `playstat-mlb` to finish instead of both firing on fixed clocks — which matters more the slower the box (esp. the Pi). The curl jobs carry the `cron` `X-API-Key` (from an `EnvironmentFile`, not committed).

## 7. Data migration

- **Budgerr DB:** reuse `backend/ops/backup.sh` (age-encrypted `pg_dump -Fc`) → restore per `backend/ops/restore.md` into the `budgerr-db` volume. Fresh-install alternative: empty DB + `alembic upgrade head`.
- **playstat DB (~701 MB):** playstat's own `pg_dump`/`pg_restore`; schema ships as their `db/schema.sql`. **Coordinate timing around playstat migration `005`**, which merges ~2026-07-18 — don't snapshot the schema before it lands.
- Migration is a **deploy-time** step (gated), not part of this prep pass; `DEPLOY.md` documents the exact commands and a verification (row-count parity) + rollback.

## 8. `backend/Dockerfile` shape (Budgerr, authored here)

- Base `python:3.12-slim` (multi-arch; Budgerr requires ≥3.11). No build toolchain needed — `psycopg[binary]` ships wheels.
- Build context = **repo root** so both `backend/` and repo-root `static/` are available. Image layout mirrors the source: `/app/backend` (workdir) + `/app/static`, so `app.main`'s `../static` mount resolves.
- Install via `pip install ./backend` (from `pyproject.toml`), non-root user, `CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8001"]`.
- `.dockerignore` excludes `.venv`, `.git`, logs, `graphify-out`, node_modules.

## 9. Tailscale

- Box joins the owner's tailnet (`tailscale up`). MagicDNS gives it a stable name.
- `tailscale funnel` publishes `budgerr-api:8001` at a public `https://<box>.<tailnet>.ts.net` URL.
- `budgerr-web` (Vercel env) and the mobile app (`EXPO_PUBLIC_API_URL`) repoint to that URL.
- playstat stays off Funnel/Serve entirely (internal-only).

## 10. Cross-session coordination & dependencies

- **playstat authors** (at their own deploy green-light, not by us): their `Dockerfile`, the auth-exempt `/health`, and the exact `mlb` job chain. Their answers (2026-07-16): `python:3.11-slim`, `uvicorn api.main:app :8000`, own Postgres 14+ (16 fine), env `DATABASE_URL` / `PLAYSTAT_API_KEYS` / `AUTH_ENABLED` / `API_BASKETBALL_KEY` / `ODDS_API_KEY` (+ dashboard vars if `web/` deploys).
- **Timing:** playstat migration `005` merges ~2026-07-18.
- **Review gate:** the playstat architect reviews the `playstat-*` service blocks against the real running system before anything goes live.

## 11. `docs/DEPLOY.md` outline

Both hardware targets, end-to-end: OS prep (Ubuntu Server / Raspberry Pi OS 64-bit) → Docker + Compose plugin + Tailscale install → clone both repos side-by-side → per-service `.env` setup → `docker compose build && up` → both DB migrations (with verification + rollback) → install/enable systemd units → smoke tests (health, an authed API call, a proxied `/playstat/*` call, one manual job run) → Funnel publish + client repoint → the Pi retrain-time caveat and the public-API-key security note.

## 12. Acceptance criteria (for the eventual implementation, not this prep)

- `deploy/docker-compose.yml` builds and `up`s locally (dev smoke) with `budgerr-api` healthy and reaching a stubbed/real `playstat-api` over the internal network.
- `backend/Dockerfile` builds multi-arch and serves `/health` 200.
- systemd unit files are valid (`systemd-analyze verify`) and express the `After=` ordering.
- `DEPLOY.md` is followable end-to-end by someone with only SSH access to a fresh box.
- Backend test suite stays green; the `/health` exemption has a test.
- Nothing in the playstat repo is modified.
