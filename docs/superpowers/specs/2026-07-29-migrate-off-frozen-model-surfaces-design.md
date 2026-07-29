# Design — Migrate Tonight + quick-entry off the frozen model surfaces onto the builder feed

**Date:** 2026-07-29
**Author:** Budgerr architect session
**Status:** Design, owner-approved → implementation plan
**Scope:** Frontend only (`budgerr-web` + `BudgerrApp`). No backend, migrations, launchd, or settlement changes.

Follow-up to the team-market-tier feature and to Playstat's model-shelve (README §16 / their session "Shelve MLB model and edges", 2026-07-28): Playstat froze `GET /edges`, `GET /game-predictions`, and `GET /parlay-recommendations` — the endpoints keep serving their last-computed rows but stop updating. Budgerr must move off the frozen surfaces onto the market-ranked builder feed (`GET /parlay-builder/saved`) it already consumes.

---

## 1. Goal

Remove Budgerr's remaining runtime dependence on the frozen `/edges` and `/game-predictions` by feeding the two `/edges` consumers (slate GameCards + bet quick-entry) and the one `/game-predictions` consumer (GameCard first-inning line) from the builder feed already fetched on Tonight (`?tier=all`). The backend 9am auto-log job (which pulled `/parlay-recommendations` + `/edges`) was already disabled 2026-07-28; the frontend model `/parlay-recommendations` UI was retired 2026-07-23.

## 2. Verified current state (2026-07-29, do not re-derive)

Frontend consumers of the frozen surfaces (from a full grep of `app`/`components`/`lib` in both repos):
- **`/edges`** via `usePlaystatEdges(slate.date)`:
  - `app/tonight/page.tsx` / `app/(tabs)/tonight.tsx` → `edgesByGame` → `GameCard` per-game edge chips.
  - `components/bets/BetForm.tsx` / `app/modal.tsx` → "Tonight's edges" panel with "+ Add to bet" (`addLegFromEdge`).
- **`/game-predictions`** via `usePlaystatGamePredictions(slate.date)`:
  - Tonight only → `firstInningByGame` (market `first_inning_runs`) → `GameCard` first-inning line.
- No other consumers. Backend `get_edges`/`get_parlay_recommendations` were only used by the now-disabled auto-log job.

Builder feed shape already in use (`usePlaystatBuilderParlays()` → `listBuilder(100, 'all')`): constructions partition cleanly by `hasTeamLeg` into player-only and team-only (never mixed). Player legs carry `game_id`, `label` (name via `playerNameFromLabel`), `stat_type`, `side`, `line`, `odds`, `market_prob`. Team legs carry `game_id`, `market ∈ {first_inning_runs,f5_runs}`, `side`, `line`, `odds`, `market_prob`.

**Key constraint — coverage.** `/edges` was comprehensive (every edge for every slate game). The builder saved feed is a **curated short list** (~10 low-risk constructions per run) whose legs cover only a handful of players/games and repeat across constructions. Migrating means the slate/quick-entry show **only the players the builder picked** — most games show nothing. This is accepted and intended (owner-approved 2026-07-29).

## 3. Decisions (owner-approved 2026-07-29)

1. **Both surfaces migrate** (slate GameCards + quick-entry), fully off `/edges`.
2. **First-inning line feeds from builder team legs** (the `?tier=all` team partition), not `/game-predictions`. Sparse but never stale.
3. **Suppress slate chips already shown in the section.** The slate GameCard player chips exclude any player leg already displayed in the "Low-risk builder parlays" section (the top-N constructions actually rendered). The quick-entry picker is a separate screen and keeps the full distinct list (no suppression there).
4. `model_prob` never surfaces; player/team labels use `joint_prob`/`market_prob`.

## 4. Design

### 4.1 Shared pure helpers — `lib/builderParlays.ts` (both clients, byte-identical)

The module stays byte-identical **across the two repos** (the constraint is cross-repo identity, not immutability); these additions go into both identically. All pure and unit-tested.

