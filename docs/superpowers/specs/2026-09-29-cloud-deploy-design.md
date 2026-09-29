# Cloud deploy design: Oracle Always Free + Supabase

Supersedes the hardware half of
[`2026-07-16-deployment-design.md`](2026-07-16-deployment-design.md). That design
is otherwise still correct: the compose topology, the systemd units, the
Tailscale Funnel exposure and the API-key security model all carry over
unchanged. What changes is **which box** and **which Postgres**, and those two
changes unblock a deploy that had been stalled since July on a hardware
question.

Owner gave the go-ahead on 2026-09-29. `docs/DEPLOY.md`'s "this is prep only,
nothing is deployed" gate is therefore lifted and that file must be rewritten to
match this design.

## 1. Why this, and what problem it actually solves

The goal is **work on Budgerr from any machine**. The code was never the
blocker: all three repos are on GitHub and clean. The blocker is that the
backend only runs on the Mac, because the LaunchAgents, the venv, the Postgres
volume and the `.env` all live there. A checkout on any other machine can read
and edit but cannot run or verify anything.

The July design already answered this (move both APIs to an always-on box,
expose via Funnel, any machine becomes a thin client). It stalled on one
sentence in §2: do not commit to the Pi 5 without first measuring playstat's
~1M-row XGBoost retrain on real hardware. Nobody measured it, so nothing moved
for two months.

**The fix is to stop treating the hardware choice as a prerequisite.** Oracle
Cloud's Always Free tier provides a `VM.Standard.A1.Flex` instance with 4 ARM
OCPUs and 24 GB RAM at no cost. That is materially more CPU and roughly 3x the
RAM of the Pi 5 that prompted the caveat, so the retrain question stops being a
gate rather than being answered. The Pi-vs-laptop table in DEPLOY.md §2 and its
retrain caveat are now moot and must be struck, not left sitting above a newer
note.

## 2. Target architecture

```
Oracle Always Free VM (Ubuntu 24.04, ARM64)
  docker compose (deploy/docker-compose.yml)
    budgerr-api    :8001 on loopback  -->  Tailscale Funnel  -->  public HTTPS
    [playstat-api  :8000 internal only  — added in a later session]
    [playstat-db                        — added in a later session]
  systemd timers: plaid-sync, auto-settle, auto-log, backup

Supabase (managed Postgres 16)   <-- budgerr-api over the session pooler
```

Deltas from the July design:

| July design | Now | Why |
|---|---|---|
| Pi 5 or old laptop | Oracle Always Free A1 (4 OCPU / 24 GB, ARM64) | Free, always-on, and enough CPU that the retrain caveat stops gating anything. Dockerfile is already multi-arch ARM64. |
| `budgerr-db` compose service on a named volume | Supabase managed Postgres | Owner already runs a Supabase project. Removes a stateful service from the box, which makes the box disposable (see §5). |
| Restore the Mac's dump on day one | **Start empty**, `alembic upgrade head` | Owner's call. Consequences in §4 — read them, they are not free. |
| Both APIs move together, one cutover | Budgerr first, playstat in a later session | The coupling is what stalled this. §6 shows it costs almost nothing to split. |

Everything else is unchanged: systemd timers (not a rewrite to GitHub Actions
cron, which was considered and rejected below), Funnel, per-consumer API keys,
loopback-only container port.

**GitHub Actions cron was considered and rejected.** Three of the four
scheduled jobs are a `curl POST` with the `cron` key, so Actions could host
them and would suit a host with no cron. Oracle gives a real always-on box, the
systemd units in `deploy/systemd/` are already authored and `systemd-analyze
verify`-clean, and moving them to Actions would add a second place secrets live
and require the Funnel URL to be reachable from GitHub. Keeping systemd is the
smaller change.

## 3. Portability is a requirement, not a hope

The owner picked Supabase with the explicit condition that moving off it later
stays easy. It currently is, and the job here is to not lose that:

- The backend talks to Postgres **only** through `DATABASE_URL`
  ([`backend/app/config.py:9`](../../../backend/app/config.py)) over plain
  SQLAlchemy + psycopg3. Alembic owns the schema.
