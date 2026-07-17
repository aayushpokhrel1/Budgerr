# Budgerr + playstat Deployment Prep — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Author (and commit) the artifacts to run Budgerr + playstat as one containerized stack on a dedicated box — Dockerfile, combined Compose, systemd units, DEPLOY.md — plus a small backend auth hardening. **Prep only: nothing is deployed, no hardware touched, no data migrated.**

**Architecture:** One `deploy/docker-compose.yml` runs `budgerr-api` + its Postgres and `playstat-api` + its *separate* Postgres. `budgerr-api` is the only publicly-exposed service (Tailscale Funnel, at deploy time); `playstat-api` is internal-only, reached by Budgerr's proxy over the Compose network. launchd jobs become systemd timers with ordering so Budgerr's morning jobs run after playstat's retrain.

**Tech Stack:** FastAPI/uvicorn, Postgres 16 (alpine), Docker Compose, systemd, Tailscale, pytest. Base images multi-arch (ARM64 Pi 5 / x86_64 laptop).

**Design spec:** `docs/superpowers/specs/2026-07-16-deployment-design.md` (read it first).

## Global Constraints

- **Never modify the `~/dev/playstat` repo.** playstat authors its own Dockerfile and `/health`. This plan references playstat; it never writes into it.
- **Prep only.** No `docker compose up` against real data, no hardware, no DB migration, no Tailscale changes on any real machine. Local smoke tests use throwaway containers only.
- Base images: `postgres:16-alpine`, `python:3.12-slim` (both multi-arch). Backend requires Python ≥3.11.
- The backend test suite must stay green (74 passing + the new auth tests).
- **graphify:** before exploring code run `graphify query "<question>"`; after any code change run `graphify update .` (AST-only, free). Only read raw source after graphify orients you, or to edit specific lines.
- Worker git rules: work on a branch, **commit but never push**, end every commit message with:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- Budgerr backend serves from `backend/`: `uvicorn app.main:app`. `app.main` mounts `StaticFiles` from `backend/static` via `Path(__file__).parent.parent / "static"` — this is why the image needs an **editable** install.

---

### Task 1: Backend auth hardening — exempt `/health`, gate docs behind the key

**Why:** The container healthcheck needs an unauthenticated `/health`. Separately, FastAPI's auto-generated `/openapi.json`, `/docs`, `/redoc` bypass the app-level auth dependency (verified: they return 200 without a key), which would publicly expose the full API schema once the API is on a public Funnel URL. Fix both.

**Files:**
- Modify: `backend/app/auth.py` (add exempt-path check)
- Modify: `backend/app/main.py:11-32` (disable built-in docs; add key-gated `/openapi.json`, `/docs`, `/redoc`)
- Test: `backend/tests/test_auth.py`

**Interfaces:**
- Consumes: `require_api_key(request: Request) -> None` (`app/auth.py:45`), `AUTH_ENABLED`, `API_KEYS` (monkeypatched in tests).
- Produces: module constant `auth.AUTH_EXEMPT_PATHS: frozenset[str]`; `/health` served without auth; `/openapi.json`, `/docs`, `/redoc` require a valid `X-API-Key` when `AUTH_ENABLED`.

- [ ] **Step 1: Write the failing tests.** Replace the body of `backend/tests/test_auth.py` with:

