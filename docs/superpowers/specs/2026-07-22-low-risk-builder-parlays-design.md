# Design — Surface Playstat's low-risk builder parlays in Budgerr

**Date:** 2026-07-22
**Author:** Budgerr architect session
**Status:** Design, pending owner spec-review → implementation plan
**Scope:** Frontend only (`budgerr-web` + `BudgerrApp`). No backend, no migrations, no launchd.

---

## 1. Goal

Consume Playstat's precomputed "low-risk parlay of the day" and surface it in the Tonight
view, replacing the retired model parlay recommendations. Make the builder parlays loggable as
paper bets. Build the team-market (NRFI/F5) path now against a fixture so it is ready when
Playstat's team legs eventually reach the saved feed.

## 2. Verified state (probed live 2026-07-22, do not re-derive)

- `GET /playstat/parlay-builder/saved?limit=` through the Budgerr proxy: **HTTP 200 in ~1.5s**,
  no 502s. Body matches the finalized contract field-for-field. `?limit=50` returns 20 rows
  total (all that exist): **both tiers present** (10× `target_payout` 2.0, 10× 1.4) across two
  nightly runs (`created_at` 2026-07-21 and 2026-07-22). `model_prob` null on several legs.
- **All 20 constructions / 40 legs are `kind:"player"`** (`runs`/`home_runs`/`stolen_bases`,
  `market:null`). **Zero `kind:"team"` legs** in real data.

## 3. Playstat coordination answers (2026-07-22, session `local_22455754`)

- **`saved` is ranked on de-vigged MARKET probability, not model output.** `model_prob` is
  per-leg context only and never touches ranking. The player-prop legs in `saved` are the
  *book's* de-vigged favorites, **not** the shelved models. The shrink-to-mean distrust applies
  only to the model-ranked `/parlay-recommendations`, not to the builder.
- **Team legs are already wired** into the nightly build but **surface ~never**: NRFI/F5 price
  near coin-flip (~0.57 de-vigged), only ~2 of ~40 clear the builder's 0.55 favorite floor, and
  those cannot out-rank the 0.9+ player favorites in the top-N. Reliable team surfacing needs an
  uncommitted Playstat design change (dedicated team tier / lower floor), **no date**
  (Playstat README §15.9). → **Build the team branch against a fixture now.**
- **Surface current `saved` parlays now** — legitimate market-ranked low-risk favorites, not
  placeholder.
- **`/parlay-recommendations` is being wound down** (model parlays −49% over 54 paper bets);
  `saved` is the forward low-risk source. But the `/parlay-recommendations` contract stays
  **intact + additive-only** for now — no breaking change without explicit notice.

## 4. Decisions (owner-approved 2026-07-22)

1. **Coordinated with Playstat first** before building (done — §3).
2. **Tonight sources:** builder only. **Retire the model `/parlay-recommendations` UI** from
   Tonight.
3. **What to show:** a short ranked list — latest run's top ~4 constructions across both tiers,
   ranked by `joint_prob` desc, tier shown as a badge.
4. **Team settlement:** **log-only.** Team-market paper bets log but do **not** auto-settle
   (respects the backend HOLD); player-leg bets settle as today.
5. **Log mechanism:** **inline** on the builder card, mirroring the existing `ParlayCard`. The
   team-market branch lives in a shared team-aware leg-builder helper.

## 5. Design

### 5.1 Data layer — `lib/playstat.ts` (both clients, mirrored)

New types, discriminated on `kind`, kept **separate** from the existing
`PlaystatParlayLeg`/`PlaystatParlayRecommendation` (which stay untouched — contract intact):

```ts
interface PlaystatBuilderLegBase {
  game_id: number;
  label: string;
  side: 'over' | 'under';
  line: number;
  odds: number;
  market_prob: number;
  model_prob: number | null;   // CONTEXT ONLY — never surfaced as edge/value
}
interface PlaystatBuilderPlayerLeg extends PlaystatBuilderLegBase {
  kind: 'player';
  player_id: number;
  stat_type: string;
  market: null;
}
interface PlaystatBuilderTeamLeg extends PlaystatBuilderLegBase {
  kind: 'team';
  player_id: null;
  stat_type: null;
  market: 'first_inning_runs' | 'f5_runs';
}
type PlaystatBuilderLeg = PlaystatBuilderPlayerLeg | PlaystatBuilderTeamLeg;

interface PlaystatBuilderConstruction {
  parlay_id: number;
  created_at: string;
  target_payout: number;   // 1.4 | 2.0
  joint_prob: number;      // de-vigged MARKET joint prob — the ranking/label basis
  combined_odds: number;
  n_legs: number;
  legs: PlaystatBuilderLeg[];
}
```