- There is **no** Supabase client library, no PostgREST, no `supabase-py`, no
  RLS policy the app depends on, no auth coupling (Budgerr's auth is its own
  `X-API-Key` check). Migrating off Supabase is changing one environment
  variable and restoring a `pg_dump`.
- **This must stay true.** A future session reaching for `supabase-py`, Supabase
  Auth, RLS, storage or realtime would convert a one-variable move into a
  rewrite. Record the constraint in `docs/ARCHITECT.md` where the data layer is
  described, so it is read before anyone adds a dependency.
- Keeping our own `age`-encrypted `pg_dump`
  ([`backend/ops/backup.sh`](../../../backend/ops/backup.sh)) is the mechanical
  enforcement of this: as long as a plain dump runs nightly and restores, no
  vendor lock-in can accumulate quietly. Supabase's free tier gives daily
  backups with short retention and no PITR, so our own backup is load-bearing
  regardless.

### Two Supabase connection traps

Both are configuration-only, but each produces a confusing failure:

1. **The direct connection is IPv6-only** on projects created recently. An
   IPv4-only host resolves it and then times out, which reads like a firewall
   problem rather than an address-family problem.
2. **The transaction pooler (port 6543) breaks psycopg3's prepared
   statements.** You get intermittent `prepared statement "_pg_..." already
   exists` errors under reuse, not a clean failure at startup.

Use the **session pooler** (port 5432 on the `...pooler.supabase.com` host). It
is IPv4-reachable and holds a real session, so prepared statements work. This
belongs as a comment beside the `database_url` setting, not only in prose here,
because the failure mode is non-obvious and the next person to touch it will be
reading `config.py`.

## 4. Consequences of starting empty — read before executing

`alembic upgrade head` against a fresh Supabase database means the cloud
instance has no history, and the Mac's data is not migrated. Two of these are
not obvious:

- **Every bank account must be re-linked.** Plaid access tokens are rows in the
  `plaid_items` table ([`backend/app/models/plaid_items.py`](../../../backend/app/models/plaid_items.py)).
  An empty DB has no tokens, so the cloud backend cannot sync any account until
  each institution is re-linked through `/link-bank`. Budget for that as an
  actual step, not a footnote.
- **Transaction history does not come back.** Plaid's sync only backfills a
  limited window, so months of categorized transactions and any budget periods
  built on them are gone from the cloud instance.
- **Bet history and bankroll start at zero.** Everything under `bets` is Mac-only.
- **The two databases diverge the moment you start using the cloud one.** There
  is no merge path afterward. If the history turns out to matter, the window to
  change your mind is before you start entering data in the cloud instance, and
  the fix is the restore path in DEPLOY.md §7A from the Mac's latest encrypted
  backup.

The Mac keeps running and keeps its data throughout. Rollback is "keep using
the Mac", which costs nothing. Nothing on the Mac is stopped, disabled or
deleted as part of this work.

## 5. The box is disposable, and that matters here

Oracle reclaims Always Free compute instances it judges idle (roughly: 95th
percentile CPU, network and memory all under 20% across 7 days). A single-user
API is a plausible candidate. This is a real risk and worth naming rather than
discovering.

Moving Postgres to Supabase happens to make it cheap. The box holds no
persistent state except `backend/.env`, the age keys and the cloned repos, so a
reclaimed instance is a rebuild from this runbook, not data loss. Keep the
secrets recoverable off-box (password manager) and reclaim becomes an
inconvenience measured in minutes.

The same property is why the compose file loses its `budgerr-db` service: a
stateless box is one you can rebuild, relocate, or replace with a different
provider without a migration.

## 6. Splitting playstat off costs almost nothing

The July design insisted both APIs move together. Inspecting the actual coupling
shows the split is nearly free, because both call sites already degrade:

- `PLAYSTAT_BASE_URL` is a plain env var
  ([`config.py:18`](../../../backend/app/config.py)), so where playstat lives is
  configuration, not architecture.
- The proxy returns `502 {"detail": "playstat upstream unavailable"}` on any
  `httpx.RequestError`
  ([`routers/playstat_proxy.py:29`](../../../backend/app/routers/playstat_proxy.py)).
  The web `/tonight` page and builder parlays degrade instead of erroring out.
