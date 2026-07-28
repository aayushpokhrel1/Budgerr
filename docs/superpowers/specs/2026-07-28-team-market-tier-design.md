# Design — Surface Playstat's team-market tier (NRFI/F5) in Tonight

**Date:** 2026-07-28
**Author:** Budgerr architect session
**Status:** Design, owner-approved → implementation plan
**Scope:** Frontend only (`budgerr-web` + `BudgerrApp`). No backend, no migrations, no
launchd, no settlement whitelist. Team-market bets stay **log-only**.

Follow-up to the shipped **low-risk builder parlays** feature
(`docs/superpowers/specs/2026-07-22-low-risk-builder-parlays-design.md`). That feature built
and fixture-verified a full team-rendering + logging branch; this design lights it up with
real data now that Playstat's dedicated team tier is populated.

---

## 1. Goal

Playstat shipped an additive `?tier=` param on `GET /parlay-builder/saved`
(`player` = default, what Budgerr already consumes; `team` = dedicated NRFI/F5-only; `all` =
combined). The team tier now returns real data. Surface it in Tonight as a **separate,
clearly-labeled, higher-variance** section that reuses the already-built team branch, without
disturbing the shipped low-risk section.

## 2. Verified state (probed live 2026-07-28 through the Budgerr proxy, web key — do not re-derive)

- `GET /playstat/parlay-builder/saved?tier=team&limit=50` → **HTTP 200, 20 constructions / 40
  legs, all `kind:"team"`** (markets `first_inning_runs` ×30, `f5_runs` ×10). This is the
  premise that was `[]` on 2026-07-23; it is now populated.
- **`joint_prob` range 0.309–0.321** across the team constructions — confirms Playstat's
  "explicitly NOT low-risk" framing (~31% joint, at the low end of the stated 30–50% band).
- Two nightly team runs present: `created_at` **2026-07-26** and **2026-07-25**. There is **no
  07-27 or 07-28 team run** — i.e. the latest team run is already 2 days stale, so
  `isRunFullyPast` will hide it and the section shows its empty state today. (Team tier is
  empty/stale by nature; treat this as normal.)
- **`?tier=all`** returns a **union feed**: each construction is purely player *or* purely team
  (**zero mixed-leg constructions**), interleaved and ranked. At `limit=50`: dates 07-28(10),
  07-27(10), 07-26(20 = 10 player + 10 team), 07-25(10). Ordered `created_at` desc, so newer
  player runs precede older team runs.
- **Partition equivalence verified.** The **player** partition of `?tier=all`'s latest run is
  byte-identical to `?tier=player` (default): latest run 07-28, top-4 `[181,182,183,184]`. The
  **team** partition of `?tier=all`'s latest team run is byte-identical to `?tier=team`: latest
  run 07-26, top-4 `[166,161,167,162]`. ⇒ A single `?tier=all` fetch, split client-side,
  reproduces both dedicated tiers exactly.
- **New additive leg fields observed** (not in the original 2026-07-22 contract): team legs now
  carry inline `home_team`, `away_team`, `player_team_side`. Not consumed here (YAGNI) — the
  existing `game_id → /games` matchup path is reused unchanged. Recorded as a future
  simplification (§8).

## 3. Decisions (owner-approved 2026-07-28)

1. **Single `tier=all` fetch, split client-side** by `hasTeamLeg` into the two sections
   (player → low-risk, team → team markets). One request instead of two; no third UI section;
   framing stays clean. The shipped low-risk section's output is unchanged (§2).
2. **Placement + count:** team section sits **below** "Low-risk builder parlays" as its own
   labeled section, showing the latest team run's **top 4** by `joint_prob`.
3. **Card style:** add a **variance variant** to `BuilderParlayCard` (neutral/amber, no green
   "% to hit"); the low-risk section keeps the existing emerald look untouched.
4. **Empty state:** reuse `isRunFullyPast` to hide a fully-played team run, and show a calm,
   non-error empty state. `[]` is a normal state, not an error.
