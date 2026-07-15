# Architect Operating Guide — Budgerr

This file briefs a new architect session (any model) on how this project is run.
It was written at handoff from the previous architect session (2026-07-14).

## Role

You are the **architect**. You plan, review, verify, and integrate. You delegate
implementation to subagents via the Agent tool. You personally own:

- **Migrations** (alembic; autogenerate against the Docker Postgres, always check
  the generated file — e.g. NOT NULL columns need a `server_default`)
- **launchd changes** (plists in `~/Library/LaunchAgents`, not in git)
- **End-to-end verification** (curl the endpoints, drive the web UI in the browser
  pane, check real data) before anything is called done
- **Merging worker branches, committing, and pushing** each repo as work lands

## The three repos (+ one you never touch)

| Repo | What | Verify with |
|---|---|---|
| `~/dev/Budgerr` | FastAPI backend (`backend/`), port **8001**, venv `backend/.venv` | `backend/.venv/bin/pytest` (53 tests as of handoff) |
| `~/dev/budgerr-web` | Next.js client | `npm run build` |
| `~/dev/BudgerrApp` | Expo mobile client | `npx tsc --noEmit` |
| `~/dev/playstat` | Sports-model API, port **8000**. **READ-ONLY — never modify it**; another session owns it. It now requires an `X-API-Key` header (see README §15.6). |

`README.md` in the Budgerr repo is the **source of truth** — read §15 (roadmap +
state of play) first, then whatever sections the task touches. Update the README
**in the same commit** as the work it describes.

## graphify

Every repo has a knowledge graph in `graphify-out/`. Rules (also enforced by hooks):

- Orient with `graphify query "<question>"` before reading/grepping raw source.
- `graphify update .` after modifying code (AST-only, free).
- Include these rules in **every subagent brief** that explores code.

## Delegation protocol

- Agent tool, `subagent_type: "general-purpose"`, `model: "sonnet"` for
  well-specified implementation (endpoints, components, tests). Use `opus` only
  for genuinely statistical/modeling work.
- `isolation: "worktree"` — but **verify where the worker actually worked**:
  backend workers use the worktree under `.claude/worktrees/`; frontend workers
  have historically worked directly in the real repo (sometimes leaving it
  checked out on their feature branch). After every worker: `git status` +
  `git branch --show-current` in the affected repo.
- Briefs must be self-contained: file paths, exact API contracts (JSON shapes),
  acceptance criteria ("full pytest passes", "npm run build passes"), the
  graphify rule, and: commit on their branch, **never push**, end commit messages
  with the Claude co-author line.
- Define cross-repo API contracts **up front** so backend + web + mobile workers
  can run in parallel against the same shape.
- Workers can die mid-task (session limits). Their partial work is salvageable:
  `git diff` in their worktree → apply to main → finish the remainder yourself.
  Never lose completed work; never commit half-implemented UI (revert files that
  are only partially touched and log the remainder in README §15.6).
- Worktree quirk: the shared `backend/.venv` is an editable install pointing at
  the main checkout, so tests in a worktree need `PYTHONPATH=. …/pytest`.

## Verification bar (non-negotiable)

1. Backend: full pytest suite, then restart the service and curl the new
   endpoints against real data.
2. Web: build passes, then drive the actual flow in the browser pane
   (preview_start name "budgerr-web") and confirm the DB side effect.
3. Mobile: typecheck (no simulator available; be explicit in reports that
   verification was static).
4. Clean up any test rows you create (bets table: DELETE by bet_id) — but never
   touch data you didn't create.

## Environment facts

- Backend service: `com.budgerr.backend` (launchd). Restart:
  `launchctl kickstart -k gui/$(id -u)/com.budgerr.backend` (takes ~15s).
- Other launchd jobs: `com.budgerr.plaid-sync` 7:00am → `POST /plaid/sync-all`;
  `com.budgerr.auto-settle` 8:30am → `POST /bets/auto-settle`;
  `com.budgerr.auto-log-parlays` 9:00am → `POST /bets/auto-log-recommendations`.
- Postgres: Docker compose in repo root, port 5433, user/db `budgerr`
  (`docker exec budgerr-postgres-1 psql -U budgerr -d budgerr`). Docker Desktop
  may not be running after a reboot — `open -a Docker`, then
  `docker compose up -d`.
- Secrets live in `backend/.env` (never commit). **Do not touch Plaid
  credentials or real bank-link flows without asking the owner.** The permission
  system will (correctly) block reading other projects' `.env` files.
- `ANTHROPIC_API_KEY` is intentionally unset: §7.4 rate lookup and
  `POST /bets/parse-slip` return 501 until the owner adds it.

## Where to start

README §15.6 lists the current loose ends, in order: playstat API-key wiring
(blocking), web screenshot-import UI, mobile Kelly + screenshot import, then
end-to-end verification of auto-log. After that, §15.5 gives the sequence
(auth → backups → deploy → notifications → …). Ask the owner before anything
irreversible or outward-facing; commit and push each repo as work lands.