- Auto-settle catches `httpx.HTTPError` per game date and moves on
  ([`auto_settlement.py:88`](../../../backend/app/auto_settlement.py)), leaving
  bets pending rather than failing the job. Bets settle whenever playstat is
  reachable again.

So Budgerr ships to the cloud with `PLAYSTAT_BASE_URL` pointing at nothing
reachable, and everything except playstat-backed features works. Two ways to
restore full parity, in order of preference:

1. **Move playstat onto the same box** (a later session). The compose file
   already has `playstat-db` and `playstat-api` authored with
   `build: ../../playstat`, no public port, reachable at
   `http://playstat-api:8000`. It is `docker compose up` on a stack that is
   already running, not a second deploy. This is the intended end state.
2. **Tailscale sidecar to the Mac's playstat**, as an interim. The box is on the
   tailnet anyway for Funnel, so `PLAYSTAT_BASE_URL=http://<mac>.<tailnet>.ts.net:8000`
   works whenever the Mac is awake. Only worth doing if the gap before (1)
   turns out to be long; note that playstat must never be exposed via Funnel,
   per the July design's §9.

Until then, DEPLOY.md's smoke test 4 (`/playstat/edges` → 200) correctly
expects **502**, and that is a pass, not a failure. Say so in the runbook or the
next person will chase it.

## 7. Work, in order

Each step is independently verifiable, and nothing touches the Mac.

1. **`deploy/docker-compose.yml`** — drop the `budgerr-db` service, its volume,
   its `depends_on` and the `DATABASE_URL` override so `backend/.env` owns the
   connection string. Leave the playstat services in place, untouched and
   commented as future work. Verify with `docker compose config --quiet` (never
   bare `docker compose config` — it dumps resolved secrets to stdout).
2. **`backend/ops/backup.sh`** — it currently dumps via
   `docker exec "$CONTAINER" pg_dump`, which has no meaning without a local DB
   container. Add a URL path: when `BUDGERR_DB_URL` is set, `pg_dump -Fc "$url"`
   directly. Default behaviour on the Mac stays byte-identical. Note at the code
   that `DATABASE_URL`'s SQLAlchemy `+psycopg` dialect suffix must be stripped
   before `pg_dump` will accept the string; that is exactly the kind of thing
   that fails at 03:00 and nowhere else.
3. **`config.py`** — comment the session-pooler requirement beside
   `database_url` (§3).
4. **`docs/DEPLOY.md`** — rewrite to this design. Lift the "prep only" gate,
   strike §2's hardware table and retrain caveat, replace §3 with Oracle OS prep
   (Ubuntu 24.04 ARM64, the `ubuntu` user, and the point that OCI images ship
   restrictive `iptables` rules plus VCN security lists — which Funnel sidesteps
   entirely, since it is outbound-initiated and needs no inbound port), rewrite
   §5 and §7 for Supabase and the empty start, add §4's re-link step, mark
   smoke test 4 as expecting 502, and add the Supabase traps and the Oracle
   reclaim policy to §14.
5. **`docs/OPERATIONS.md`** — §1 currently states "the project runs on a Mac"
   and that a non-Mac checkout can verify nothing. That becomes false the day
   this ships. Correct it in place rather than appending.
6. **`docs/ARCHITECT.md`** — record the §3 portability constraint at the data
   layer.
7. **`PRODUCT.md`** — the deploy roadmap entry says gated on the owner and
   hardware. Both are resolved; make it true.
8. **Execute the runbook** on the Oracle instance, then repoint `budgerr-web`
   (Vercel env var) and `budgerr-app` (`EXPO_PUBLIC_API_URL`), and add the
   Vercel origin to `CORS_ORIGINS`.

Steps 1-3 are code and can be verified by CI (`pytest -q` in `backend/`) before
any cloud account exists. Step 8 is the only step that needs the instance.

## 8. What stays on the Mac

The Mac's launchd stack, its Postgres volume and its backups are untouched and
remain authoritative for the data they hold. Turning any of it off is a separate
decision for after the cloud instance has run correctly for a real stretch of
days, and it is the owner's call, not a session's.
