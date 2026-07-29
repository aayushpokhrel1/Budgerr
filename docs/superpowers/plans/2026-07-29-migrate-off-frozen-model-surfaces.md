# Migrate Tonight + Quick-entry off Frozen Model Surfaces — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Feed the slate GameCards and the bet quick-entry from the market-ranked builder feed (`?tier=all`, already fetched on Tonight) instead of the now-frozen `/edges` and `/game-predictions`.

**Architecture:** Add pure helpers to the byte-identical `lib/builderParlays.ts` that group the latest builder run's player legs by game (with a suppression set) and pick the first-inning team leg per game. Tonight builds those maps from the partitions it already computes and passes them to GameCard; quick-entry lists the builder's distinct player legs. Remove the frozen-surface hooks; keep their API methods + types (contract intact).

**Tech Stack:** Next.js + React Query + Tailwind (`budgerr-web`); Expo/React Native + React Query (`BudgerrApp`). Spec: `docs/superpowers/specs/2026-07-29-migrate-off-frozen-model-surfaces-design.md`.

## Global Constraints

- **Frontend only.** No backend, migrations, launchd, or settlement changes.
- **`lib/builderParlays.ts` must stay byte-identical between the two repos.** Task 1 edits it in `budgerr-web`; Task 4 copies that exact file into `BudgerrApp`. No other task edits it.
- **`model_prob` must never surface** in the UI. Player/team display uses `market_prob`/`joint_prob`/odds only.
- **Keep** `playstatApi.edges` / `playstatApi.gamePredictions` methods and the `PlaystatEdge` / `PlaystatGamePrediction` types (endpoints still serve; contract intact). Only the React-Query hooks that consume them are deleted.
- **Slate chip suppression:** GameCard player chips exclude any player leg already shown in the rendered "Low-risk builder parlays" section. Quick-entry does NOT suppress (separate screen).
- **Coverage is expected to be sparse** — most games show no chips / no first-inning line. Normal; no per-game empty-state text.
- **graphify:** orient with `graphify query "<question>"` before grepping; `graphify update .` currently refuses in both frontend repos (known tooling issue) — do not force it. Reading/editing the specific files a task names is fine.
- **Commits:** commit on the current branch, **never push**; end every commit message with:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

## File map

**`budgerr-web`:**
- Modify `lib/builderParlays.ts` — add 5 pure helpers (Task 1).
- Modify `lib/builderParlays.test.ts` — helper tests (Task 1).
- Modify `components/tonight/GameCard.tsx` — builder-leg props (Task 2).
- Modify `app/tonight/page.tsx` — drop frozen hooks, build per-game maps, suppression (Task 2).
- Modify `components/bets/BetForm.tsx` — builder-fed quick-entry (Task 3).
- Modify `lib/queries.ts` — delete `usePlaystatEdges` + `usePlaystatGamePredictions` (Task 3).

**`BudgerrApp`:**
- Overwrite `lib/builderParlays.ts` — verbatim copy from web (Task 4).
- Modify `components/tonight/GameCard.tsx` (Task 5).
- Modify `app/(tabs)/tonight.tsx` (Task 5).
- Modify `app/modal.tsx` — builder-fed quick-entry (Task 6).
- Modify `lib/queries.ts` — delete the two hooks (Task 6).

---

## Task 1 (budgerr-web): builderParlays.ts helpers + tests

**Files:**
- Modify: `lib/builderParlays.ts` (import line 2; append helpers at end)
- Test: `lib/builderParlays.test.ts`

**Interfaces:**
- Produces: `playerLegIdentity(leg)`, `playerLegKeys(constructions)`, `distinctPlayerLegs(playerConstructions)`, `playerLegsByGame(playerConstructions, excludeKeys?)`, `firstInningLegByGame(teamConstructions)` — signatures in Step 3.
- Consumes: existing `selectLatestRun` from this module.

- [ ] **Step 1: Write the failing tests**

Append to `lib/builderParlays.test.ts`:

