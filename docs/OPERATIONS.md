# Budgerr Operations

How to run it, verify it, and keep it alive. Deploying to a new box is
`docs/DEPLOY.md`; what the system is is `docs/ARCHITECT.md`.

## 1. Which machine

A cloud deploy is designed and its runbook is ready (`docs/DEPLOY.md`). The target is
an Oracle Always Free instance with managed Supabase Postgres, and **it has not been
executed**, so everything in this section still describes reality. When it does ship,
the Mac stops being the only machine that can run Budgerr, and a checkout anywhere
becomes able to verify against the deployed API.

**The project runs on a Mac.** The scheduled jobs are launchd LaunchAgents, the
backup script shells out to macOS paths, and `docs/DEPLOY.md` targets a Linux box
for the eventual move. A checkout on another machine (Windows, for instance) is
fine for reading and editing, but it has no `backend/.venv`, no Postgres container,
and no scheduled jobs, so **nothing there can be verified by running it**. Do not
read an empty `docker ps` on such a box as an outage.

All four projects (`Budgerr`, `budgerr-app`, `budgerr-web`, `playstat`) live under a
plain development directory, deliberately **not** under `~/Documents`. On the Mac,
`~/Documents` is iCloud-synced, and that sync caused intermittent file-read
deadlocks (`OSError: [Errno 11]`) specifically for launchd-spawned processes.

Python venvs and `node_modules` embed absolute paths and are not portable, so moving
any of these projects means rebuilding those, not copying the folder.

## 2. Local services

| Thing | Where | Notes |
| --- | --- | --- |
| Backend | port **8001**, venv `backend/.venv` | launchd `com.budgerr.backend` |
| playstat | port **8000** | separate project, **never modify it** |
| Postgres | Docker compose at repo root, port **5433**, user and db `budgerr` | `restart: unless-stopped` |

Restart the backend:

```bash
launchctl kickstart -k gui/$(id -u)/com.budgerr.backend
```

It takes roughly 15 seconds to come back. Postgres shell:

```bash
docker exec budgerr-postgres-1 psql -U budgerr -d budgerr
```

Docker Desktop may not be running after a reboot. Start it, then
`docker compose up -d`.

The backend runs as a LaunchAgent (`~/Library/LaunchAgents/com.budgerr.backend.plist`,
**not tracked in git**) so it survives logout and reboot and restarts on crash. This
is the interim arrangement until the move in `docs/DEPLOY.md` happens.

## 3. Scheduled jobs

All launchd, all curl against the local backend with the `cron` API key.

| Job | Time | Hits | State |
| --- | --- | --- | --- |
| `com.budgerr.plaid-sync` | 7:00am | `POST /plaid/sync-all` | active |
| `com.budgerr.auto-settle` | 8:30am | `POST /bets/auto-settle` | active |
| `com.budgerr.backup` | 3:00am | `backend/ops/backup.sh` | active |
| `com.budgerr.auto-log-parlays` | 9:00am | `POST /bets/auto-log-recommendations` | **disabled** |

`auto-settle` runs at 8:30am because playstat backfills box scores at 8am. That
ordering matters: settle before the data lands and it resolves nothing.

`plaid-sync` syncs every linked Plaid item, tolerating per-item failures, and
recomputes budget periods for each touched month. Alert-threshold checks run inside
that recompute, so they need no job of their own.

**`auto-log-parlays` is deliberately off.** It pulled
`/parlay-recommendations` and `/edges`, both frozen playstat surfaces, so leaving it
on would accrue paper bets from stale rows and quietly poison the calibration data.
Repointing it at the builder feed is an open item in `PRODUCT.md`. Do not re-enable
it as-is.

Plist changes are hand-owned and not in git. `docs/DEPLOY.md` has the systemd timer
equivalents for the Linux move, where the same ordering is expressed as
`After=playstat-mlb.service`.

## 4. Secrets

Everything lives in `backend/.env`, which is never committed. `backend/.env.example`
lists the keys.

- `BUDGERR_API_KEYS`: comma-separated `name:key` pairs, one per consumer (`web`,
  `mobile`, `cron`), so any one rotates independently. `AUTH_ENABLED` is the
  kill-switch.
- `PLAYSTAT_API_KEY`: the key playstat provisioned for Budgerr. Injected
  server-side by the proxy, so it never reaches a client.
- `ANTHROPIC_API_KEY`: **intentionally unset.** The rewards rate lookup and
  `POST /bets/parse-slip` both return 501 until it is set, because each call costs
  money. Both frontends handle that 501 with a message rather than an error.
- `NTFY_BASE_URL` and `NTFY_TOPIC`: push notifications. Unset topic means no-op.
  The phone has to be **subscribed to the topic in the ntfy app** to receive
  anything.