New API method: `playstatApi.parlays.listBuilder(limit = 10)` →
`fetchJson('/parlay-builder/saved?limit=${limit}')`. Keep the existing `parlays.list()`
(model) in place but unused by Tonight.

New React Query hook `usePlaystatBuilderParlays()` in `lib/queries.ts` (web) / the mobile
equivalent, mirroring `usePlaystatParlays`.

### 5.2 Latest-run selection + ranking (shared, pure)

The feed is `created_at` desc and interleaves nightly runs. Selection:

1. Fetch `listBuilder(10)`.
2. Determine the most-recent run by `created_at` **date** (the two payout-tier batches of a
   run share a date, ~1 min apart); keep only that date's constructions.
3. Sort by `joint_prob` desc; take the top `N` (default **4**).

Ranking/labels use `joint_prob` / `market_prob` only. **`model_prob` is never displayed.**

### 5.3 `BuilderParlayCard` (new component, mirrors `ParlayCard`)

- **Header:** `{n_legs}-leg · {combined_odds.toFixed(2)}x`, a **tier badge**
  (`1.4x` / `2.0x`), and `{Math.round(joint_prob*100)}% to hit`.
- **Legs:**
  - player: `{name} {side} {stat_type} ({odds})` — `name` derived from `label` (§5.4).
  - team: `{Away} @ {Home} — {NRFI|F5} {side} {line} ({odds})`, matchup resolved from the
    slate `game_id → PlaystatGame` map already built in the Tonight view (no new fetch);
    fallback to a game-less label if the id isn't in the slate.
- **¼-Kelly:** reuse `quarterKelly(combined_odds, joint_prob, remainingBudget)` verbatim.
- **Inline logging:** stake input + "Log as paper bet" button + "Logged as paper ✓" state,
  identical UX to `ParlayCard`.

### 5.4 Shared team-aware leg-builder (the team-market branch)

A pure helper `builderConstructionToBetInput(construction, gamesById, stake)` → `BetInput`,
producing `BetLegInput[]`:

- **player leg:** `player_name` = `label` with the ` {stat_type} {side} {line}` suffix stripped
  (fallback `#{player_id}` or raw label); `stat_type`, `line_value = leg.line`, `side`, `odds`;
  `model_prob` omitted (builder is market-ranked — do not assert a model prediction).
- **team leg:** `player_name` = `"{Away} @ {Home} · {NRFI|F5}"` from `gamesById.get(game_id)`
  (fallback `"NRFI/F5 (game {id})"`); `stat_type` = the `market` string; `line_value = leg.line`;
  `side`; `odds`. **No `player_id`, no auto-settle** (BetLegInput has no `game_id`/`market`).
- `placed_at` = the first resolvable leg's game date (`gamesById.get(game_id).date` → `T12:00:00Z`);
  enables player-leg settlement, harmless for team legs.
- `bet_type` = `n_legs > 1 ? 'parlay' : 'single'`; `sportsbook: 'paper'`, `is_paper: true`,
  `potential_payout = stake * combined_odds`.

Because logging is inline, **`BetForm.tsx` / `app/modal.tsx` need no team-specific branch** —
their free-text leg fields already accept a manually-entered team leg. This supersedes the
brief's "add a team-market branch to BetForm/modal" line; the team branch lives in the helper
above. (Noted explicitly to avoid confusion against the original brief.)

### 5.5 Tonight view changes (`app/tonight/page.tsx` + mobile equivalent)

- Remove the "Recommended parlays" (model) block and the `usePlaystatParlays` /
  `usePlaystatAllEdges` usage from the view.
- Add a "Low-risk builder parlays" section rendering the selected top-N `BuilderParlayCard`s,
  passing the slate `gamesById` map + `remainingBudget`.
- Empty state when the latest run is empty ("nightly builder runs each evening").

### 5.6 Settlement boundary (log-only for team)

Team-market bets log as paper bets and **stay pending** — no auto-settle — with a small
"won't auto-settle yet" note on any team leg in the card. Player-leg builder bets settle via
the existing box-score path (line is present on the leg; `placed_at` set from the game date).
Backend team-market settlement (BetLegInput `game_id`+`market`, grading against
`team_game_stats`) remains **on HOLD** and is out of scope here.

### 5.7 Fixture + verification of the team branch

Real data has no team legs, so the team path is verified via a **committed fixture**
(`lib/__fixtures__/builderTeamConstruction.ts`) with one `first_inning_runs` and one `f5_runs`
leg in the contract shape. Verification uses:

- a **unit test** of `builderConstructionToBetInput` over the fixture (player + team + mixed),
  asserting the produced `BetLegInput[]` and the log-only team behavior; and