```ts
import {
  playerLegIdentity,
  playerLegKeys,
  distinctPlayerLegs,
  playerLegsByGame,
  firstInningLegByGame,
} from './builderParlays';
import type { PlaystatBuilderConstruction } from './playstat';

function pLeg(game_id: number, player_id: number, stat: string, side: string, line: number) {
  return { kind: 'player' as const, game_id, player_id, stat_type: stat, market: null,
    label: `P${player_id} ${stat} ${side} ${line}`, side, line, odds: -120,
    market_prob: 0.9, model_prob: null };
}
function tLeg(game_id: number, market: 'first_inning_runs' | 'f5_runs', side: string, line: number) {
  return { kind: 'team' as const, game_id, player_id: null, stat_type: null, market,
    label: `${market} ${side} ${line}`, side, line, odds: -150, market_prob: 0.57, model_prob: null };
}
function con(id: number, date: string, jp: number, legs: any[]): PlaystatBuilderConstruction {
  return { parlay_id: id, created_at: `${date} 09:00:00-04:00`, target_payout: 1.4,
    joint_prob: jp, combined_odds: 1.4, n_legs: legs.length, legs };
}

describe('builder slate/quick-entry helpers', () => {
  const players = [
    con(1, '2026-07-28', 0.92, [pLeg(10, 100, 'runs', 'over', 0.5), pLeg(20, 200, 'hits', 'over', 0.5)]),
    con(2, '2026-07-28', 0.90, [pLeg(10, 100, 'runs', 'over', 0.5), pLeg(30, 300, 'rbis', 'over', 0.5)]),
    con(9, '2026-07-27', 0.99, [pLeg(40, 400, 'runs', 'over', 0.5)]), // older run, must be excluded
  ];
  const teams = [
    con(50, '2026-07-26', 0.32, [tLeg(70, 'first_inning_runs', 'under', 0.5), tLeg(80, 'f5_runs', 'under', 1.5)]),
  ];

  it('playerLegIdentity is player_id|stat|side|line', () => {
    expect(playerLegIdentity(pLeg(10, 100, 'runs', 'over', 0.5) as any)).toBe('100|runs|over|0.5');
  });

  it('playerLegKeys collects player-leg identities across constructions', () => {
    expect(playerLegKeys([players[0]])).toEqual(new Set(['100|runs|over|0.5', '200|hits|over|0.5']));
  });

  it('distinctPlayerLegs dedupes the latest run and excludes older runs', () => {
    const legs = distinctPlayerLegs(players);
    expect(legs.map((l) => l.player_id)).toEqual([100, 200, 300]); // 100 deduped; 400 (older run) gone
  });

  it('playerLegsByGame groups by game_id and honors excludeKeys', () => {
    const all = playerLegsByGame(players);
    expect([...all.keys()].sort((a, b) => a - b)).toEqual([10, 20, 30]);
    const excluded = playerLegsByGame(players, new Set(['100|runs|over|0.5']));
    expect([...excluded.keys()].sort((a, b) => a - b)).toEqual([20, 30]); // game 10 suppressed
  });

  it('firstInningLegByGame keeps only first_inning_runs legs, one per game', () => {
    const map = firstInningLegByGame(teams);
    expect([...map.keys()]).toEqual([70]); // f5 game 80 excluded
    expect(map.get(70)?.market).toBe('first_inning_runs');
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/dev/budgerr-web && npx vitest run lib/builderParlays.test.ts`
Expected: FAIL — the five new functions are not exported yet.

- [ ] **Step 3: Add the helpers**

In `lib/builderParlays.ts`, change the import on line 2 to add the two leg types:

```ts
import { PlaystatBuilderConstruction, PlaystatBuilderLeg, PlaystatBuilderPlayerLeg, PlaystatBuilderTeamLeg, PlaystatGame } from './playstat';
```

Append at the end of the file:

```ts
/** Stable identity for a player leg (dedup + slate-suppression key). */
export function playerLegIdentity(leg: PlaystatBuilderPlayerLeg): string {
  return `${leg.player_id}|${leg.stat_type}|${leg.side}|${leg.line}`;
}

/** Set of player-leg identities across the given constructions (e.g. the
 *  top-N already shown in the section), for slate suppression. */
export function playerLegKeys(constructions: PlaystatBuilderConstruction[]): Set<string> {
  const keys = new Set<string>();
  for (const c of constructions)
    for (const leg of c.legs) if (leg.kind === 'player') keys.add(playerLegIdentity(leg));
  return keys;
}

/** Deduped flat list of the LATEST player run's player legs (quick-entry picker). */
export function distinctPlayerLegs(
  playerConstructions: PlaystatBuilderConstruction[]
): PlaystatBuilderPlayerLeg[] {
  const run = selectLatestRun(playerConstructions, Infinity);
  const seen = new Set<string>();
  const out: PlaystatBuilderPlayerLeg[] = [];
  for (const c of run)
    for (const leg of c.legs)
      if (leg.kind === 'player') {
        const k = playerLegIdentity(leg);
        if (!seen.has(k)) {
          seen.add(k);
          out.push(leg);
        }
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

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ~/dev/budgerr-web && npx vitest run lib/builderParlays.test.ts`
Expected: PASS (existing 18 + 5 new).

