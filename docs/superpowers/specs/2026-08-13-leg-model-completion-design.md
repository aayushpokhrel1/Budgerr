# Complete the bet-leg model — `game_id` + `player_id` + `market`

**Date:** 2026-08-13
**Status:** Design approved, pending spec review → plan
**Repos touched:** `~/dev/Budgerr` (backend), `~/dev/budgerr-web`, `~/dev/BudgerrApp`

## Motivation

Two forward features both need the same thing that our stored bet legs currently
lack — a structured, join-able identity per leg:

1. **Closing-line-value (CLV) tracking.** Coordinated with the Playstat session
   "Closing-line value investigation" (2026-08-13). Their verdict: build the
   *plumbing* now, keep any CLV *number* dark until their signal proves out
   (currently negative, ~3–4 week horizon; they'll ping). The plumbing they need
   us to have is the join key per leg:
   - Player leg: `(game_id, player_id, stat_type, line_value)` + `side ∈ {over, under}`
   - Team leg: `(game_id, market, line_value)` + `side ∈ {over, under}` (totals)

   Rules from Playstat that constrain this design: there is **no closing line**
   (their last snapshot is ~100 min pre-pitch line *movement*, not a close — do
   not name anything `closing_odds`); a **moved `line_value` is a different bet**
   (excluded, not compared) so `line_value` must be stored at log time (already
   is); attach CLV to the **builder**, not a model. The read surface (lookup
   endpoint vs. additive fields) is **not finalized** — out of scope here.
   See memory `playstat-clv-contract`.

2. **Team-market (NRFI/F5) settlement**, currently on HOLD. Its stated blocker is
   the same: `BetLegInput` has no `game_id`/`market`, so a team leg can't be
   graded against `team_game_stats(game_id, market, side)`. Completing the leg
   model removes that structural blocker (wiring settlement itself stays on the
   documented HOLD).

This change is therefore **dual-purpose plumbing**: it is the prerequisite both
features share, it is contract-stable (the join keys are settled), and it costs
nothing if the CLV signal never materializes.

## Current state (verified 2026-08-13)

`bet_legs` (`backend/app/models/betting.py:51`) stores: `player_name`, `stat_type`,
`line_value`, `side`, `odds`, `model_prob`, `leg_status`. It has **no** `game_id`,
`player_id`, or `market`.

`builderConstructionToBetInput` (`lib/builderParlays.ts`, byte-identical in
`budgerr-web` and `BudgerrApp`) folds the builder leg into that shape:

- **Player leg:** `player_name = playerNameFromLabel(leg)`, `stat_type = leg.stat_type`.
  Drops `leg.game_id` and `leg.player_id` on the floor.
- **Team leg:** `player_name = "{matchup or 'Game {id}'} · {marketLabel}"`,
  `stat_type = leg.market`. The market is **doubled** into both the display string
  and `stat_type`; `game_id` is dropped.

The **pre-log** display (`legDisplay`) already renders from structured construction
fields (`leg.market`, `matchup(leg.game_id)`, `marketLabel`). The folding only
affects what is **stored**, and therefore how a leg renders **post-log** (bet
history/detail, which reads the stored `bet_legs` row).

## Design

### 1. Data model — `bet_legs` (backend)

Add three **nullable** columns (additive migration, **no backfill**):

| Column | Type | Populated for | Notes |
|---|---|---|---|
| `game_id` | `INT` nullable | both leg kinds (builder legs) | Playstat's game id, external id-space — **no FK** to any Budgerr table. |
| `player_id` | `INT` nullable | player legs only | Playstat player id. |
| `market` | `VARCHAR` nullable | team legs only | `first_inning_runs` / `f5_runs`. |

`line_value`, `side`, `odds`, `stat_type`, `player_name`, `model_prob` unchanged.
All three are nullable, so no `server_default` is required; manual/historical legs
and manual quick-entry bets simply leave them null.

`BetLegInput` (Pydantic, backend) and the `BetLegInput` TS type (both frontends'
`lib/api`) gain the three optional fields. The bet-creation path persists them.

### 2. Log-time population — `builderConstructionToBetInput` (both frontends)

Must stay **byte-identical** across `budgerr-web` and `BudgerrApp`.

- **Player leg:** unchanged `player_name`/`stat_type`; **add** `game_id: leg.game_id`,
  `player_id: leg.player_id`. `market` unset.
- **Team leg:** `market: leg.market`, `game_id: leg.game_id`; `player_name` becomes
  the **clean matchup only** (`matchup(...) ?? \`Game ${leg.game_id}\``) — **drop
  the `· marketLabel` fold**; `stat_type` unset; `player_id` unset.

Result: the stored leg carries the exact CLV join keys, and the market is no longer
doubled.

### 3. Display — post-log leg rendering (both frontends)

Wherever a stored leg is rendered (bet history/detail component — to be located per
repo in the plan), render structurally:

- `market != null` → team leg → `"{player_name} {marketLabel(market)} {side} {line}"`
- else → `"{player_name} {stat_type} {side} {line}"`

The `else` branch is unchanged behavior for **player legs** *and* **legacy team
rows** (pre-migration rows have null `market` and still carry the old folded
`player_name`/`stat_type`), so **old rows do not regress and no backfill is needed**.
`legDisplay` (pre-log) is essentially unchanged (already structured); reuse
`marketLabel` for the shared label.

### 4. Auto-settle safety

Team legs stay **log-only**. Moving `market` out of `stat_type` sets team-leg
`stat_type` to null; null-`stat_type` legs are already excluded from auto-settle
(covered by `test_pending_and_null_stat_type_legs_excluded`). This is **strictly
safer** than today (a raw `"first_inning_runs"` stat_type can no longer be matched
against any box-score stat). Add a test asserting a team leg (`game_id` + `market`
set, `stat_type` null) is **not** settled, to lock the behavior.

### 5. Out of scope (YAGNI)

- **No CLV read/backfill job, no CLV UI, no `clv`/`value`/`edge` fields.** The read
  surface is unfinalized upstream; the signal is dark. (If shared shapes ever get
  named, mirror Playstat's "movement" vocabulary, per their README §15.8 #2.)
- **Not** consuming the inline `home_team`/`away_team` builder fields for matchup —
  matchup still resolves via `gamesById` (we fetch `/games` for `isRunFullyPast`
  regardless). Separate known loose end.
- **Not** wiring team-market settlement — this only adds the columns it will need;
  settlement stays on the documented HOLD.

## Components & boundaries

| Unit | Change | Verify |
|---|---|---|
| `backend/app/models/betting.py` `BetLeg` | 3 nullable columns | migration autogenerate + review |
| alembic migration | add columns to `bet_legs` (nullable) | `alembic upgrade head` against Docker PG; architect owns |
| `BetLegInput` (backend Pydantic) + create-bet path | 3 optional fields, persisted | pytest + curl a builder-leg log |
| backend auto-settle test | new team-leg-not-settled assertion | full pytest |
| `lib/builderParlays.ts` (both frontends, byte-identical) | populate `game_id`/`player_id`/`market`; team `player_name` clean matchup | Vitest; `diff` the two files == identical |
| `BetLegInput` TS type (both frontends) | 3 optional fields | `npm run build` / `tsc` |
| post-log leg-display component (both frontends) | structured render w/ legacy fallback | drive `/tonight` log + view in browser; `tsc` mobile |
| `README.md` §15.1 | new bullet documenting this change | same commit |

## Testing

- **Backend:** full pytest suite green; new auto-settle test; restart service and
  `POST` a builder-leg-shaped bet, confirm `game_id`/`player_id`/`market` persist,
  then `DELETE` the test bet.
- **Web:** `npm run build` + Vitest (extend `builderParlays` tests to assert the new
  fields on both player and team legs, and the clean team `player_name`); drive
  `/tonight`, log a player leg and a team leg, confirm the stored rows + display,
  clean up the rows.
- **Mobile:** `npx tsc --noEmit`; confirm `lib/builderParlays.ts` byte-identical to
  web (`diff`).

## Verification bar / ownership

- Migration + launchd/service restart: **architect** (per `docs/ARCHITECT.md`).
- Frontend + backend-schema implementation: delegate to `general-purpose` sonnet
  subagents against this spec; architect reviews, verifies end-to-end, commits +
  pushes each repo. Cross-repo `BetLegInput` contract (the 3 optional fields) is
  fixed by this spec so backend + both frontends can proceed in parallel.
- `lib/builderParlays.ts` must be verified byte-identical across repos before commit.

## Risks

- **Byte-identical drift** between the two `builderParlays.ts` — mitigated by a
  `diff` gate in verification.
- **Legacy team-leg display** — pre-migration team rows keep the old folded string;
  the `else` display branch renders them exactly as today (acceptable; they are a
  handful of paper bets).
- **Migration on a NOT-NULL mistake** — all three columns are nullable by design;
  review the autogenerated migration to confirm no accidental `nullable=False`.