5. **Log-only** for team bets (unchanged): they log as paper bets and do **not** auto-settle
   (backend settlement remains on HOLD; `BetLegInput` has no `game_id`/`market`).
6. Rank/label by `joint_prob` / `market_prob` only — **`model_prob` never surfaced**.

## 4. Design

### 4.1 Data layer — `lib/playstat.ts` (both clients, mirrored)

`playstatApi.parlays.listBuilder` gains an optional `tier` argument:

```ts
listBuilder: async (limit = 10, tier?: 'player' | 'team' | 'all'):
  Promise<PlaystatBuilderConstruction[]> =>
  fetchJson(`/parlay-builder/saved?limit=${limit}${tier ? `&tier=${tier}` : ''}`),
```

No type changes — `PlaystatBuilderConstruction` and the discriminated `PlaystatBuilderLeg`
(player | team) already cover team legs. The new inline `home_team`/`away_team`/
`player_team_side` fields are ignored (not added to the type; not consumed).

### 4.2 Hook — `lib/queries.ts` (both clients)

The **existing** `usePlaystatBuilderParlays()` switches its query fn to
`playstatApi.parlays.listBuilder(100, 'all')`. **No new hook** — one combined feed serves both
sections. (This supersedes the original brief's separate `usePlaystatBuilderTeamParlays` line,
which predated the tier=all decision.)

**Limit = 100 rationale:** `?tier=all` is `created_at` desc, so newer player runs sit ahead of
older/stale team runs. A generous limit keeps a stale team run inside the fetched window; if a
team run ever falls outside it, the team section simply shows its normal empty state (graceful,
not misleading). The endpoint is small and fast (~0.3–1.5s), so 100 is cheap.

### 4.3 Tonight page wiring — `app/tonight/page.tsx` + mobile equivalent

Partition the combined feed **before** selecting each latest run (tier=all constructions are
never mixed, §2, so `hasTeamLeg` cleanly classifies each construction):

```ts
const all = builderParlays.data ?? [];
const playerCons = all.filter((c) => !hasTeamLeg(c));
const teamCons   = all.filter((c) =>  hasTeamLeg(c));

const latestPlayerRun = selectLatestRun(playerCons, 4); // low-risk section
const latestTeamRun   = selectLatestRun(teamCons, 4);   // team section
```

Each partition keeps its **own** games resolution, because the latest player run and latest
team run are different dates:

- `usePlaystatGames(runDate(latestPlayerRun))` → `playerGamesById` (existing, renamed from
  `builderGames`/`builderGamesById`).
- `usePlaystatGames(runDate(latestTeamRun))` → `teamGamesById` (new).