```python
import pytest
from fastapi.testclient import TestClient

from app import auth
from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_auth_disabled_by_default_allows_request_with_no_key(client, monkeypatch):
    monkeypatch.setattr(auth, "AUTH_ENABLED", False)
    monkeypatch.setattr(auth, "API_KEYS", {})

    resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_is_exempt_even_when_auth_enabled(client, monkeypatch):
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "API_KEYS", {"web": "secret123"})

    resp = client.get("/health")  # no key

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_auth_enabled_missing_key_returns_401(client, monkeypatch):
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "API_KEYS", {"web": "secret123"})

    resp = client.get("/openapi.json")  # protected, no DB

    assert resp.status_code == 401
    assert resp.json() == {"detail": "missing or invalid API key"}


def test_auth_enabled_wrong_key_returns_401(client, monkeypatch):
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "API_KEYS", {"web": "secret123"})

    resp = client.get("/openapi.json", headers={"X-API-Key": "wrong-key"})

    assert resp.status_code == 401
    assert resp.json() == {"detail": "missing or invalid API key"}


def test_auth_enabled_valid_key_allows_openapi(client, monkeypatch):
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "API_KEYS", {"web": "secret123", "mobile": "othersecret"})

    resp = client.get("/openapi.json", headers={"X-API-Key": "secret123"})

    assert resp.status_code == 200
    assert resp.json()["info"]["title"] == "Budgerr"


def test_docs_requires_key(client, monkeypatch):
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "API_KEYS", {"web": "secret123"})

    assert client.get("/docs").status_code == 401
    assert client.get("/docs", headers={"X-API-Key": "secret123"}).status_code == 200
```

- [ ] **Step 2: Run the tests to verify they fail.**

Run: `cd backend && .venv/bin/pytest tests/test_auth.py -v`
Expected: `test_health_is_exempt...` currently PASSES (health already 200 with valid... no — with AUTH on and no key it currently 401s → FAILS); `test_auth_enabled_missing_key_returns_401` and `test_docs_requires_key` FAIL (openapi/docs currently return 200 without a key). At least three failures.

- [ ] **Step 3: Exempt `/health` in `backend/app/auth.py`.** Add, immediately after the `API_KEYS = _parse_keys(...)` line (auth.py:42):

```python
# Paths served without auth so orchestration (Docker/Compose healthchecks,
# systemd) can probe liveness. Leaks only {"status": "ok"}.
AUTH_EXEMPT_PATHS = frozenset({"/health"})
```

Then change the body of `require_api_key` (auth.py:45) so the exempt check is first:

```python
def require_api_key(request: Request) -> None:
    """Global FastAPI dependency: reject requests without a valid X-API-Key.

    No-op when AUTH_ENABLED is false. Never includes the presented key (or
    any configured key) in errors or logs.
    """
    if request.url.path in AUTH_EXEMPT_PATHS:
        return
    if not AUTH_ENABLED:
        return
    presented = request.headers.get("X-API-Key", "")
    valid = False
    for key in API_KEYS.values():
        if secrets.compare_digest(presented, key):
            valid = True
    if not valid:
        raise HTTPException(status_code=401, detail="missing or invalid API key")
```

- [ ] **Step 4: Gate docs behind auth in `backend/app/main.py`.** The built-in docs routes bypass the app-level dependency, so disable them and re-add guarded ones. Replace lines 1-11 imports + app construction:

```python
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.auth import require_api_key
from app.config import settings
from app.routers import bet_import, bets, budgeting, plaid, playstat_proxy, rewards

# docs_url/redoc_url/openapi_url=None disables FastAPI's built-in docs routes,
# which bypass the app-level auth dependency. They are re-added below as normal
# routes so `Depends(require_api_key)` covers them.
app = FastAPI(
    title="Budgerr",
    dependencies=[Depends(require_api_key)],
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
```

Then add, immediately after the existing `@app.get("/health")` block (end of file):

```python
@app.get("/openapi.json", include_in_schema=False)
def openapi_json() -> JSONResponse:
    return JSONResponse(app.openapi())


@app.get("/docs", include_in_schema=False)
def swagger_ui() -> HTMLResponse:
    return get_swagger_ui_html(openapi_url="/openapi.json", title="Budgerr docs")


@app.get("/redoc", include_in_schema=False)
def redoc() -> HTMLResponse:
    return get_redoc_html(openapi_url="/openapi.json", title="Budgerr docs")
```

- [ ] **Step 5: Run the tests to verify they pass.**

Run: `cd backend && .venv/bin/pytest tests/test_auth.py -v`
Expected: all six PASS.

- [ ] **Step 6: Run the full suite (no regressions) and update graphify.**

Run: `cd backend && .venv/bin/pytest`
Expected: all pass (was 74; now +2 auth tests).
Run: `graphify update .`

- [ ] **Step 7: Commit.**