- [ ] **Step 5: Commit**

```bash
cd ~/dev/budgerr-web && git add lib/builderParlays.ts lib/builderParlays.test.ts
git commit -m "feat: builder slate/quick-entry helpers (per-game legs, suppression)

playerLegIdentity, playerLegKeys, distinctPlayerLegs, playerLegsByGame,
firstInningLegByGame — pure helpers to feed the slate + quick-entry off the
builder feed instead of the frozen /edges + /game-predictions.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2 (budgerr-web): GameCard + Tonight page onto the builder feed

**Files:**
- Modify: `components/tonight/GameCard.tsx` (full rewrite below)
- Modify: `app/tonight/page.tsx`

**Interfaces:**
- Consumes: `playerLegsByGame`, `firstInningLegByGame`, `playerLegKeys`, `playerNameFromLabel` (Task 1 + existing); `PlaystatBuilderPlayerLeg`, `PlaystatBuilderTeamLeg` types.
- Produces: `GameCard` props `{ game, playerLegs: PlaystatBuilderPlayerLeg[], firstInningLeg?: PlaystatBuilderTeamLeg }`.

- [ ] **Step 1: Rewrite GameCard**

Replace the entire contents of `components/tonight/GameCard.tsx` with:

```tsx
import { PlaystatBuilderPlayerLeg, PlaystatBuilderTeamLeg, PlaystatGame } from '@/lib/playstat';
import { marketLabel, playerNameFromLabel } from '@/lib/builderParlays';

function statusLabel(status: string | null): string {
  if (!status || status === 'NS' || status === 'S') return 'Upcoming';
  if (status === 'FT' || status === 'AOT') return 'Final';
  return status;
}

export function GameCard({
  game,
  playerLegs,
  firstInningLeg,
}: {
  game: PlaystatGame;
  playerLegs: PlaystatBuilderPlayerLeg[];
  firstInningLeg?: PlaystatBuilderTeamLeg;
}) {
  const label = statusLabel(game.status);
  const isFinal = label === 'Final';

  return (
    <div className="rounded-xl border border-border p-4">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium truncate">
          {game.away_team_name} @ {game.home_team_name}
        </span>
        <span
          className={
            isFinal
              ? 'text-xs font-medium px-2 py-1 rounded bg-surface text-muted whitespace-nowrap'
              : 'text-xs font-medium px-2 py-1 rounded bg-surface text-accent whitespace-nowrap'
          }
        >
          {label}
        </span>
      </div>

      {firstInningLeg && (
        <p className="mt-2 text-xs text-muted">
          {marketLabel(firstInningLeg.market)} {firstInningLeg.side} {firstInningLeg.line}:{' '}
          <span className="font-medium text-muted font-mono tabular-nums">
            {Math.round(firstInningLeg.market_prob * 100)}%
          </span>
          <span className="text-accent font-mono tabular-nums">
            {' '}({firstInningLeg.odds > 0 ? '+' : ''}
            {firstInningLeg.odds})
          </span>
        </p>
      )}

      {playerLegs.length > 0 && (
        <div className="mt-2 space-y-1">
          {playerLegs.map((leg) => (
            <p
              key={`${leg.player_id}-${leg.stat_type}-${leg.side}-${leg.line}`}
              className="text-xs text-muted truncate"
            >
              {playerNameFromLabel(leg)} {leg.side} {leg.line} {leg.stat_type}{' '}
              <span className="text-accent font-mono tabular-nums">
                ({leg.odds > 0 ? '+' : ''}
                {leg.odds})
              </span>
            </p>
          ))}
        </div>
      )}
    </div>
  );
}
```

Note: `marketLabel` maps `first_inning_runs` → `NRFI`, so the line reads e.g. `NRFI under 0.5: 57% (-154)`.

- [ ] **Step 2: Rewire the Tonight page — imports**

In `app/tonight/page.tsx`:
- Change the builderParlays import (line 9) to add the three helpers:
  ```tsx
  import { firstInningLegByGame, hasTeamLeg, isRunFullyPast, playerLegKeys, playerLegsByGame, runDate, selectLatestRun } from '@/lib/builderParlays';
  ```
- Change the playstat types import (line 8) to just:
  ```tsx
  import { PlaystatGame } from '@/lib/playstat';
  ```
  (`PlaystatEdge`/`PlaystatGamePrediction` are no longer used here; the new memos infer the builder-leg types from the helper return types, so no leg-type import is needed.)
- In the `@/lib/queries` import block, remove `usePlaystatEdges` and `usePlaystatGamePredictions`.

- [ ] **Step 3: Rewire the Tonight page — drop frozen fetches, add per-game maps**

Remove these lines (the frozen fetches and their memos):
```tsx
  const edges = usePlaystatEdges(slate.data?.date);
  const gamePredictions = usePlaystatGamePredictions(slate.data?.date);