Gate each section on its own `isRunFullyPast(latestRun, gamesById)` exactly as the low-risk
section does today (wait for that partition's games before deciding; hide a fully-past run).

No new logic in `lib/builderParlays.ts` — the partition uses the existing `hasTeamLeg`,
`selectLatestRun`, `runDate`, `isRunFullyPast`, `legDisplay`, `builderConstructionToBetInput`.
The module therefore stays **byte-identical across the two repos** (unchanged, or trivially
unchanged).

### 4.4 Team-market section (new component usage in Tonight)

Rendered directly below the low-risk section:

- Heading: **"Team markets (NRFI/F5) — higher variance"**.
- Muted subcaption: **"~30–50% to hit · logs as paper, won't auto-settle."**
- Body: the latest team run's top-4 `BuilderParlayCard`s with `variant="variance"`, passing
  `teamGamesById` + `remainingBudget`.
- Empty state (when `latestTeamRun` is empty or hidden by `isRunFullyPast`):
  **"No team-market parlays in tonight's build — the team tier is often empty."** No error
  styling.

### 4.5 `BuilderParlayCard` variance variant — `components/tonight/BuilderParlayCard.tsx`

Add `variant?: 'lowrisk' | 'variance'` (default `'lowrisk'`):

- `'lowrisk'` (default): today's emerald border + green "% to hit" badge — **unchanged**.
- `'variance'`: neutral/amber border (e.g. `border-amber-300 dark:border-amber-900`) and the
  joint-prob badge rendered muted (surface/`text-muted`, **no green**) so it does not read as
  "safe."

The existing `teamNote` ("Team markets log but don't auto-settle yet") already fires via
`hasTeamLeg` and stays. The low-risk section passes no `variant` (defaults to emerald); the team
section passes `variant="variance"`.

### 4.6 Verification affordance — repoint `?demo=builder-team`

Today `?demo=builder-team` injects a fixture team construction into the **player** section —
now incorrect (team constructions belong in the team section). **Repoint it:** in dev only
(`NODE_ENV !== 'production'`), the flag reveals the **real** latest team run in the team section
**even when `isRunFullyPast` would hide it**. This drives the **real** team card against **real**
data (real matchups resolved via `/games?date=<team run date>`, real inline-log → real pending
paper bet) — a stronger check than a synthetic fixture, and it works today against the stale
07-26 run.

The committed fixture (`lib/__fixtures__/builderTeamConstruction.ts`) and its Vitest unit test
of `builderConstructionToBetInput` remain for pure-module coverage.

### 4.7 Mobile parity — `BudgerrApp`

Mirrors §4.1–4.6: the same `listBuilder(limit, tier)` signature, the same
`usePlaystatBuilderParlays()` switch to `(100, 'all')`, the same partition + dual games
resolution on the mobile Tonight surface, the same team section and card variant. Because the
partition adds no module logic, `lib/builderParlays.ts` stays **byte-identical** to web's.
Verified statically (`npx tsc --noEmit`) — no simulator available.

## 5. Non-goals / out of scope

- Any backend change, migration, `BetLegInput` field addition, or settlement-whitelist change
  (backend team-market settlement remains on **HOLD**).
- Team-market **auto-settlement** (log-only stands).
- Consuming the new inline `home_team`/`away_team`/`player_team_side` leg fields (future
  simplification, §8).
- Mixed player+team constructions (none exist in `?tier=all`).
- Making team runs appear more often / less stale (Playstat-side).
- Changing `/parlay-recommendations` or any launchd job.

## 6. Acceptance criteria

- Web `npm run build` passes.
- Low-risk section is **unchanged** — still the latest player run's top-4 (`[181,182,183,184]`
  against current data).
- Team-market section renders the latest team run's top-4 `BuilderParlayCard`s in the
  **variance variant** (amber/neutral, no green badge) with the "won't auto-settle" note; or the
  calm empty state when the run is hidden/empty.
- Browser (via the repointed demo affordance): a **real** team card renders with a resolved
  matchup; inline-logging it produces a **pending** paper bet (no auto-settle); the test row is
  cleaned up.
- Mobile `npx tsc --noEmit` passes; `lib/builderParlays.ts` byte-identical to web's.
- `model_prob` appears nowhere in the UI; team ranking/labels use `joint_prob`/`market_prob`.
- README §15 updated in the same commit.

## 7. Verification plan (architect)

- Web build + drive `/tonight` in the browser pane (`preview_start "budgerr-web"`): confirm the
  low-risk section is intact and the team section shows its empty state (07-26 run hidden);
  then with the demo flag, confirm the real team card renders + logs a pending paper bet;
  delete the test row.
- Mobile `npx tsc --noEmit`.
- Diff `lib/builderParlays.ts` between the two repos to confirm byte-identity.

## 8. Follow-ups (flagged, not built here)

- **Inline matchup fields:** team legs now carry `home_team`/`away_team` inline. A future change
  could render team matchups (and possibly drop the per-run `/games` fetch for the label) from
  the leg directly; `isRunFullyPast` would still need game status, so the `/games` fetch can't be
  fully removed without a different hiding signal. Deferred (YAGNI).
- **Team-market settlement:** build when the backend HOLD lifts — `BetLegInput` `game_id`+
  `market`, grading against `team_game_stats(runs_inning_1/runs_f5)`.
- **`graphify update`** still refuses in both frontend repos (stale-graph tooling issue,
  unrelated) — expected; do not force.