- `CORS_ORIGINS`: must include wherever a client is served from in development.

**Do not touch Plaid credentials or real bank-link flows without asking the owner.**
Reading another project's `.env` is correctly blocked by the permission system.

## 5. Verifying a change

The bar, per surface. Static checks alone are not "done" for anything user-facing.

**Backend**

```bash
backend/.venv/bin/pytest
```

**78 tests** across 10 files as of 2026-09-27 (auth 6, auto-settlement 11, bet
analytics 11, auto-log 9, bankroll 9, leg-model fields 2, notify 3, playstat proxy 6,
recurring 16, slip parser 5). The suite is database-independent, which is why CI
needs no Postgres service. After it passes, restart the service and curl the changed
endpoints against real data.

**Web** (`budgerr-web`)

```bash
npm run build
```

Plus `npx vitest run`: **27 tests** as of 2026-09-27. Then drive the actual flow in
a browser and confirm the database side effect, not just the render.

**Mobile** (`budgerr-app`)

```bash
npx tsc --noEmit
```

There is **no simulator available**, so mobile verification is static only. Say so
explicitly in any report rather than implying a runtime check happened.

**Cross-repo**: when a change touches `lib/builderParlays.ts` or `lib/kelly.ts`,
confirm the file is still byte-identical in both client repos. That is the whole
mechanism keeping them in sync, and a silent divergence there is invisible until
behaviour differs between clients.

**Clean up test rows you create** (bets: `DELETE` by `bet_id`). Never touch data you
did not create.

## 6. Migrations

alembic, autogenerated against the Docker Postgres. **Always read the generated
file** before applying it: a `NOT NULL` column needs a `server_default`, and
autogenerate will not add one.

Additive and nullable is the house style, and `7161fe789272` is the worked example:
three nullable columns, no backfill, a display fallback so old rows render
unchanged. That is what made it safe to ship without touching existing data.

## 7. Backups

`backend/ops/backup.sh` runs `pg_dump -Fc` against the Docker Postgres and encrypts
with `age`.

- Public recipient: `~/.config/budgerr/backup-age.pub`.
- Private identity: `~/.config/budgerr/backup-age.key`, mode 600, kept **off** the
  backup location. **Losing it makes every backup unrecoverable.**
- Authoritative copy to a local backups directory, atomic write with newest-14
  retention. Best-effort second copy to iCloud Drive.

**macOS trap:** a launchd process can *create* files in iCloud but cannot `rename`
or `unlink` them without Full Disk Access. That is the entire reason for the
local-authoritative plus create-only-iCloud split. Do not "simplify" it into writing
atomically straight to iCloud.

The script is env-overridable so it runs unchanged on macOS and on the Linux box.
Restore steps and the scratch-DB restore drill are in `backend/ops/restore.md`. The
drill has been performed once and row counts matched live.

## 8. CI

GitHub Actions on all three repos, on push to `main` and on pull requests.

- Backend: `pip install -e .[dev]` then pytest. The **editable** install is
  required, because `app.main` mounts `StaticFiles` from `../static`, which exists
  only in the source tree and not in a copied site-packages install. The same
  reason applies to `backend/Dockerfile`.
- Web: `npm ci` then `npm run build`.
- Mobile: `npm ci` then `tsc --noEmit`.

Pushing workflow files needs the `workflow` OAuth scope on the git token
(`gh auth refresh -s workflow`).

## 9. graphify

Every repo has a knowledge graph in `graphify-out/`, which is gitignored and
disposable.

- Orient with `graphify query "<question>"` before grepping raw source.
- `graphify update .` after modifying code. AST-only, no API cost.
- A user-global post-commit hook keeps it fresh on commit.

## 10. Troubleshooting

| Symptom | Cause |
| --- | --- |
| Everything 401s, including `/docs` | Working as designed. Only `/health` is exempt. Send a key, or set `AUTH_ENABLED=false` and restart. |
| `/playstat/*` returns 502 | playstat is unreachable. The proxy is fine; check the upstream service. |
| Rewards lookup or slip parse returns 501 | `ANTHROPIC_API_KEY` unset. Deliberate. |
| Tonight shows almost nothing per game | Normal. The builder is a curated short list, not comprehensive. |
| Team markets section is empty | Normal, and often. Team legs rarely clear playstat's floor. |
| Tests pass in a worktree only with `PYTHONPATH=.` | The shared `backend/.venv` is an editable install pointing at the main checkout. |
| `OSError: [Errno 11]` from a scheduled job | iCloud sync. The project must not live under `~/Documents`. |

`docs/DEPLOY.md` section 14 covers troubleshooting specific to the deployed box.
