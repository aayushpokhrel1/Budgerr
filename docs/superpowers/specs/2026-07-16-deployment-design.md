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
| `budgerr-api` | build `backend/Dockerfile` (context = `backend/`) | **public via Funnel** | `uvicorn app.main:app --host 0.0.0.0 --port 8001` |
| `playstat-api` | build from playstat's own Dockerfile (**they author**) | internal only | `uvicorn api.main:app --host 0.0.0.0 --port 8000` |
| `playstat-dashboard` | playstat `web/` `next start` :3000 | *(optional, compose profile, off by default)* | playstat's own dashboard, only if wanted remotely |

**Two separate Postgres services**, not one shared instance: respects playstat's ownership boundary (its own schema/dump/restore), keeps backups independent, and 8 GB easily absorbs two small Postgres containers.

**Cross-repo layout on the box:** `~/dev/Budgerr` and `~/dev/playstat` sit side by side; the compose file references playstat via a relative build context (`../../playstat`). `DEPLOY.md` documents the required directory layout. **Budgerr never writes into the playstat repo.**

## 4. Networking & exposure

- **Internal Compose network** joins all services. Budgerr's playstat proxy switches its upstream from `localhost:8000` → `http://playstat-api:8000` (Compose service DNS). The playstat `X-API-Key` continues to be injected server-side; it never leaves the box.
- **Only `budgerr-api` is exposed to the internet, via Tailscale Funnel** (public HTTPS URL, Tailscale-managed TLS, no inbound firewall ports — the tunnel is outbound-initiated). Required because `budgerr-web` is hosted publicly (Vercel) and cannot reach a tailnet-only address.
- **`playstat-api` is never publicly exposed.** It is consumed only server-side by Budgerr's proxy over the internal network. This keeps playstat at zero public attack surface.
- **Clients:** both `budgerr-web` (Vercel) and the mobile app point at the Funnel URL and attach their `X-API-Key`. The phone therefore needs **no** Tailscale app.
- **Security tradeoff (call out prominently in DEPLOY.md):** the public Funnel URL means the Budgerr `X-API-Key` is the *sole* gate to personal financial + betting data. Mitigations: keys are per-consumer and constant-time-compared already; use long random keys, rotate on any suspicion, and remember rotating the `cron` key means updating the cron unit files too. If the owner later wants zero public surface, the fallback is Tailscale **Serve** (tailnet-only) for the API + a tailnet-reachable web host — documented but not the chosen path.

## 5. `/health` auth exemption (small backend change, owned here)

Today Budgerr's `/health` is behind the global `require_api_key` dependency (returns 401 without a key), which makes a clean container healthcheck awkward. Change: exempt `/health` from the guard so orchestration can probe it unauthenticated (it leaks only `{"status":"ok"}`). This mirrors the auth-exempt `/health` playstat is adding on their side.

- **Compose healthchecks:** `budgerr-api` → `GET /health` expects 200 (via the image's own `HEALTHCHECK`, stdlib probe since `-slim` lacks curl). `playstat-api` → `curl -fsS http://localhost:8000/health` expects 200 — **delivered by playstat as of 82de4db**: auth-exempt via their `api/auth.py` `PUBLIC_PATHS`, returns `{"status":"ok","database":"ok"}` and **503 when Postgres is unreachable** (a real DB check, not just liveness), and their image installs `curl` for exactly this. The earlier "assert 401 on `/edges`" fallback is obsolete and has been dropped.
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
- Build context = **`backend/`** — the whole app is self-contained there (`app/`, `static/` at `backend/static`, `pyproject.toml`). Copy the context to `/app`, workdir `/app`; `app.main`'s mount `Path(__file__).parent.parent / "static"` then resolves to `/app/static`.
- Install with **`pip install -e .`** (editable): a normal wheel install puts `app` in site-packages *without* `static/` alongside it, breaking the `__file__`-relative mount — the exact reason CI uses `-e`. Editable keeps the source (and `static/`) in place at `/app`.
- Non-root user, `CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8001"]`.
- `backend/.dockerignore` excludes `.venv`, `.git`, `*.log`, `__pycache__`, `.pytest_cache`, `graphify-out`.

## 9. Tailscale

- Box joins the owner's tailnet (`tailscale up`). MagicDNS gives it a stable name.
- `tailscale funnel` publishes `budgerr-api:8001` at a public `https://<box>.<tailnet>.ts.net` URL.
- `budgerr-web` (Vercel env) and the mobile app (`EXPO_PUBLIC_API_URL`) repoint to that URL.
- playstat stays off Funnel/Serve entirely (internal-only).

## 10. Cross-session coordination & dependencies

- **playstat has now DELIVERED (2026-07-17, playstat commit `82de4db`)** — authored on their side, never by us: their `Dockerfile` (build-verified: `python:3.11-slim`, **installs `libgomp1`** because xgboost links OpenMP at runtime and slim omits it — without it the image builds then dies at `import xgboost`; plus `curl` for the healthcheck; 591 MB amd64; `.dockerignore` excludes `.env`/`web/`/`.venv`), the auth-exempt `/health`, and the `mlb` chain definition. Serve: `uvicorn api.main:app --host 0.0.0.0 --port 8000`. Own Postgres **14.22** (target 14+; our `postgres:16-alpine` is acceptable), env `DATABASE_URL` / `PLAYSTAT_API_KEYS` / `AUTH_ENABLED` / `API_BASKETBALL_KEY` (required at import — process won't start without it) / `ODDS_API_KEY` / optional `CORS_ORIGINS`.
- **One image, two uses:** the same playstat image serves the API (default CMD) *and* runs the daily batch chain as different commands (`python -m modeling.clv`, `python -m optimizer.parlay --target-payout 2.0 --max-legs 3`, …). The batch modules are deliberately in the image — do not strip them as "API-only deadweight". Task 4's `playstat-mlb` unit invokes them via `docker compose run --rm playstat-api <cmd>`.
- **arm64 is expected-good but NOT proven** — playstat build-verified only `linux/amd64`. All needed wheels (xgboost, scipy, numpy, psycopg2-binary) publish arm64, but if the Pi is chosen, do a `--platform linux/arm64` build **early** rather than discovering a gap at deploy. DEPLOY.md must say this.
- **The playstat dashboard (`web/`, Next.js :3000) is NOT in our compose** (deliberately out of scope). If it is ever wanted on the box it is a separate service needing its own `PLAYSTAT_API_KEY` / `DASHBOARD_USER` / `DASHBOARD_PASSWORD_HASH` / `SESSION_SECRET`; tell the playstat session if that changes.
- **playstat's preferred deploy timing** (their call, owner-gated): after their pending merges land and the chain runs clean with them, and after the paper-trading ledger has a few days of real settled results (~2026-07-18+), so what moves is measured rather than hoped-for. Nothing blocks authoring the compose now.
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