```
and the entire `edgesByGame` useMemo block and the entire `firstInningByGame` useMemo block.

After the existing `builderConstructions` useMemo (which stays), add:
```tsx
  // Slate cards are fed by the builder feed (frozen /edges + /game-predictions retired).
  // Suppress player legs already shown in the rendered low-risk section.
  const shownKeys = useMemo(() => playerLegKeys(builderConstructions), [builderConstructions]);
  const slatePlayerLegsByGame = useMemo(
    () => playerLegsByGame(playerCons, shownKeys),
    [playerCons, shownKeys]
  );
  const slateFirstInningByGame = useMemo(
    () => firstInningLegByGame(teamCons),
    [teamCons]
  );
```
(`playerCons` and `teamCons` already exist from the team-tier feature.)

- [ ] **Step 4: Rewire the Tonight page — GameCard props**

Replace the `<GameCard ... />` usage in the games map with:
```tsx
        {games.map((game) => (
          <GameCard
            key={game.game_id}
            game={game}
            playerLegs={slatePlayerLegsByGame.get(game.game_id) ?? []}
            firstInningLeg={slateFirstInningByGame.get(game.game_id)}
          />
        ))}
```

- [ ] **Step 5: Build**

Run: `cd ~/dev/budgerr-web && npm run build`
Expected: build succeeds; no unused-import errors (`PlaystatEdge`/`PlaystatGamePrediction` no longer imported in these files; `usePlaystatEdges`/`usePlaystatGamePredictions` no longer imported here — they still exist in `lib/queries.ts`, deleted in Task 3).

- [ ] **Step 6: Commit**

```bash
cd ~/dev/budgerr-web && git add components/tonight/GameCard.tsx app/tonight/page.tsx
git commit -m "feat: feed slate GameCards from the builder feed, not frozen /edges

GameCard now takes builder player legs (per game, suppressing legs already shown
in the low-risk section) and a builder first-inning team leg, replacing the
frozen /edges chips + /game-predictions NRFI line.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3 (budgerr-web): Quick-entry onto the builder feed + delete unused hooks

**Files:**
- Modify: `components/bets/BetForm.tsx`
- Modify: `lib/queries.ts` (delete `usePlaystatEdges` + `usePlaystatGamePredictions`)

**Interfaces:**
- Consumes: `usePlaystatBuilderParlays` (existing), `hasTeamLeg` + `distinctPlayerLegs` + `playerNameFromLabel` (Task 1 + existing), `PlaystatBuilderPlayerLeg` type.

- [ ] **Step 1: Rewire BetForm imports + data source**

In `components/bets/BetForm.tsx`:
- Replace line 6:
  ```tsx
  import { useCreateBet, usePlaystatBuilderParlays } from '@/lib/queries';
  ```
- Replace line 7:
  ```tsx
  import { PlaystatBuilderPlayerLeg } from '@/lib/playstat';
  import { distinctPlayerLegs, hasTeamLeg, playerNameFromLabel } from '@/lib/builderParlays';
  ```
- Replace the data hooks (lines 21-22):
  ```tsx
  const builderParlays = usePlaystatBuilderParlays();
  const builderPicks = useMemo(() => {
    const playerCons = (builderParlays.data ?? []).filter((c) => !hasTeamLeg(c));
    return distinctPlayerLegs(playerCons);
  }, [builderParlays.data]);
  ```