- a **guarded dev query-param** (`?demo=builder-team`, inert in production) that merges the
  fixture into the builder list so the real `BuilderParlayCard` + inline-log path can be driven
  in the browser pane against a team leg. (Exact guard mechanism finalized in the plan; the
  affordance doubles as QA when team data eventually flows. Removable if the owner prefers.)

### 5.8 Mobile parity

`BudgerrApp` mirrors §5.1–5.6: same types + `listBuilder` in its `lib/playstat.ts`, the same
selection/leg-builder helpers, the builder card on the mobile Tonight surface, and the retired
model section. `app/modal.tsx` unchanged (per §5.4). Verified statically (`tsc --noEmit`) — no
simulator available.

## 6. Non-goals / out of scope

- Any backend change, migration, or `BetLegInput` field addition.
- Team-market **auto-settlement** (on HOLD, backend).
- Changing `/parlay-recommendations` or its auto-log launchd job (see §8).
- Making team legs reliably *appear* in `saved` (Playstat-side, uncommitted).

## 7. Acceptance criteria

- Web `npm run build` passes; the Tonight view renders builder cards from real `saved` data
  (player legs today), with the model section gone; a paper bet logs from a card and appears in
  the bets list (then cleaned up).
- Team branch: unit test passes; the `?demo=builder-team` fixture renders a team card with a
  resolved matchup and a "won't auto-settle" note; logging it produces a pending paper bet.
- Mobile `npx tsc --noEmit` passes.
- `model_prob` appears nowhere in the UI.
- README §15 updated in the same commit.

## 8. Follow-ups (flagged, not built here)

- **Auto-log job:** `com.budgerr.auto-log-parlays` (9:00am → `POST /bets/auto-log-recommendations`)
  still pulls the wound-down model `/parlay-recommendations`. With the model UI retired,
  consider repointing paper-bet calibration at the builder source. Backend + launchd; owner
  decision; separate effort.
- **Team-market settlement:** build when Playstat's team legs actually flow AND the HOLD lifts —
  BetLegInput `game_id`+`market`, grading against `team_game_stats(runs_inning_1/runs_f5)`.

## 9. Implementation notes — build deltas (2026-07-23)

Built via subagent-driven execution; the following refinements emerged during implementation +
end-to-end verification and now reflect the shipped code (web `dad8cd6..af33aa5`, mobile
`79fb0d2..449739c`, branch `feat/builder-parlays`):

- **Run-date game resolution (design change, owner-approved during verification).** §5.2/§5.4
  assumed builder-leg games resolve from the displayed slate. Verification found the builder's
  latest saved run can be for a *different day* than the "next slate" (e.g. a 2026-07-22 run vs a
  2026-07-23 slate), so slate-based resolution left `placed_at` = now and team matchups
  unresolved. Fixed: resolve builder-leg games from the builder RUN's own `created_at` date via a
  new `playstatApi.games.listForDate` + `usePlaystatGames(runDate(latestRun))`; helper `runDate`.
- **Hide fully-past runs (owner-approved).** A run is suppressed once all its games have
  started/finished — `isRunFullyPast` (games are "upcoming" iff status ∈ {null,'NS','S'}, mirroring
  `GameCard`'s `statusLabel`; ≥1 resolvable game and none upcoming ⇒ hidden). This keeps stale
  past runs (and their un-settleable bets) out of Tonight; the section shows its empty state
  until a fresh run lands. Verified live: the stale 2026-07-22 run was correctly hidden.
- **Player-leg display includes the line.** `legDisplay` shows `"{name} {side} {line} {stat}"`
  for player legs (parity with the team branch's `"{matchup} — {NRFI|F5} {side} {line}"`); an
  early build dropped the player line (caught in final review).
- **Model UI fully removed, incl. dead code.** Beyond retiring the Tonight section, the orphaned
  `components/tonight/ParlayCard.tsx` and the now-unused `usePlaystatParlays` /
  `usePlaystatAllEdges` hooks were deleted in both clients. `playstatApi.parlays.list` and the
  `PlaystatParlay*` types are retained (contract intact, per §6).
- **Testing.** `budgerr-web` gained a minimal Vitest setup (it had no test runner) covering the
  pure module — 16 tests. Mobile has no runner; its `lib/builderParlays.ts` is byte-identical to
  web's and verified by `tsc`.
- **Open observation (candidate refinement, not built):** ranking the short list by `joint_prob`
  desc means the safer 1.4x tier dominates — in practice all shown are 1.4x, the 2.0x tier rarely
  appears. If surfacing both tiers matters, consider e.g. top-2 per tier instead of top-4 overall.
- **Known non-blockers:** `graphify-out` is stale in both frontend repos (`graphify update`
  refused — a scan-corpus tooling issue, unrelated to this change); mobile README/DESIGN prose
  still mention the deleted `ParlayCard`.