```bash
git add backend/app/auth.py backend/app/main.py backend/tests/test_auth.py graphify-out
git commit -m "backend: exempt /health from auth, gate openapi/docs behind the API key

Container healthchecks need an unauthenticated /health. FastAPI's built-in
/openapi.json,/docs,/redoc bypass the app-level auth dependency (they 200
without a key), which would expose the full API schema on the public Funnel
URL; re-add them as key-gated routes.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `backend/Dockerfile` + `.dockerignore`

**Why:** Containerize the Budgerr API, multi-arch, with the editable install so the static mount resolves.

**Files:**
- Create: `backend/Dockerfile`
- Create: `backend/.dockerignore`

**Interfaces:**
- Consumes: `backend/pyproject.toml`, `backend/app/`, `backend/static/`. Depends on Task 1 only for the healthcheck semantics (unauthenticated `/health`).
- Produces: an image serving `uvicorn app.main:app` on `:8001` with a `/health` HEALTHCHECK.

- [ ] **Step 1: Create `backend/.dockerignore`:**

```
.venv
__pycache__
*.pyc
.pytest_cache
*.log
.git
.env
graphify-out
```

(Excluding `.env` keeps secrets out of the image — they are injected at runtime via Compose `env_file`.)

- [ ] **Step 2: Create `backend/Dockerfile`:**

```dockerfile
# Multi-arch (linux/arm64 for Pi 5, linux/amd64 for the laptop).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Build context is backend/, so this copies app/, static/, pyproject.toml, etc.
COPY . /app

# Editable install: a plain wheel install puts `app` in site-packages WITHOUT
# static/ alongside it, breaking app.main's __file__-relative StaticFiles mount.
# psycopg[binary] ships wheels, so no libpq build toolchain is needed.
RUN pip install -e .

RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8001

# /health is auth-exempt (Task 1); no key needed. curl is absent from -slim,
# so probe with stdlib urllib.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8001/health').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
```

- [ ] **Step 3: Build the image.**

Run: `cd ~/dev/Budgerr && docker build -t budgerr-api:smoke backend`
Expected: builds successfully (if the Docker daemon is down: `open -a Docker && docker compose up -d` first, per the repo's Docker gotcha).

- [ ] **Step 4: Smoke-run the container and hit `/health`.** `/health` needs no DB, so no Postgres required:

```bash
docker run -d --name budgerr-smoke -p 8099:8001 -e AUTH_ENABLED=false budgerr-api:smoke
sleep 4
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8099/health   # expect 200
docker rm -f budgerr-smoke
```

Expected: `200`.

- [ ] **Step 5: Commit.**

```bash
git add backend/Dockerfile backend/.dockerignore
git commit -m "deploy: multi-arch Dockerfile for the Budgerr API (editable install for static mount)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `deploy/docker-compose.yml` + `deploy/.env.example`

**Why:** The combined stack. Two separate Postgres services; Budgerr public-facing, playstat internal-only.

**Files:**
- Create: `deploy/docker-compose.yml`
- Create: `deploy/.env.example`