- Add `useMemo` to the React import on line 3:
  ```tsx
  import { useMemo, useRef, useState } from 'react';
  ```

- [ ] **Step 2: Replace `addLegFromEdge` with `addLegFromBuilderLeg`**

Replace the `addLegFromEdge` function (lines 40-51) with:
```tsx
  const addLegFromBuilderLeg = (leg: PlaystatBuilderPlayerLeg) => {
    setLegs((prev) => [
      ...prev,
      {
        player_name: playerNameFromLabel(leg),
        stat_type: leg.stat_type,
        line_value: String(leg.line),
        side: leg.side,
        odds: String(leg.odds),
      },
    ]);
  };
```

- [ ] **Step 3: Replace the "Tonight's edges" panel**

Replace the whole `{tonightsEdges.data && tonightsEdges.data.length > 0 && ( ... )}` block (lines 193-219) with:
```tsx
      {builderPicks.length > 0 && (
        <div className="rounded-lg bg-surface p-3">
          <p className="text-xs text-muted mb-2">Tonight&apos;s builder picks</p>
          <div className="space-y-1">
            {builderPicks.map((leg) => (
              <div
                key={`${leg.player_id}-${leg.game_id}-${leg.stat_type}-${leg.side}-${leg.line}`}
                className="flex items-center justify-between text-sm"
              >
                <span>
                  {playerNameFromLabel(leg)} {leg.side} {leg.line} {leg.stat_type}{' '}
                  <span className="text-muted font-mono tabular-nums">
                    ({leg.odds > 0 ? '+' : ''}
                    {leg.odds})
                  </span>
                </span>
                <button
                  className="text-xs text-accent hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent rounded"
                  onClick={() => addLegFromBuilderLeg(leg)}
                >
                  + Add to bet
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
```

- [ ] **Step 4: Delete the now-unused hooks**

In `lib/queries.ts`, delete the entire `usePlaystatEdges` function (lines ~270-276) and the entire `usePlaystatGamePredictions` function (lines ~278-283). Leave `usePlaystatGames`, `usePlaystatSlate`, `usePlaystatBuilderParlays`, and the `playstatApi.edges`/`gamePredictions` methods untouched.

- [ ] **Step 5: Build**

Run: `cd ~/dev/budgerr-web && npm run build`
Expected: build succeeds. If the build reports `usePlaystatSlate` unused in BetForm, remove that import too (it was only used to supply the edges date).

- [ ] **Step 6: Commit**

```bash
cd ~/dev/budgerr-web && git add components/bets/BetForm.tsx lib/queries.ts
git commit -m "feat: feed bet quick-entry from the builder feed; drop frozen hooks

Quick-entry 'Add to bet' picker now lists the builder's distinct player legs
('Tonight's builder picks') instead of frozen /edges. Delete the now-unused
usePlaystatEdges + usePlaystatGamePredictions hooks (API methods + types kept).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4 (BudgerrApp): copy builderParlays.ts verbatim

**Files:**
- Overwrite: `lib/builderParlays.ts` (byte-identical copy from web)

- [ ] **Step 1: Copy the file**

Run:
```bash
cp ~/dev/budgerr-web/lib/builderParlays.ts ~/dev/BudgerrApp/lib/builderParlays.ts
```

- [ ] **Step 2: Verify byte-identity**

Run: `diff -q ~/dev/budgerr-web/lib/builderParlays.ts ~/dev/BudgerrApp/lib/builderParlays.ts`
Expected: no output (identical).

- [ ] **Step 3: Typecheck**

Run: `cd ~/dev/BudgerrApp && npx tsc --noEmit`
Expected: no errors (the new helpers reference `PlaystatBuilderPlayerLeg`/`PlaystatBuilderTeamLeg`, which exist in mobile's `lib/playstat.ts`).

- [ ] **Step 4: Commit**

```bash
cd ~/dev/BudgerrApp && git add lib/builderParlays.ts
git commit -m "feat: builder slate/quick-entry helpers (byte-identical copy from web)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5 (BudgerrApp): GameCard + Tonight screen onto the builder feed

**Files:**
- Modify: `components/tonight/GameCard.tsx` (full rewrite below)
- Modify: `app/(tabs)/tonight.tsx`