```ts
/** Stable identity for a player leg (dedup + suppression key). */
export function playerLegIdentity(leg: PlaystatBuilderPlayerLeg): string {
  return `${leg.player_id}|${leg.stat_type}|${leg.side}|${leg.line}`;
}

/** The set of player-leg identities across the given constructions
 *  (e.g. the top-N already shown in the section), for slate suppression. */
export function playerLegKeys(constructions: PlaystatBuilderConstruction[]): Set<string> {
  const keys = new Set<string>();
  for (const c of constructions)
    for (const leg of c.legs)
      if (leg.kind === 'player') keys.add(playerLegIdentity(leg));
  return keys;
}

/** Deduped flat list of the LATEST player run's player legs (quick-entry picker). */
export function distinctPlayerLegs(
  playerConstructions: PlaystatBuilderConstruction[]
): PlaystatBuilderPlayerLeg[] {
  const run = selectLatestRun(playerConstructions, Infinity); // full latest run, not just top-N
  const seen = new Set<string>();
  const out: PlaystatBuilderPlayerLeg[] = [];
  for (const c of run)
    for (const leg of c.legs)
      if (leg.kind === 'player') {
        const k = playerLegIdentity(leg);
        if (!seen.has(k)) { seen.add(k); out.push(leg); }
      }
  return out;
}

/** Latest player run's distinct player legs grouped by game_id, minus excluded keys. */
export function playerLegsByGame(
  playerConstructions: PlaystatBuilderConstruction[],
  excludeKeys: ReadonlySet<string> = new Set()
): Map<number, PlaystatBuilderPlayerLeg[]> {
  const map = new Map<number, PlaystatBuilderPlayerLeg[]>();
  for (const leg of distinctPlayerLegs(playerConstructions)) {
    if (excludeKeys.has(playerLegIdentity(leg))) continue;
    const list = map.get(leg.game_id) ?? [];
    list.push(leg);
    map.set(leg.game_id, list);
  }
  return map;
}

/** Latest team run's first-inning (NRFI) leg per game_id. */
export function firstInningLegByGame(
  teamConstructions: PlaystatBuilderConstruction[]
): Map<number, PlaystatBuilderTeamLeg> {
  const map = new Map<number, PlaystatBuilderTeamLeg>();
  const run = selectLatestRun(teamConstructions, Infinity);
  for (const c of run)
    for (const leg of c.legs)
      if (leg.kind === 'team' && leg.market === 'first_inning_runs' && !map.has(leg.game_id))
        map.set(leg.game_id, leg);
  return map;
}
```

`selectLatestRun(cons, Infinity)` returns the whole latest run (existing behavior: filter to latest date, sort by `joint_prob`, `slice(0, Infinity)` = all). No change to `selectLatestRun` itself.

### 4.2 Tonight page (`app/tonight/page.tsx` + `app/(tabs)/tonight.tsx`)

- **Remove** `usePlaystatEdges`, `usePlaystatGamePredictions`, and the `edgesByGame` / `firstInningByGame` memos. Tonight makes no `/edges` or `/game-predictions` request.
- Reuse the existing `playerCons` / `teamCons` partitions and the already-rendered section list (`builderConstructions`, which is `[]` when the section is hidden/empty):
  ```ts
  const shownKeys = useMemo(() => playerLegKeys(builderConstructions), [builderConstructions]);
  const slatePlayerLegsByGame = useMemo(
    () => playerLegsByGame(playerCons, shownKeys), [playerCons, shownKeys]);
  const slateFirstInningByGame = useMemo(
    () => firstInningLegByGame(teamCons), [teamCons]);
  ```
- Pass to each `GameCard`: `playerLegs={slatePlayerLegsByGame.get(game.game_id) ?? []}` and `firstInningLeg={slateFirstInningByGame.get(game.game_id)}`.

Suppression note: `builderConstructions` is the *displayed* section list (empty when the run is hidden by `isRunFullyPast` or absent), so when nothing is shown in the section, `shownKeys` is empty and the slate shows all builder player legs — correct.

### 4.3 GameCard (`components/tonight/GameCard.tsx` + mobile)