**Interfaces:**
- Consumes: `backend/Dockerfile` (Task 2), `../playstat` (playstat's own Dockerfile, authored on their side).
- Produces: services `budgerr-db`, `budgerr-api` (host `:8001`), `playstat-db`, `playstat-api` (internal only). Budgerr reaches playstat at `http://playstat-api:8000`.

- [ ] **Step 1: Create `deploy/docker-compose.yml`:**

```yaml
name: budgerr-stack

services:
  budgerr-db:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: budgerr
      POSTGRES_PASSWORD: budgerr
      POSTGRES_DB: budgerr
    volumes:
      - budgerr_pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U budgerr"]
      interval: 10s
      timeout: 5s
      retries: 5

  budgerr-api:
    build:
      context: ../backend
    restart: unless-stopped
    depends_on:
      budgerr-db:
        condition: service_healthy
    # Secrets (BUDGERR_API_KEYS, AUTH_ENABLED, PLAID_*, PLAYSTAT_API_KEY,
    # NTFY_*, ANTHROPIC_API_KEY) come from backend/.env. The two overrides
    # below repoint the connection URLs at the compose network.
    env_file:
      - ../backend/.env
    environment:
      DATABASE_URL: postgresql+psycopg://budgerr:budgerr@budgerr-db:5432/budgerr
      PLAYSTAT_BASE_URL: http://playstat-api:8000
    ports:
      - "8001:8001"   # host-published so Tailscale Funnel can wrap it at deploy time

  playstat-db:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: playstat
      POSTGRES_PASSWORD: playstat
      POSTGRES_DB: playstat
    volumes:
      - playstat_pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U playstat"]
      interval: 10s
      timeout: 5s
      retries: 5

  playstat-api:
    # playstat authors this Dockerfile in the playstat repo (../playstat).
    # Budgerr never writes into that repo. Until it exists, bring the stack up
    # with the Budgerr services only:
    #   docker compose up -d budgerr-db budgerr-api
    build:
      context: ../playstat
    restart: unless-stopped
    depends_on:
      playstat-db:
        condition: service_healthy
    env_file:
      - ../playstat/.env
    environment:
      DATABASE_URL: postgresql+psycopg2://playstat:playstat@playstat-db:5432/playstat
    # No `ports:` — playstat is internal-only. Budgerr's proxy reaches it at
    # http://playstat-api:8000 over the compose network. Never exposed publicly.

volumes:
  budgerr_pgdata:
  playstat_pgdata:
```

- [ ] **Step 2: Create `deploy/.env.example`** (documentation only — real secrets live in `backend/.env` and `playstat/.env`, both gitignored):

```bash
# deploy/.env.example — reference for what each service's env must contain.
# The compose file loads real values from ../backend/.env and ../playstat/.env.
# Do NOT put secrets in this file.

# ---- budgerr-api (from backend/.env) ----
# AUTH_ENABLED=true
# BUDGERR_API_KEYS=web:<key>,mobile:<key>,cron:<key>
# DATABASE_URL is OVERRIDDEN by compose to budgerr-db (ignore backend/.env's value)
# PLAYSTAT_BASE_URL is OVERRIDDEN by compose to http://playstat-api:8000
# PLAYSTAT_API_KEY=<budgerr key provisioned in playstat's PLAYSTAT_API_KEYS>
# PLAID_CLIENT_ID / PLAID_SECRET / PLAID_ENV
# CORS_ORIGINS=https://<your-vercel-web-domain>   # public web origin
# NTFY_TOPIC=<hard-to-guess topic>   ANTHROPIC_API_KEY (optional)

# ---- playstat-api (from playstat/.env; owned by the playstat session) ----
# DATABASE_URL is OVERRIDDEN by compose to playstat-db
# PLAYSTAT_API_KEYS=budgerr:<same key as PLAYSTAT_API_KEY above>,...
# AUTH_ENABLED=true  API_BASKETBALL_KEY=<...>  ODDS_API_KEY=<...>
```

- [ ] **Step 3: Validate the compose file.**

Run: `cd ~/dev/Budgerr && docker compose -f deploy/docker-compose.yml config`
Expected: prints the fully-resolved config with no error (this validates YAML + interpolation for all four services without building anything). If `backend/.env`/`playstat/.env` are absent locally, create empty placeholders or expect an env-file warning — the structure must still resolve.

- [ ] **Step 4: Partial-stack smoke (Budgerr services only; playstat's Dockerfile may not exist yet).**

```bash
cd ~/dev/Budgerr/deploy
docker compose up -d --build budgerr-db budgerr-api
sleep 8
curl -s -o /dev/null -w "health:%{http_code}\n" http://127.0.0.1:8001/health          # expect 200
# with AUTH_ENABLED=true in backend/.env, openapi is now key-gated:
curl -s -o /dev/null -w "openapi-nokey:%{http_code}\n" http://127.0.0.1:8001/openapi.json  # expect 401
docker compose down
```

Expected: `health:200`, `openapi-nokey:401`. (If `backend/.env` has `AUTH_ENABLED=false`, openapi returns 200 — note which and move on; the container wiring is what's under test here.)

- [ ] **Step 5: Commit.**

```bash
git add deploy/docker-compose.yml deploy/.env.example
git commit -m "deploy: combined docker-compose (two Postgres, budgerr public, playstat internal)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: systemd units (`deploy/systemd/`)

**Why:** Replace the four Budgerr launchd jobs with systemd timers, with ordering so the morning jobs run after playstat's retrain. **Cannot be executed/verified on macOS** (no systemd) — author + structural-check here; `systemd-analyze verify` runs on the box (documented in DEPLOY.md).

**Files (create):**
- `deploy/systemd/budgerr-plaid-sync.service` / `.timer`
- `deploy/systemd/budgerr-auto-settle.service` / `.timer`
- `deploy/systemd/budgerr-auto-log.service` / `.timer`
- `deploy/systemd/budgerr-backup.service` / `.timer`
- `deploy/systemd/README.md` (install instructions + the playstat-mlb ordering note)

**Interfaces:**
- Consumes: the running `budgerr-api` container publishing `:8001` on the host; `/etc/budgerr/cron.env` holding `BUDGERR_CRON_KEY`.
- Produces: four timer-activated oneshot services. `budgerr-auto-settle`/`budgerr-auto-log` order `After=playstat-mlb.service` (soft — no `Wants=`, so they never *trigger* a retrain, only queue behind one already running).

- [ ] **Step 1: Create the three curl-job services.** Each is a oneshot that curls the containerized API with the cron key.

`deploy/systemd/budgerr-plaid-sync.service`:
```ini
[Unit]
Description=Budgerr Plaid sync
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
EnvironmentFile=/etc/budgerr/cron.env
ExecStart=/usr/bin/curl -fsS -X POST -H "X-API-Key: ${BUDGERR_CRON_KEY}" http://127.0.0.1:8001/plaid/sync-all
```

`deploy/systemd/budgerr-auto-settle.service`:
```ini
[Unit]
Description=Budgerr auto-settle bets
Wants=network-online.target
After=network-online.target playstat-mlb.service

[Service]
Type=oneshot
EnvironmentFile=/etc/budgerr/cron.env
ExecStart=/usr/bin/curl -fsS -X POST -H "X-API-Key: ${BUDGERR_CRON_KEY}" http://127.0.0.1:8001/bets/auto-settle
```

`deploy/systemd/budgerr-auto-log.service`:
```ini
[Unit]
Description=Budgerr auto-log parlay recommendations
Wants=network-online.target
After=network-online.target playstat-mlb.service

[Service]
Type=oneshot
EnvironmentFile=/etc/budgerr/cron.env
ExecStart=/usr/bin/curl -fsS -X POST -H "X-API-Key: ${BUDGERR_CRON_KEY}" http://127.0.0.1:8001/bets/auto-log-recommendations
```

- [ ] **Step 2: Create the backup service.** Runs the (container-aware) backup script; the script path and its `docker compose exec budgerr-db pg_dump` adaptation are covered in DEPLOY.md (Task 5).

`deploy/systemd/budgerr-backup.service`:
```ini
[Unit]
Description=Budgerr encrypted Postgres backup
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
# BUDGERR_HOME points at the repo checkout on the box (set in the env file).
EnvironmentFile=/etc/budgerr/cron.env
ExecStart=/bin/bash ${BUDGERR_HOME}/backend/ops/backup.sh
```

- [ ] **Step 3: Create the four timers.** Times shift the morning jobs *after* playstat's 08:30 retrain (which can run tens of minutes — longer on a Pi), fixing the pre-existing race. `Persistent=true` catches missed runs (box asleep/off).

`deploy/systemd/budgerr-plaid-sync.timer`:
```ini
[Unit]
Description=Budgerr Plaid sync daily 07:00

[Timer]
OnCalendar=*-*-* 07:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

`deploy/systemd/budgerr-auto-settle.timer` (was 08:30 → 09:15, after the retrain):
```ini
[Unit]
Description=Budgerr auto-settle daily 09:15

[Timer]
OnCalendar=*-*-* 09:15:00
Persistent=true

[Install]
WantedBy=timers.target
```

`deploy/systemd/budgerr-auto-log.timer` (was 09:00 → 09:45):
```ini
[Unit]
Description=Budgerr auto-log daily 09:45

[Timer]
OnCalendar=*-*-* 09:45:00
Persistent=true

[Install]
WantedBy=timers.target
```

`deploy/systemd/budgerr-backup.timer`:
```ini
[Unit]
Description=Budgerr backup daily 03:00

[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 4: Create `deploy/systemd/README.md`** with install steps and the coordination note:

```markdown
# Budgerr systemd units

Copy `*.service`/`*.timer` to `/etc/systemd/system/`, then:

    sudo systemctl daemon-reload
    sudo systemctl enable --now budgerr-plaid-sync.timer budgerr-auto-settle.timer \
        budgerr-auto-log.timer budgerr-backup.timer

Create `/etc/budgerr/cron.env` (mode 600, root-owned):

    BUDGERR_CRON_KEY=<the cron key from BUDGERR_API_KEYS>
    BUDGERR_HOME=/home/<user>/dev/Budgerr

## playstat ordering (coordinate with the playstat session)

`budgerr-auto-settle`/`budgerr-auto-log` declare `After=playstat-mlb.service` so
they queue behind playstat's morning retrain if it is still running. That unit
is authored on the **playstat** side. `After=` without `Wants=` is a soft
ordering: if `playstat-mlb.service` is absent, these jobs simply run at their
scheduled time. Confirm the real retrain duration on the chosen box and tune the
09:15/09:45 times if needed.

## Verify on the box (cannot run on macOS)

    systemd-analyze verify /etc/systemd/system/budgerr-*.service \
        /etc/systemd/system/budgerr-*.timer
    systemctl list-timers 'budgerr-*'
```

- [ ] **Step 5: Structural sanity-check the units** (no systemd on macOS, so grep the required keys):

```bash
cd ~/dev/Budgerr/deploy/systemd
for f in *.service; do grep -q '^\[Service\]' "$f" && grep -q '^ExecStart=' "$f" && echo "OK $f" || echo "BAD $f"; done
for f in *.timer; do grep -q '^\[Timer\]' "$f" && grep -q '^OnCalendar=' "$f" && grep -q '^WantedBy=timers.target' "$f" && echo "OK $f" || echo "BAD $f"; done
```

Expected: every line prints `OK ...`.

- [ ] **Step 6: Commit.**

```bash
git add deploy/systemd
git commit -m "deploy: systemd timers replacing launchd jobs (morning jobs ordered after playstat retrain)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: `docs/DEPLOY.md` runbook

**Why:** A followable end-to-end deploy guide for both hardware targets. No automated test — verify by spec-coverage review.

**Files:**
- Create: `docs/DEPLOY.md`

- [ ] **Step 1: Write `docs/DEPLOY.md`** with these sections and the exact commands. Keep it copy-pasteable.

1. **Scope & gate.** State: deployment is owner-gated; this doc is the runbook, run only when the owner green-lights. Both APIs move together.
2. **Choose the box.** Pi 5 8GB ARM64 vs old laptop x86_64 table (from the spec §2), including the **Pi retrain caveat** (playstat's daily XGBoost retrain on ~1M rows is materially slower on the Pi).
3. **OS prep.** Ubuntu Server 24.04 (laptop) or Raspberry Pi OS 64-bit (Pi): create user, `apt update`, install Docker Engine + compose plugin (`curl -fsSL https://get.docker.com | sh`, `usermod -aG docker $USER`), install Tailscale (`curl -fsSL https://tailscale.com/install.sh | sh`).
4. **Get the repos.** Clone `Budgerr` and `playstat` side-by-side under `~/dev/`. Note the compose expects `../backend`, `../playstat` relative to `deploy/`.
5. **Secrets.** Create `backend/.env` and `playstat/.env` from their examples (list every var from `deploy/.env.example`). Emphasize: use long random `BUDGERR_API_KEYS`; the `cron` key also goes in `/etc/budgerr/cron.env`.
6. **Build & start.** `cd Budgerr/deploy && docker compose build && docker compose up -d`. Note the playstat service needs playstat's Dockerfile present first (coordinate; until then `up -d budgerr-db budgerr-api`).
7. **Database migration** (both, with verification + rollback):
   - Budgerr: restore per `backend/ops/restore.md` into `budgerr-db`, or fresh `docker compose exec budgerr-api alembic upgrade head`.
   - playstat: its own `pg_dump`/`pg_restore` (~701 MB) into `playstat-db`; **coordinate around playstat migration 005 (~2026-07-18)** — do not snapshot before it lands. Verify with row-count parity vs the laptop.
8. **systemd.** Install units per `deploy/systemd/README.md`; run `systemd-analyze verify`; `systemctl list-timers 'budgerr-*'`. Note the backup script needs its `pg_dump` pointed at the container: `docker compose exec -T budgerr-db pg_dump ...`.
9. **Tailscale exposure.** `tailscale up`; publish Budgerr only: `tailscale funnel 8001` (or `tailscale serve` if the owner later chooses tailnet-only). **playstat is never exposed.** Record the public URL.
10. **Repoint clients.** Set the Vercel web env + mobile `EXPO_PUBLIC_API_URL` to the Funnel URL. Add that origin to Budgerr `CORS_ORIGINS`.
11. **Smoke tests.** `curl <url>/health` → 200; `curl -H "X-API-Key: <web>" <url>/bets/bankroll` → 200; `curl <url>/openapi.json` → 401 (no key); a proxied `curl -H "X-API-Key: <web>" <url>/playstat/edges` → 200; run one job manually.
12. **Security note (call out).** The Funnel URL is public; the `X-API-Key` is the sole gate to financial data. Rotate on suspicion; rotating the `cron` key means updating `/etc/budgerr/cron.env`.
13. **Rollback.** `docker compose down` on the box; the Mac's launchd services remain the source of truth until cutover is confirmed. Nothing is deleted from the Mac until parity is verified.

- [ ] **Step 2: Spec-coverage self-review.** Re-read `docs/superpowers/specs/2026-07-16-deployment-design.md` §1–§11 and confirm each point appears in DEPLOY.md or the artifacts. Fix gaps inline.

- [ ] **Step 3: Commit.**

```bash
git add docs/DEPLOY.md
git commit -m "docs: DEPLOY.md runbook for the combined stack (both hardware targets)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review (author's check against the spec)

- **§1 artifacts** → Tasks 1–5 cover Dockerfile, compose, systemd, DEPLOY.md, `/health` change. ✓
- **§2 design-for-both** → Dockerfile/compose use multi-arch base images; DEPLOY.md documents both targets + Pi caveat. ✓
- **§3 topology / two DBs** → Task 3 compose. ✓
- **§4 networking/exposure** → budgerr `:8001` published + Funnel (DEPLOY §9); playstat no ports (Task 3). ✓
- **§5 /health exemption + healthchecks** → Task 1 + Dockerfile/compose healthchecks. ✓ (Bonus: docs gating, an approved scope addition.)
- **§6 systemd + ordering** → Task 4 (`After=playstat-mlb.service`, shifted times). ✓
- **§7 migration** → DEPLOY §7. ✓
- **§8 Dockerfile shape** → Task 2 (context `backend/`, editable install). ✓
- **§9 Tailscale** → DEPLOY §9. ✓
- **§10 coordination** → playstat authors its Dockerfile/`/health`; migration 005 timing noted (Task 3 comments, systemd README, DEPLOY §7). ✓
- **§11 DEPLOY outline** → Task 5. ✓
- **§12 acceptance** → local smokes (Tasks 2–3), `systemd-analyze verify` on box (Task 4), suite green (Task 1), playstat repo untouched (global constraint). ✓
- **Placeholder scan:** none — all steps carry real code/commands. The playstat-mlb ExecStart is intentionally playstat's deliverable (documented), not a Budgerr placeholder.
- **Type/name consistency:** `AUTH_EXEMPT_PATHS`, `require_api_key`, `PLAYSTAT_BASE_URL`, service names (`budgerr-api`/`playstat-api`/`budgerr-db`/`playstat-db`) consistent across tasks. ✓