**Interfaces:**
- Consumes: Task 4 helpers; `PlaystatBuilderPlayerLeg`/`PlaystatBuilderTeamLeg` types; `marketLabel`/`playerNameFromLabel`.
- Produces: mobile `GameCard` props `{ game, playerLegs, firstInningLeg }`.

- [ ] **Step 1: Rewrite mobile GameCard**

Replace the entire contents of `components/tonight/GameCard.tsx` with:

```tsx
import { StyleSheet, View } from 'react-native';

import { Text } from '@/components/Themed';
import Colors from '@/constants/Colors';
import { cardShadow } from '@/constants/Shadow';
import { useColorScheme } from '@/components/useColorScheme';
import { PlaystatBuilderPlayerLeg, PlaystatBuilderTeamLeg, PlaystatGame } from '@/lib/playstat';
import { marketLabel, playerNameFromLabel } from '@/lib/builderParlays';

function statusLabel(status: string | null): string {
  if (!status || status === 'NS' || status === 'S') return 'Upcoming';
  if (status === 'FT' || status === 'AOT') return 'Final';
  return status;
}

export function GameCard({
  game,
  playerLegs,
  firstInningLeg,
}: {
  game: PlaystatGame;
  playerLegs: PlaystatBuilderPlayerLeg[];
  firstInningLeg?: PlaystatBuilderTeamLeg;
}) {
  const theme = Colors[useColorScheme()];
  const label = statusLabel(game.status);
  const isFinal = label === 'Final';

  return (
    <View style={[styles.card, { backgroundColor: theme.card, borderColor: theme.border }]}>
      <View style={styles.headerRow}>
        <Text style={styles.matchup} numberOfLines={1}>
          {game.away_team_name} @ {game.home_team_name}
        </Text>
        <View style={[styles.badge, { backgroundColor: theme.border }]}>
          <Text style={[styles.badgeText, { color: isFinal ? theme.textSecondary : theme.tint }]}>
            {label}
          </Text>
        </View>
      </View>

      {firstInningLeg && (
        <Text style={[styles.edgeRow, { color: theme.textSecondary, marginTop: 8 }]}>
          {marketLabel(firstInningLeg.market)} {firstInningLeg.side} {firstInningLeg.line}:{' '}
          <Text style={{ color: theme.textSecondary, fontWeight: '500' }}>
            {Math.round(firstInningLeg.market_prob * 100)}%
          </Text>
          <Text style={{ color: theme.tint }}>
            {' '}({firstInningLeg.odds > 0 ? '+' : ''}
            {firstInningLeg.odds})
          </Text>
        </Text>
      )}

      {playerLegs.length > 0 && (
        <View style={styles.edgesList}>
          {playerLegs.map((leg) => (
            <Text
              key={`${leg.player_id}-${leg.stat_type}-${leg.side}-${leg.line}`}
              style={[styles.edgeRow, { color: theme.textSecondary }]}
              numberOfLines={1}
            >
              {playerNameFromLabel(leg)} {leg.side} {leg.line} {leg.stat_type}{' '}
              <Text style={{ color: theme.tint }}>
                ({leg.odds > 0 ? '+' : ''}
                {leg.odds})
              </Text>
            </Text>
          ))}
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  card: { borderRadius: 12, borderWidth: 0.5, padding: 14, marginBottom: 10, ...cardShadow },
  headerRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8 },
  matchup: { fontSize: 14, fontWeight: '500', flex: 1 },
  badge: { paddingHorizontal: 8, paddingVertical: 3, borderRadius: 6 },
  badgeText: { fontSize: 11, fontWeight: '500' },
  edgesList: { marginTop: 8, gap: 4 },
  edgeRow: { fontSize: 12 },
});
```

- [ ] **Step 2: Rewire mobile Tonight — imports**

In `app/(tabs)/tonight.tsx`:
- Change the builderParlays import (line 10) to:
  ```tsx
  import { firstInningLegByGame, hasTeamLeg, isRunFullyPast, playerLegKeys, playerLegsByGame, runDate, selectLatestRun } from '@/lib/builderParlays';
  ```
- Change the playstat types import (line 11) to just:
  ```tsx
  import { PlaystatGame } from '@/lib/playstat';
  ```
  (`PlaystatEdge`/`PlaystatGamePrediction` are no longer used here; the new memos infer the builder-leg types.)