- Props change: `edges: PlaystatEdge[]` → `playerLegs: PlaystatBuilderPlayerLeg[]`; `firstInning?: PlaystatGamePrediction` → `firstInningLeg?: PlaystatBuilderTeamLeg`.
- **Player chips:** render each `playerLegs` entry as `{playerNameFromLabel(leg)} {leg.side} {leg.line} {leg.stat_type}  ({odds})` — reuse `playerNameFromLabel` / the same formatting `legDisplay` uses for player legs. No chips when the list is empty.
- **First-inning line:** from `firstInningLeg` — `1st inning {side} {line}: {round(market_prob*100)}%  ({odds})`. Shown only when a team leg exists for the game. Semantics shift from book/model prob to "builder's NRFI pick," consistent with retiring the frozen source.
- `PlaystatEdge` / `PlaystatGamePrediction` imports removed from GameCard.

### 4.4 Quick-entry (`components/bets/BetForm.tsx` + `app/modal.tsx`)

- Replace `usePlaystatEdges(slate.date)` (and `usePlaystatSlate`, used only to supply that date) with `usePlaystatBuilderParlays()`; derive `distinctPlayerLegs(playerCons)` where `playerCons = (data ?? []).filter(c => !hasTeamLeg(c))`.
- Panel heading "Tonight's edges (from playstat)" → **"Tonight's builder picks"**. Same "+ Add to bet" UX; `addLegFromBuilderLeg(leg)` maps `player_name = playerNameFromLabel(leg)`, `stat_type = leg.stat_type`, `line_value = String(leg.line)`, `side = leg.side`, `odds = String(leg.odds)`.
- Team legs are **not** offered in quick-entry (player-prop only; team markets are log-only via their card). No suppression here (separate screen).

### 4.5 Cleanup

- Delete the now-unused `usePlaystatEdges` and `usePlaystatGamePredictions` hooks in both repos.
- **Keep** `playstatApi.edges` / `playstatApi.gamePredictions` methods and the `PlaystatEdge` / `PlaystatGamePrediction` types (endpoints still serve; contract intact — mirrors keeping `parlays.list` when the model UI was retired).

### 4.6 Empty / coverage behavior

Most slate games show no player chips and no first-inning line (curated feed + sparse team tier). This is normal — no per-game empty-state text. The "Low-risk builder parlays" and "Team markets" sections are unchanged.

### 4.7 Mobile parity

`BudgerrApp` mirrors §4.1–4.5 identically; `lib/builderParlays.ts` stays byte-identical to web's. Static verify (`npx tsc --noEmit`).

## 5. Non-goals

- No backend/settlement/migration/launchd changes.
- Not restoring comprehensive per-game coverage (that was `/edges`, frozen).
- Not deleting the `/edges`/`/game-predictions` API methods or types (contract intact).
- Not changing the builder sections' layout.
- `model_prob` stays hidden.

## 6. Acceptance criteria

- Web `npm run build` passes; Vitest covers the new helpers (`playerLegIdentity`, `playerLegKeys`, `distinctPlayerLegs`, `playerLegsByGame` with/without exclusion, `firstInningLegByGame`) over a mixed `?tier=all` fixture.
- On `/tonight`: no `/edges` or `/game-predictions` network requests; GameCards show builder player chips only on games the builder picked, **excluding** legs already shown in the "Low-risk builder parlays" section; the first-inning line shows only for games with a builder team leg.
- Quick-entry shows "Tonight's builder picks" from the builder feed and "+ Add to bet" adds a correct leg draft.
- Mobile `npx tsc --noEmit` passes; `lib/builderParlays.ts` byte-identical across repos.
- `model_prob` appears nowhere.
- README §15 / §16 updated in the same commit as merge.

## 7. Verification plan (architect)

- Web build + Vitest; drive `/tonight` in the browser pane, confirm via `read_network_requests` that no `/edges` or `/game-predictions` calls fire, and that slate chips are suppressed for section legs; test quick-entry add-to-bet.
- Mobile `tsc`.
- Diff `lib/builderParlays.ts` across repos for byte-identity.

## 8. Follow-ups (flagged, not built here)

- If richer per-game coverage is wanted later, that needs a Playstat-side comprehensive market feed (the frozen `/edges` replacement) — coordinate before building.
- Inline team fields (`home_team`/`away_team`) remain unconsumed (prior follow-up).
