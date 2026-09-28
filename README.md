# Budgerr

A personal finance app that treats betting as a first-class budget category instead
of a line item buried in "Entertainment". It connects real bank accounts through
Plaid, categorizes spending, tracks bets and parlays per leg, and puts tonight's
slate next to the remaining betting budget in one view.

Built for one person, deliberately. See `PRODUCT.md` for what that constrains and
what it rules out.

## The three repos

Budgerr is **three separate repositories** under one folder. A change that spans
them is several commits in several repos, so name the repo when you write a decision
down.

| Repo | What it holds | Verify with |
| --- | --- | --- |
| `Budgerr` (this one) | FastAPI backend in `backend/`, PostgreSQL, alembic migrations, and the cross-repo docs below | `backend/.venv/bin/pytest` |
| [`budgerr-app`](https://github.com/aayushpokhrel1/budgerr-app) | Expo / React Native client | `npx tsc --noEmit` |
| [`budgerr-web`](https://github.com/aayushpokhrel1/budgerr-web) | Next.js client | `npm run build`, `npx vitest run` |

Both clients talk to the same backend and share no code. They do keep a few pure
helper files (`lib/builderParlays.ts`, `lib/kelly.ts`) **byte-identical** across the
two repos, so a change to one is a change to both.

[`playstat`](https://github.com/aayushpokhrel1/Playstat) is a **fourth, separate
project** with its own backend and database, consumed read-only through a proxy.
Never modify it from here; another session owns it.

## Where things are written down

| File | What it holds |
| --- | --- |
| `README.md` | This file: what Budgerr is and how the repos fit together |
| `PRODUCT.md` | Product truth: audience, decisions, roadmap, what is deferred and why |
| `docs/ARCHITECT.md` | The architecture across the three repos |
| `docs/OPERATIONS.md` | Run, verify, scheduled jobs, secrets, backups, CI, environment traps |
| `docs/DEPLOY.md` | The deployment runbook for the eventual Linux box |
| `docs/superpowers/` | Per-feature specs and plans, written before building |
| `HANDOVER.md` | Current state only, and gitignored, so nothing durable lives there |
| `CLAUDE.md` | Conventions for sessions working in this repo |

The sibling repos each carry their own `README.md`, `PRODUCT.md`, and `DESIGN.md`
for client-specific detail. `docs/ARCHITECT.md` is the cross-repo view.

## Status

The backend runs locally on the owner's Mac under launchd, with Postgres in Docker.
Deployment artifacts are authored and smoke-tested but **nothing is deployed**: it is
gated on the owner choosing hardware. `docs/OPERATIONS.md` covers the current setup,
`docs/DEPLOY.md` the move, and `HANDOVER.md` whatever is in flight right now.