- In the `@/lib/queries` import block, remove `usePlaystatEdges` and `usePlaystatGamePredictions`.

- [ ] **Step 3: Rewire mobile Tonight — drop frozen fetches, add per-game maps**

Remove:
```tsx
  const edges = usePlaystatEdges(slate.data?.date);
  const gamePredictions = usePlaystatGamePredictions(slate.data?.date);
```
and the entire `edgesByGame` useMemo block and the entire `firstInningByGame` useMemo block. Also remove `edges.refetch();` and `gamePredictions.refetch();` from `refetchAll`.

After the `builderConstructions`/`teamConstructions` memos, add:
```tsx
  const shownKeys = useMemo(() => playerLegKeys(builderConstructions), [builderConstructions]);
  const slatePlayerLegsByGame = useMemo(
    () => playerLegsByGame(playerCons, shownKeys),
    [playerCons, shownKeys]
  );
  const slateFirstInningByGame = useMemo(
    () => firstInningLegByGame(teamCons),
    [teamCons]
  );
```

- [ ] **Step 4: Rewire mobile Tonight — GameCard props**

Replace the `<GameCard ... />` usage in the games map with:
```tsx
      {games.map((game) => (
        <GameCard
          key={game.game_id}
          game={game}
          playerLegs={slatePlayerLegsByGame.get(game.game_id) ?? []}
          firstInningLeg={slateFirstInningByGame.get(game.game_id)}
        />
      ))}
```

- [ ] **Step 5: Typecheck**

Run: `cd ~/dev/BudgerrApp && npx tsc --noEmit`
Expected: no errors (`PlaystatEdge`/`PlaystatGamePrediction` no longer referenced in these two files).

- [ ] **Step 6: Commit**

```bash
cd ~/dev/BudgerrApp && git add components/tonight/GameCard.tsx "app/(tabs)/tonight.tsx"
git commit -m "feat: feed slate GameCards from the builder feed, not frozen /edges (mobile)

Mirror budgerr-web: GameCard takes builder player legs (per game, suppressing
legs shown in the low-risk section) + a builder first-inning team leg.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6 (BudgerrApp): Quick-entry onto the builder feed + delete unused hooks

**Files:**
- Modify: `app/modal.tsx`
- Modify: `lib/queries.ts` (delete `usePlaystatEdges` + `usePlaystatGamePredictions`)

- [ ] **Step 1: Rewire modal imports + data source**

In `app/modal.tsx`:
- Add `useMemo` to the React import (line 3): `import { useMemo, useState } from 'react';`
- Replace line 17:
  ```tsx
  import { PlaystatBuilderPlayerLeg } from '@/lib/playstat';
  import { distinctPlayerLegs, hasTeamLeg, playerNameFromLabel } from '@/lib/builderParlays';
  ```
- Replace line 18:
  ```tsx
  import { useCreateBet, usePlaystatBuilderParlays } from '@/lib/queries';
  ```
- Replace the data hooks (lines 34-35):
  ```tsx
  const builderParlays = usePlaystatBuilderParlays();
  const builderPicks = useMemo(() => {
    const playerCons = (builderParlays.data ?? []).filter((c) => !hasTeamLeg(c));
    return distinctPlayerLegs(playerCons);
  }, [builderParlays.data]);
  ```

- [ ] **Step 2: Replace `addLegFromEdge` with `addLegFromBuilderLeg`**

Replace the `addLegFromEdge` function (lines 105-116) with:
```tsx
  const addLegFromBuilderLeg = (leg: PlaystatBuilderPlayerLeg) => {
    setLegs((prev) => [
      ...prev,
      {
        player_name: playerNameFromLabel(leg),
        stat_type: leg.stat_type,
        line_value: String(leg.line),
        side: leg.side,
        odds: String(leg.odds),
      },
    ]);
  };
```

- [ ] **Step 3: Replace the "Tonight's edges" card**

Replace the whole `{tonightsEdges.data && tonightsEdges.data.length > 0 && ( ... )}` block (lines 205-225) with:
```tsx
      {builderPicks.length > 0 && (
        <View style={[styles.edgesCard, { backgroundColor: theme.card }]}>
          <Text style={[styles.edgesTitle, { color: theme.textSecondary }]}>
            Tonight&apos;s builder picks
          </Text>
          {builderPicks.map((leg) => (
            <View
              key={`${leg.player_id}-${leg.game_id}-${leg.stat_type}-${leg.side}-${leg.line}`}
              style={styles.edgeRow}
            >
              <Text style={{ fontSize: 13, flex: 1 }} numberOfLines={1}>
                {playerNameFromLabel(leg)} {leg.side} {leg.line} {leg.stat_type}{' '}
                <Text style={{ color: theme.textMuted }}>
                  ({leg.odds > 0 ? '+' : ''}
                  {leg.odds})
                </Text>
              </Text>
              <Pressable onPress={() => addLegFromBuilderLeg(leg)}>
                <Text style={{ color: theme.tint, fontSize: 13 }}>+ Add</Text>
              </Pressable>
            </View>
          ))}
        </View>
      )}
```

- [ ] **Step 4: Delete the now-unused hooks**

In `lib/queries.ts`, delete the entire `usePlaystatEdges` function and the entire `usePlaystatGamePredictions` function. Leave the other hooks and the `playstatApi.edges`/`gamePredictions` methods untouched.

- [ ] **Step 5: Typecheck**

Run: `cd ~/dev/BudgerrApp && npx tsc --noEmit`
Expected: no errors. If `usePlaystatSlate` is now unused in `modal.tsx`, remove that import too.

- [ ] **Step 6: Commit**

```bash
cd ~/dev/BudgerrApp && git add app/modal.tsx lib/queries.ts
git commit -m "feat: feed bet quick-entry from the builder feed; drop frozen hooks (mobile)

Mirror budgerr-web: quick-entry picker lists builder distinct player legs;
delete unused usePlaystatEdges + usePlaystatGamePredictions (methods+types kept).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7 (architect, not a worker): Verify + README + merge/push

- [ ] **Step 1: builderParlays.ts byte-identity**

Run: `diff -q ~/dev/BudgerrApp/lib/builderParlays.ts ~/dev/budgerr-web/lib/builderParlays.ts`
Expected: no output.

- [ ] **Step 2: Web build + full Vitest**

Run: `cd ~/dev/budgerr-web && npm run build && npx vitest run`
Expected: build passes; all tests (18 + 5 new) pass.

- [ ] **Step 3: Drive `/tonight` in the browser pane**

`preview_start "budgerr-web"`, navigate `/tonight`:
- Via `read_network_requests`, confirm **no** request to `/edges` or `/game-predictions` fires.
- Confirm slate GameCards show builder player chips only on games the builder picked, and that legs shown in the "Low-risk builder parlays" section do **not** also appear as slate chips (suppression). Confirm the first-inning line shows only where a builder team leg exists.
- Open the bet quick-entry (BetForm) and confirm the "Tonight's builder picks" panel lists builder legs and "+ Add to bet" adds a correct leg draft (then discard without logging, or clean up any test bet).

- [ ] **Step 4: Mobile typecheck**

Run: `cd ~/dev/BudgerrApp && npx tsc --noEmit`

- [ ] **Step 5: Update README §15/§16** documenting the migration (slate + quick-entry now builder-fed; first-inning line from builder team legs; frozen hooks removed, API methods/types kept), in the merge commit.

- [ ] **Step 6: Merge worker branches, commit README, push both frontend repos + Budgerr.**

---

## Self-review

- **Spec coverage:** §4.1 helpers → Task 1 (+ copy Task 4); §4.2 Tonight → Tasks 2/5; §4.3 GameCard → Tasks 2/5; §4.4 quick-entry → Tasks 3/6; §4.5 cleanup → Tasks 3/6 (hooks deleted; methods/types kept); §4.7 mobile parity → Tasks 4-6; §6 acceptance + §7 verification → Task 7. All covered.
- **Placeholder scan:** none — every code step has full code; README step names exact content.
- **Type consistency:** `playerLegIdentity`, `playerLegKeys`, `distinctPlayerLegs`, `playerLegsByGame(_, excludeKeys)`, `firstInningLegByGame`, `playerNameFromLabel`, `marketLabel`, and GameCard props `{ game, playerLegs, firstInningLeg }` are used consistently across web and mobile tasks.
- **Byte-identity guard:** `lib/builderParlays.ts` edited only in Task 1, copied verbatim in Task 4, diff-checked in Task 7.
