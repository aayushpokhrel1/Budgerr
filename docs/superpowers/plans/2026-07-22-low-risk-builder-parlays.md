# Low-risk Builder Parlays Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface Playstat's precomputed low-risk builder parlays (`GET /parlay-builder/saved`) in both frontends' Tonight view, retire the model `/parlay-recommendations` UI, and make builder parlays loggable as paper bets — with a fixture-verified team-market (NRFI/F5) branch.

**Architecture:** Additive types + a new API method + a React Query hook in each client's `lib/playstat.ts` / `lib/queries.ts`; a shared pure-logic module (`lib/builderParlays.ts`) for latest-run selection, leg display, and paper-bet payload construction (including the team branch); a new `BuilderParlayCard` mirroring the existing `ParlayCard`; Tonight view rewiring to the builder source. Team-market bets are **log-only** (no auto-settle). Web adds a minimal Vitest setup to unit-test the pure module; mobile mirrors the same logic verified by `tsc`.

**Tech Stack:** `budgerr-web` (Next.js App Router, React Query, Tailwind, TypeScript) + `BudgerrApp` (Expo / React Native, React Query, TypeScript). New dev dep: `vitest` in `budgerr-web` only.

## Global Constraints

- **Two repos.** Tasks 1–4 are in `~/dev/budgerr-web`; Tasks 5–7 are in `~/dev/BudgerrApp`. Never touch `~/dev/playstat`.
- **Endpoint:** `GET /parlay-builder/saved?limit=` via the existing proxy base (`<backend>/playstat`). No backend change.
- **Ranking & labels use `joint_prob` / `market_prob` ONLY. `model_prob` must never appear in the UI** (context-only, often null).
- **Team-market bets are log-only** — they log as paper bets but must NOT be made to auto-settle. Do not add `game_id`/`market` to `BetLegInput`. Do not touch the backend, alembic, or the auto-settle whitelist.
- **Leave the existing `PlaystatParlayLeg` / `PlaystatParlayRecommendation` types and `playstatApi.parlays.list()` in place** (the `/parlay-recommendations` contract stays intact); only the Tonight view stops *using* them.
- **graphify rule:** orient with `graphify query "<question>"` before grepping/reading raw source; run `graphify update .` after modifying code in a repo.
- **Commits:** commit on your task branch, **never push**. End every commit message with:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- **Verification bar:** web = `npm run build` + `npm run test` (new) + driven in the browser; mobile = `npx tsc --noEmit` (static only, no simulator).

---

## Task 1: Builder types, API method, and query hook (budgerr-web)

**Files:**
- Modify: `~/dev/budgerr-web/lib/playstat.ts` (add types after `PlaystatParlayRecommendation` ~L72; add `listBuilder` inside `playstatApi.parlays` ~L120-123)
- Modify: `~/dev/budgerr-web/lib/queries.ts` (add hook after `usePlaystatParlays` ~L292)

**Interfaces:**
- Produces: `PlaystatBuilderLeg` (discriminated union), `PlaystatBuilderConstruction`, `playstatApi.parlays.listBuilder(limit?)`, `usePlaystatBuilderParlays()`.

- [ ] **Step 1: Add the builder types to `lib/playstat.ts`** immediately after the `PlaystatParlayRecommendation` interface (do not modify the existing types):

```ts
export interface PlaystatBuilderLegBase {
  game_id: number;
  label: string;
  side: 'over' | 'under';
  line: number;
  odds: number;
  market_prob: number;
  model_prob: number | null; // CONTEXT ONLY — never surfaced as edge/value
}
export interface PlaystatBuilderPlayerLeg extends PlaystatBuilderLegBase {
  kind: 'player';
  player_id: number;
  stat_type: string;
  market: null;
}
export interface PlaystatBuilderTeamLeg extends PlaystatBuilderLegBase {
  kind: 'team';
  player_id: null;
  stat_type: null;
  market: 'first_inning_runs' | 'f5_runs';
}
export type PlaystatBuilderLeg = PlaystatBuilderPlayerLeg | PlaystatBuilderTeamLeg;

export interface PlaystatBuilderConstruction {
  parlay_id: number;
  created_at: string;
  target_payout: number; // 1.4 | 2.0
  joint_prob: number; // de-vigged MARKET joint prob — ranking/label basis
  combined_odds: number;
  n_legs: number;
  legs: PlaystatBuilderLeg[];
}
```

- [ ] **Step 2: Add the `listBuilder` method** inside the existing `parlays:` object in `playstatApi` (keep `list` untouched):

```ts
  parlays: {
    list: async (limit = 3): Promise<PlaystatParlayRecommendation[]> =>
      fetchJson<PlaystatParlayRecommendation[]>(`/parlay-recommendations?limit=${limit}`),
    listBuilder: async (limit = 10): Promise<PlaystatBuilderConstruction[]> =>
      fetchJson<PlaystatBuilderConstruction[]>(`/parlay-builder/saved?limit=${limit}`),
  },
```

- [ ] **Step 3: Add the query hook to `lib/queries.ts`** after `usePlaystatParlays` (and add `PlaystatBuilderConstruction` is not needed here — the hook infers it):

```ts
export function usePlaystatBuilderParlays() {
  return useQuery({
    queryKey: ['playstat-builder-parlays'],
    queryFn: () => playstatApi.parlays.listBuilder(),
  });
}
```

- [ ] **Step 4: Typecheck**

Run: `cd ~/dev/budgerr-web && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
cd ~/dev/budgerr-web
git add lib/playstat.ts lib/queries.ts
git commit -m "feat(web): builder parlay types, listBuilder API, query hook

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Shared pure module + fixture + Vitest + unit tests (budgerr-web)

**Files:**
- Create: `~/dev/budgerr-web/lib/builderParlays.ts`
- Create: `~/dev/budgerr-web/lib/__fixtures__/builderTeamConstruction.ts`
- Create: `~/dev/budgerr-web/lib/builderParlays.test.ts`
- Create: `~/dev/budgerr-web/vitest.config.ts`
- Modify: `~/dev/budgerr-web/package.json` (add `vitest` devDep + `test` script)

**Interfaces:**
- Consumes: `PlaystatBuilderConstruction`, `PlaystatBuilderLeg`, `PlaystatGame` (Task 1 + existing), `BetInput`, `BetLegInput` (existing `lib/api.ts`).
- Produces: `selectLatestRun`, `legDisplay`, `marketLabel`, `playerNameFromLabel`, `matchup`, `hasTeamLeg`, `builderConstructionToBetInput`, and the `builderTeamConstruction` fixture.

- [ ] **Step 1: Write the pure module `lib/builderParlays.ts`**

```ts
import { BetInput, BetLegInput } from './api';
import { PlaystatBuilderConstruction, PlaystatBuilderLeg, PlaystatGame } from './playstat';

const MARKET_LABEL: Record<'first_inning_runs' | 'f5_runs', string> = {
  first_inning_runs: 'NRFI',
  f5_runs: 'F5',
};

export function marketLabel(market: 'first_inning_runs' | 'f5_runs'): string {
  return MARKET_LABEL[market];
}

/** Player-leg labels are "{name} {stat_type} {side} {line}"; strip the known
 *  suffix to recover the name. Falls back to the raw label if it doesn't match. */
export function playerNameFromLabel(leg: {
  label: string;
  stat_type: string;
  side: string;
  line: number;
}): string {
  const suffix = ` ${leg.stat_type} ${leg.side} ${leg.line}`;
  return leg.label.endsWith(suffix) ? leg.label.slice(0, -suffix.length) : leg.label;
}

/** "Away @ Home" for a team leg, or undefined if the game isn't in the map. */
export function matchup(gameId: number, gamesById: Map<number, PlaystatGame>): string | undefined {
  const g = gamesById.get(gameId);
  return g ? `${g.away_team_name} @ ${g.home_team_name}` : undefined;
}

/** One-line display string for a leg (used by BuilderParlayCard). */
export function legDisplay(leg: PlaystatBuilderLeg, gamesById: Map<number, PlaystatGame>): string {
  if (leg.kind === 'team') {
    const m = matchup(leg.game_id, gamesById);
    return `${m ? `${m} — ` : ''}${marketLabel(leg.market)} ${leg.side} ${leg.line}`;
  }
  return `${playerNameFromLabel(leg)} ${leg.side} ${leg.stat_type}`;
}

export function hasTeamLeg(construction: PlaystatBuilderConstruction): boolean {
  return construction.legs.some((leg) => leg.kind === 'team');
}

/** Most-recent nightly run (by created_at DATE), top N by joint_prob desc. */
export function selectLatestRun(
  constructions: PlaystatBuilderConstruction[],
  n = 4
): PlaystatBuilderConstruction[] {
  if (constructions.length === 0) return [];
  const dates = constructions.map((c) => c.created_at.slice(0, 10)).sort();
  const latestDate = dates[dates.length - 1];
  return constructions
    .filter((c) => c.created_at.slice(0, 10) === latestDate)
    .sort((a, b) => b.joint_prob - a.joint_prob)
    .slice(0, n);
}

/** Paper-bet payload for a construction. Team legs log but cannot auto-settle
 *  (BetLegInput has no game_id/market) — that's intentional and documented. */
export function builderConstructionToBetInput(
  construction: PlaystatBuilderConstruction,
  gamesById: Map<number, PlaystatGame>,
  stake: number
): BetInput {
  const legs: BetLegInput[] = construction.legs.map((leg) => {
    if (leg.kind === 'team') {
      const m = matchup(leg.game_id, gamesById);
      return {
        player_name: `${m ?? `Game ${leg.game_id}`} · ${marketLabel(leg.market)}`,
        stat_type: leg.market,
        line_value: leg.line,
        side: leg.side,
        odds: leg.odds,
      };
    }
    return {
      player_name: playerNameFromLabel(leg),
      stat_type: leg.stat_type,
      line_value: leg.line,
      side: leg.side,
      odds: leg.odds,
    };
  });
  const gameDate = construction.legs
    .map((leg) => gamesById.get(leg.game_id)?.date)
    .find((d): d is string => !!d);
  return {
    sportsbook: 'paper',
    bet_type: construction.legs.length > 1 ? 'parlay' : 'single',
    stake,
    potential_payout: stake * construction.combined_odds,
    placed_at: gameDate ? `${gameDate}T12:00:00Z` : undefined,
    is_paper: true,
    legs,
  };
}
```

- [ ] **Step 2: Write the team fixture `lib/__fixtures__/builderTeamConstruction.ts`** (real `game_id`s from the live probe, so they resolve against the real slate in the browser demo):

```ts
import { PlaystatBuilderConstruction } from '../playstat';

/** Synthetic construction with team (NRFI/F5) legs. Real /parlay-builder/saved
 *  data is 100% player props today — team lines price near coin-flip and rarely
 *  clear Playstat's favorite floor — so this fixture exercises the team branch in
 *  unit tests and via the ?demo=builder-team dev path. */
export const builderTeamConstruction: PlaystatBuilderConstruction = {
  parlay_id: 999001,
  created_at: '2026-07-22 13:02:10.000000-04:00',
  target_payout: 1.4,
  joint_prob: 0.62,
  combined_odds: 1.42,
  n_legs: 2,
  legs: [
    {
      kind: 'team',
      game_id: 100823110,
      label: 'first_inning_runs under 0.5',
      side: 'under',
      line: 0.5,
      odds: -120,
      market_prob: 0.58,
      model_prob: null,
      player_id: null,
      stat_type: null,
      market: 'first_inning_runs',
    },
    {
      kind: 'team',
      game_id: 100824083,
      label: 'f5_runs under 4.5',
      side: 'under',
      line: 4.5,
      odds: -110,
      market_prob: 0.55,
      model_prob: null,
      player_id: null,
      stat_type: null,
      market: 'f5_runs',
    },
  ],
};
```

- [ ] **Step 3: Add Vitest** — install and wire the script.

```bash
cd ~/dev/budgerr-web && npm install -D vitest
```

Then add to `package.json` `"scripts"` (alongside `dev`/`build`/`start`/`lint`):

```json
    "test": "vitest run"
```

- [ ] **Step 4: Write `vitest.config.ts`** (node env — pure logic, no DOM):

```ts
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: { environment: 'node', include: ['lib/**/*.test.ts'] },
});
```

- [ ] **Step 5: Write the failing tests `lib/builderParlays.test.ts`**

```ts
import { describe, expect, it } from 'vitest';

import {
  builderConstructionToBetInput,
  hasTeamLeg,
  legDisplay,
  playerNameFromLabel,
  selectLatestRun,
} from './builderParlays';
import { builderTeamConstruction } from './__fixtures__/builderTeamConstruction';
import { PlaystatBuilderConstruction } from './playstat';

const GAMES = new Map([
  [100823110, { game_id: 100823110, sport: 'MLB', date: '2026-07-22', home_team_id: 1, home_team_name: 'Red Sox', away_team_id: 2, away_team_name: 'Yankees', status: null }],
  [100824083, { game_id: 100824083, sport: 'MLB', date: '2026-07-22', home_team_id: 3, home_team_name: 'Royals', away_team_id: 4, away_team_name: 'Guardians', status: null }],
]);

function playerConstruction(id: number, date: string, jointProb: number): PlaystatBuilderConstruction {
  return {
    parlay_id: id,
    created_at: `${date} 13:02:10.000000-04:00`,
    target_payout: 2.0,
    joint_prob: jointProb,
    combined_odds: 2.01,
    n_legs: 1,
    legs: [
      { kind: 'player', game_id: 100823110, label: "Ke'Bryan Hayes runs under 0.5", side: 'under', line: 0.5, odds: -147, market_prob: 0.66, model_prob: 0.69, player_id: 100663647, stat_type: 'runs', market: null },
    ],
  };
}

describe('playerNameFromLabel', () => {
  it('strips the "{stat} {side} {line}" suffix', () => {
    expect(playerNameFromLabel({ label: "Ke'Bryan Hayes runs under 0.5", stat_type: 'runs', side: 'under', line: 0.5 })).toBe("Ke'Bryan Hayes");
  });
  it('falls back to the raw label when the suffix does not match', () => {
    expect(playerNameFromLabel({ label: 'Weird Label', stat_type: 'runs', side: 'under', line: 0.5 })).toBe('Weird Label');
  });
});

describe('selectLatestRun', () => {
  it('keeps only the most-recent date and sorts by joint_prob desc, capped at n', () => {
    const older = playerConstruction(1, '2026-07-21', 0.9);
    const newA = playerConstruction(2, '2026-07-22', 0.5);
    const newB = playerConstruction(3, '2026-07-22', 0.8);
    const result = selectLatestRun([older, newA, newB], 4);
    expect(result.map((c) => c.parlay_id)).toEqual([3, 2]);
  });
  it('returns [] for empty input', () => {
    expect(selectLatestRun([], 4)).toEqual([]);
  });
});

describe('legDisplay', () => {
  it('renders a team leg with the resolved matchup and market label', () => {
    expect(legDisplay(builderTeamConstruction.legs[0], GAMES)).toBe('Yankees @ Red Sox — NRFI under 0.5');
  });
  it('renders a team leg without matchup when the game is missing', () => {
    expect(legDisplay(builderTeamConstruction.legs[0], new Map())).toBe('NRFI under 0.5');
  });
});

describe('builderConstructionToBetInput', () => {
  it('maps a player leg to a settleable BetLegInput and sets placed_at from the game date', () => {
    const bet = builderConstructionToBetInput(playerConstruction(1, '2026-07-22', 0.8), GAMES, 10);
    expect(bet.legs).toEqual([{ player_name: "Ke'Bryan Hayes", stat_type: 'runs', line_value: 0.5, side: 'under', odds: -147 }]);
    expect(bet.placed_at).toBe('2026-07-22T12:00:00Z');
    expect(bet.potential_payout).toBeCloseTo(20.1);
    expect(bet.is_paper).toBe(true);
  });
  it('maps team legs with market in stat_type and matchup in player_name (log-only, no game_id/market)', () => {
    const bet = builderConstructionToBetInput(builderTeamConstruction, GAMES, 10);
    expect(bet.bet_type).toBe('parlay');
    expect(bet.legs[0]).toEqual({ player_name: 'Yankees @ Red Sox · NRFI', stat_type: 'first_inning_runs', line_value: 0.5, side: 'under', odds: -120 });
    expect(bet.legs[1].stat_type).toBe('f5_runs');
    expect(bet.legs[0]).not.toHaveProperty('game_id');
    expect(hasTeamLeg(builderTeamConstruction)).toBe(true);
  });
});
```

- [ ] **Step 6: Run tests to verify they fail** (module/functions exist so this mostly guards logic; run before wiring the UI):

Run: `cd ~/dev/budgerr-web && npm run test`
Expected: PASS (the implementation in Step 1 satisfies these). If any FAIL, fix `builderParlays.ts` — do not weaken the tests.

- [ ] **Step 7: Typecheck + build**

Run: `cd ~/dev/budgerr-web && npx tsc --noEmit && npm run build`
Expected: no errors (test files are excluded from the Next build; confirm the build passes).

- [ ] **Step 8: Commit**

```bash
cd ~/dev/budgerr-web
git add lib/builderParlays.ts lib/__fixtures__/builderTeamConstruction.ts lib/builderParlays.test.ts vitest.config.ts package.json package-lock.json
git commit -m "feat(web): builder parlay pure module + fixture + vitest tests

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: BuilderParlayCard component (budgerr-web)

**Files:**
- Create: `~/dev/budgerr-web/components/tonight/BuilderParlayCard.tsx`

**Interfaces:**
- Consumes: `PlaystatBuilderConstruction`, `PlaystatGame` (Task 1), `builderConstructionToBetInput`, `legDisplay`, `hasTeamLeg` (Task 2), `quarterKelly` (`lib/kelly.ts`), `useCreateBet` (`lib/queries.ts`).
- Produces: `BuilderParlayCard` (default: named export) taking `{ construction, gamesById, remainingBudget? }`.

- [ ] **Step 1: Write the component** (mirrors `ParlayCard`; stake input + ¼-Kelly; team note; index-based leg keys because team legs have null `player_id`):

```tsx
'use client';

import { useState } from 'react';

import { quarterKelly } from '@/lib/kelly';
import { PlaystatBuilderConstruction, PlaystatGame } from '@/lib/playstat';
import { builderConstructionToBetInput, hasTeamLeg, legDisplay } from '@/lib/builderParlays';
import { useCreateBet } from '@/lib/queries';

const DEFAULT_STAKE = 10;

export function BuilderParlayCard({
  construction,
  gamesById,
  remainingBudget,
}: {
  construction: PlaystatBuilderConstruction;
  gamesById: Map<number, PlaystatGame>;
  remainingBudget?: number;
}) {
  const createBet = useCreateBet();
  const [stake, setStake] = useState(String(DEFAULT_STAKE));
  const [logged, setLogged] = useState(false);

  const kelly =
    remainingBudget !== undefined
      ? quarterKelly(construction.combined_odds, construction.joint_prob, remainingBudget)
      : null;
  const showKelly = kelly !== null && kelly.f > 0 && (remainingBudget ?? 0) > 0;

  const stakeNum = parseFloat(stake);
  const stakeValid = !Number.isNaN(stakeNum) && stakeNum > 0;
  const teamNote = hasTeamLeg(construction);

  const logAsPaperBet = () => {
    if (!stakeValid) return;
    createBet.mutate(builderConstructionToBetInput(construction, gamesById, stakeNum), {
      onSuccess: () => setLogged(true),
    });
  };

  return (
    <div className="rounded-xl border border-emerald-200 dark:border-emerald-900 p-4">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium font-mono tabular-nums">
          {construction.n_legs}-leg · {construction.combined_odds.toFixed(2)}x
        </span>
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium px-2 py-1 rounded bg-surface text-muted whitespace-nowrap font-mono tabular-nums">
            {construction.target_payout.toFixed(1)}x
          </span>
          <span className="text-xs font-medium px-2 py-1 rounded bg-emerald-50 dark:bg-emerald-950 text-emerald-600 dark:text-emerald-400 whitespace-nowrap font-mono tabular-nums">
            {Math.round(construction.joint_prob * 100)}% to hit
          </span>
        </div>
      </div>
      <div className="mt-2 space-y-1">
        {construction.legs.map((leg, i) => (
          <p key={i} className="text-xs text-muted truncate">
            {legDisplay(leg, gamesById)}{' '}
            <span className="text-accent font-mono tabular-nums">
              ({leg.odds > 0 ? '+' : ''}
              {leg.odds})
            </span>
          </p>
        ))}
      </div>

      {teamNote && (
        <p className="mt-2 text-xs text-muted italic">Team markets log but don&apos;t auto-settle yet.</p>
      )}

      {showKelly && kelly && (
        <p className="mt-2 text-xs text-muted">
          ¼-Kelly: ${kelly.suggested.toFixed(2)} of ${(remainingBudget ?? 0).toFixed(2)} left
        </p>
      )}

      <div className="mt-3 flex items-center gap-2">
        {logged ? (
          <span className="text-xs font-medium text-emerald-600 dark:text-emerald-400">Logged as paper ✓</span>
        ) : (
          <>
            <span className="text-xs text-muted">$</span>
            <input
              className="w-16 rounded-lg border border-border bg-transparent px-2 py-1 text-xs font-mono tabular-nums focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
              value={stake}
              onChange={(e) => setStake(e.target.value)}
              disabled={createBet.isPending}
              aria-label="Hypothetical stake"
              placeholder={showKelly && kelly ? kelly.suggested.toFixed(2) : undefined}
            />
            <button
              className="text-xs px-3 py-1.5 rounded-lg border border-border hover:bg-surface transition-colors duration-150 disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
              onClick={logAsPaperBet}
              disabled={createBet.isPending || !stakeValid}
            >
              {createBet.isPending ? 'Logging...' : 'Log as paper bet'}
            </button>
          </>
        )}
      </div>
      {createBet.isError && (
        <p className="mt-1 text-xs text-red-600 dark:text-red-400">
          {String(createBet.error?.message ?? createBet.error)}
        </p>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Typecheck**

Run: `cd ~/dev/budgerr-web && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
cd ~/dev/budgerr-web
git add components/tonight/BuilderParlayCard.tsx
git commit -m "feat(web): BuilderParlayCard with tier badge, team note, inline logging

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Tonight view rewiring + dev demo path (budgerr-web)

**Files:**
- Modify: `~/dev/budgerr-web/app/tonight/page.tsx`

**Interfaces:**
- Consumes: `usePlaystatBuilderParlays` (Task 1), `selectLatestRun` (Task 2), `BuilderParlayCard` (Task 3), `builderTeamConstruction` fixture (Task 2), existing `usePlaystatSlate` / `usePlaystatEdges` / `usePlaystatGamePredictions`.

- [ ] **Step 1: Rewire `app/tonight/page.tsx`.** Replace the model-parlay imports/hooks/section with the builder source. Specifically:

Change the imports block to drop `ParlayCard`, `usePlaystatParlays`, `usePlaystatAllEdges` and add the builder pieces + `useEffect`/`useState`:

```tsx
'use client';

import { useEffect, useMemo, useState } from 'react';

import { BudgetPeriodCard } from '@/components/budget/BudgetPeriodCard';
import { GameCard } from '@/components/tonight/GameCard';
import { BuilderParlayCard } from '@/components/tonight/BuilderParlayCard';
import { PlaystatEdge, PlaystatGame, PlaystatGamePrediction } from '@/lib/playstat';
import { selectLatestRun } from '@/lib/builderParlays';
import { builderTeamConstruction } from '@/lib/__fixtures__/builderTeamConstruction';
import {
  currentMonth,
  useBudgetPeriods,
  useCategories,
  usePlaystatBuilderParlays,
  usePlaystatEdges,
  usePlaystatGamePredictions,
  usePlaystatSlate,
} from '@/lib/queries';
```

Replace the `const allEdges = ...` and `const parlays = usePlaystatParlays();` lines with:

```tsx
  const builderParlays = usePlaystatBuilderParlays();

  // Dev-only: `?demo=builder-team` injects a fixture team construction so the
  // team branch can be driven in the browser (real saved data is player-only).
  const [demoTeam, setDemoTeam] = useState(false);
  useEffect(() => {
    if (
      process.env.NODE_ENV !== 'production' &&
      new URLSearchParams(window.location.search).get('demo') === 'builder-team'
    ) {
      setDemoTeam(true);
    }
  }, []);
```

Add a `gamesById` memo next to the existing `edgesByGame` memo:

```tsx
  const gamesById = useMemo(() => {
    const map = new Map<number, PlaystatGame>();
    for (const game of slate.data?.games ?? []) map.set(game.game_id, game);
    return map;
  }, [slate.data]);

  const builderConstructions = useMemo(() => {
    const base = selectLatestRun(builderParlays.data ?? [], 4);
    return demoTeam ? [builderTeamConstruction, ...base] : base;
  }, [builderParlays.data, demoTeam]);
```

Replace the entire "Recommended parlays" JSX block (the `<p>Recommended parlays</p>` through its `)}`) with:

```tsx
      <p className="text-sm font-medium text-muted">Low-risk builder parlays</p>
      {builderConstructions.length === 0 ? (
        <p className="text-sm text-muted">
          No builder parlays yet — Playstat precomputes the low-risk parlay each evening.
        </p>
      ) : (
        <div className="space-y-3">
          {builderConstructions.map((construction) => (
            <BuilderParlayCard
              key={construction.parlay_id}
              construction={construction}
              gamesById={gamesById}
              remainingBudget={bettingPeriod?.remaining}
            />
          ))}
        </div>
      )}
```

- [ ] **Step 2: Typecheck + build**

Run: `cd ~/dev/budgerr-web && npx tsc --noEmit && npm run build`
Expected: no errors, no unused-import warnings for the removed hooks.

- [ ] **Step 3: `graphify update`**

Run: `cd ~/dev/budgerr-web && graphify update .`

- [ ] **Step 4: Commit**

```bash
cd ~/dev/budgerr-web
git add app/tonight/page.tsx graphify-out
git commit -m "feat(web): Tonight shows builder parlays, retires model parlay UI

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 5: Architect browser verification (NOT a worker step — architect runs this).**
Real player-leg flow: `preview_start` "budgerr-web", load `/tonight`, confirm the "Low-risk builder parlays" section renders cards (player legs today) with tier badges and `% to hit`, the model section is gone, and logging a card creates a paper bet in the DB (then delete the test row). Team branch: load `/tonight?demo=builder-team`, confirm the fixture card shows "Yankees @ Red Sox — NRFI under 0.5", the "won't auto-settle yet" note, and logs a pending paper bet. Confirm `model_prob` appears nowhere.

---

## Task 5: Builder types, API, hook, and pure module (BudgerrApp)

**Files:**
- Modify: `~/dev/BudgerrApp/lib/playstat.ts` (add types after `PlaystatParlayRecommendation` ~L68; add `listBuilder` in `parlays` ~L115-118)
- Modify: `~/dev/BudgerrApp/lib/queries.ts` (add `usePlaystatBuilderParlays`)
- Create: `~/dev/BudgerrApp/lib/builderParlays.ts`

**Interfaces:**
- Produces: the same `PlaystatBuilderLeg`/`PlaystatBuilderConstruction` types, `playstatApi.parlays.listBuilder`, `usePlaystatBuilderParlays`, and the pure module (identical to web Task 2's `builderParlays.ts`).

- [ ] **Step 1: Add the builder types to `lib/playstat.ts`** immediately after the `PlaystatParlayRecommendation` interface (do not modify existing types):

```ts
export interface PlaystatBuilderLegBase {
  game_id: number;
  label: string;
  side: 'over' | 'under';
  line: number;
  odds: number;
  market_prob: number;
  model_prob: number | null; // CONTEXT ONLY — never surfaced as edge/value
}
export interface PlaystatBuilderPlayerLeg extends PlaystatBuilderLegBase {
  kind: 'player';
  player_id: number;
  stat_type: string;
  market: null;
}
export interface PlaystatBuilderTeamLeg extends PlaystatBuilderLegBase {
  kind: 'team';
  player_id: null;
  stat_type: null;
  market: 'first_inning_runs' | 'f5_runs';
}
export type PlaystatBuilderLeg = PlaystatBuilderPlayerLeg | PlaystatBuilderTeamLeg;

export interface PlaystatBuilderConstruction {
  parlay_id: number;
  created_at: string;
  target_payout: number; // 1.4 | 2.0
  joint_prob: number; // de-vigged MARKET joint prob — ranking/label basis
  combined_odds: number;
  n_legs: number;
  legs: PlaystatBuilderLeg[];
}
```

- [ ] **Step 2: Add `listBuilder`** inside the existing `parlays:` object in `playstatApi` (keep `list` untouched):

```ts
  parlays: {
    list: async (limit = 3): Promise<PlaystatParlayRecommendation[]> =>
      fetchJson<PlaystatParlayRecommendation[]>(`/parlay-recommendations?limit=${limit}`),
    listBuilder: async (limit = 10): Promise<PlaystatBuilderConstruction[]> =>
      fetchJson<PlaystatBuilderConstruction[]>(`/parlay-builder/saved?limit=${limit}`),
  },
```

- [ ] **Step 3: Add the hook to `lib/queries.ts`** (React Query is the same lib):

```ts
export function usePlaystatBuilderParlays() {
  return useQuery({
    queryKey: ['playstat-builder-parlays'],
    queryFn: () => playstatApi.parlays.listBuilder(),
  });
}
```

- [ ] **Step 4: Create `lib/builderParlays.ts`** (pure, RN-safe; same relative imports `./api`, `./playstat`). Do NOT create a fixture or test file here (no runner on mobile; the logic is unit-tested on web):

```ts
import { BetInput, BetLegInput } from './api';
import { PlaystatBuilderConstruction, PlaystatBuilderLeg, PlaystatGame } from './playstat';

const MARKET_LABEL: Record<'first_inning_runs' | 'f5_runs', string> = {
  first_inning_runs: 'NRFI',
  f5_runs: 'F5',
};

export function marketLabel(market: 'first_inning_runs' | 'f5_runs'): string {
  return MARKET_LABEL[market];
}

/** Player-leg labels are "{name} {stat_type} {side} {line}"; strip the known
 *  suffix to recover the name. Falls back to the raw label if it doesn't match. */
export function playerNameFromLabel(leg: {
  label: string;
  stat_type: string;
  side: string;
  line: number;
}): string {
  const suffix = ` ${leg.stat_type} ${leg.side} ${leg.line}`;
  return leg.label.endsWith(suffix) ? leg.label.slice(0, -suffix.length) : leg.label;
}

/** "Away @ Home" for a team leg, or undefined if the game isn't in the map. */
export function matchup(gameId: number, gamesById: Map<number, PlaystatGame>): string | undefined {
  const g = gamesById.get(gameId);
  return g ? `${g.away_team_name} @ ${g.home_team_name}` : undefined;
}

/** One-line display string for a leg (used by BuilderParlayCard). */
export function legDisplay(leg: PlaystatBuilderLeg, gamesById: Map<number, PlaystatGame>): string {
  if (leg.kind === 'team') {
    const m = matchup(leg.game_id, gamesById);
    return `${m ? `${m} — ` : ''}${marketLabel(leg.market)} ${leg.side} ${leg.line}`;
  }
  return `${playerNameFromLabel(leg)} ${leg.side} ${leg.stat_type}`;
}

export function hasTeamLeg(construction: PlaystatBuilderConstruction): boolean {
  return construction.legs.some((leg) => leg.kind === 'team');
}

/** Most-recent nightly run (by created_at DATE), top N by joint_prob desc. */
export function selectLatestRun(
  constructions: PlaystatBuilderConstruction[],
  n = 4
): PlaystatBuilderConstruction[] {
  if (constructions.length === 0) return [];
  const dates = constructions.map((c) => c.created_at.slice(0, 10)).sort();
  const latestDate = dates[dates.length - 1];
  return constructions
    .filter((c) => c.created_at.slice(0, 10) === latestDate)
    .sort((a, b) => b.joint_prob - a.joint_prob)
    .slice(0, n);
}

/** Paper-bet payload for a construction. Team legs log but cannot auto-settle
 *  (BetLegInput has no game_id/market) — that's intentional and documented. */
export function builderConstructionToBetInput(
  construction: PlaystatBuilderConstruction,
  gamesById: Map<number, PlaystatGame>,
  stake: number
): BetInput {
  const legs: BetLegInput[] = construction.legs.map((leg) => {
    if (leg.kind === 'team') {
      const m = matchup(leg.game_id, gamesById);
      return {
        player_name: `${m ?? `Game ${leg.game_id}`} · ${marketLabel(leg.market)}`,
        stat_type: leg.market,
        line_value: leg.line,
        side: leg.side,
        odds: leg.odds,
      };
    }
    return {
      player_name: playerNameFromLabel(leg),
      stat_type: leg.stat_type,
      line_value: leg.line,
      side: leg.side,
      odds: leg.odds,
    };
  });
  const gameDate = construction.legs
    .map((leg) => gamesById.get(leg.game_id)?.date)
    .find((d): d is string => !!d);
  return {
    sportsbook: 'paper',
    bet_type: construction.legs.length > 1 ? 'parlay' : 'single',
    stake,
    potential_payout: stake * construction.combined_odds,
    placed_at: gameDate ? `${gameDate}T12:00:00Z` : undefined,
    is_paper: true,
    legs,
  };
}
```

- [ ] **Step 5: Typecheck**

Run: `cd ~/dev/BudgerrApp && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
cd ~/dev/BudgerrApp
git add lib/playstat.ts lib/queries.ts lib/builderParlays.ts
git commit -m "feat(mobile): builder parlay types, API, hook, pure module

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: BuilderParlayCard component (BudgerrApp)

**Files:**
- Create: `~/dev/BudgerrApp/components/tonight/BuilderParlayCard.tsx`

**Interfaces:**
- Consumes: same as web Task 3, plus RN primitives + `Themed` `Text`, `Colors`, `cardShadow`, `useColorScheme` (see existing `components/tonight/ParlayCard.tsx`).
- Produces: `BuilderParlayCard` taking `{ construction, gamesById, remainingBudget? }`. Uses fixed `PAPER_STAKE = 10` (mobile `ParlayCard` has no stake input).

- [ ] **Step 1: Write the RN component** (mirror `ParlayCard.tsx`, add tier badge + team note, index-based leg keys, `legDisplay` for text):

```tsx
import { useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, View } from 'react-native';

import { Text } from '@/components/Themed';
import Colors from '@/constants/Colors';
import { cardShadow } from '@/constants/Shadow';
import { useColorScheme } from '@/components/useColorScheme';
import { quarterKelly } from '@/lib/kelly';
import { PlaystatBuilderConstruction, PlaystatGame } from '@/lib/playstat';
import { builderConstructionToBetInput, hasTeamLeg, legDisplay } from '@/lib/builderParlays';
import { useCreateBet } from '@/lib/queries';

const PAPER_STAKE = 10;

export function BuilderParlayCard({
  construction,
  gamesById,
  remainingBudget = 0,
}: {
  construction: PlaystatBuilderConstruction;
  gamesById: Map<number, PlaystatGame>;
  remainingBudget?: number;
}) {
  const theme = Colors[useColorScheme()];
  const createBet = useCreateBet();
  const [logged, setLogged] = useState(false);

  const { suggested: kellyStake } = quarterKelly(
    construction.combined_odds,
    construction.joint_prob,
    remainingBudget
  );
  const teamNote = hasTeamLeg(construction);

  const logAsPaperBet = () => {
    if (createBet.isPending || logged) return;
    createBet.mutate(builderConstructionToBetInput(construction, gamesById, PAPER_STAKE), {
      onSuccess: () => setLogged(true),
    });
  };

  return (
    <View style={[styles.card, { backgroundColor: theme.card, borderColor: theme.edgeBorder }]}>
      <View style={styles.headerRow}>
        <Text style={styles.title}>
          {construction.n_legs}-leg · {construction.combined_odds.toFixed(2)}x
        </Text>
        <View style={styles.badges}>
          <View style={[styles.badge, { backgroundColor: theme.edgeBg }]}>
            <Text style={[styles.badgeText, { color: theme.textSecondary }]}>
              {construction.target_payout.toFixed(1)}x
            </Text>
          </View>
          <View style={[styles.badge, { backgroundColor: theme.edgeBg }]}>
            <Text style={[styles.badgeText, { color: theme.edge }]}>
              {Math.round(construction.joint_prob * 100)}% to hit
            </Text>
          </View>
        </View>
      </View>
      <View style={styles.legsList}>
        {construction.legs.map((leg, i) => (
          <Text key={i} style={[styles.legRow, { color: theme.textSecondary }]} numberOfLines={1}>
            {legDisplay(leg, gamesById)}{' '}
            <Text style={{ color: theme.tint }}>
              ({leg.odds > 0 ? '+' : ''}
              {leg.odds})
            </Text>
          </Text>
        ))}
      </View>
      {teamNote && (
        <Text style={[styles.note, { color: theme.textMuted }]}>
          Team markets log but don&apos;t auto-settle yet.
        </Text>
      )}
      {kellyStake > 0 && (
        <View style={styles.kellyRow}>
          <Text style={[styles.kellyText, { color: theme.text }]}>
            ¼-Kelly stake: ${kellyStake.toFixed(2)}
          </Text>
          <Text style={[styles.kellyCaption, { color: theme.textMuted }]}>
            Guidance only — sizing depends on model calibration, not a bet recommendation.
          </Text>
        </View>
      )}
      <Pressable
        style={[
          styles.paperButton,
          { borderColor: theme.border },
          logged && { backgroundColor: theme.successBg, borderColor: theme.successBg },
        ]}
        onPress={logAsPaperBet}
        disabled={createBet.isPending || logged}
      >
        {createBet.isPending ? (
          <ActivityIndicator size="small" color={theme.textSecondary} />
        ) : (
          <Text style={[styles.paperButtonText, { color: logged ? theme.success : theme.textSecondary }]}>
            {logged ? 'Logged ✓' : 'Log as paper bet'}
          </Text>
        )}
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  card: { borderRadius: 12, borderWidth: 0.5, padding: 14, marginBottom: 10, ...cardShadow },
  headerRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8 },
  title: { fontSize: 14, fontWeight: '500', flex: 1 },
  badges: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  badge: { paddingHorizontal: 8, paddingVertical: 3, borderRadius: 6 },
  badgeText: { fontSize: 11, fontWeight: '500' },
  legsList: { marginTop: 8, gap: 4 },
  legRow: { fontSize: 12 },
  note: { marginTop: 8, fontSize: 11, fontStyle: 'italic' },
  kellyRow: { marginTop: 8, gap: 2 },
  kellyText: { fontSize: 12, fontWeight: '500' },
  kellyCaption: { fontSize: 10 },
  paperButton: { marginTop: 10, borderWidth: 0.5, borderRadius: 8, paddingVertical: 8, alignItems: 'center' },
  paperButtonText: { fontSize: 12, fontWeight: '500' },
});
```

- [ ] **Step 2: Typecheck**

Run: `cd ~/dev/BudgerrApp && npx tsc --noEmit`
Expected: no errors. (If `theme.edge`/`edgeBg`/`edgeBorder`/`successBg` are missing on the theme type, they already exist — they're used by the current `ParlayCard`.)

- [ ] **Step 3: Commit**

```bash
cd ~/dev/BudgerrApp
git add components/tonight/BuilderParlayCard.tsx
git commit -m "feat(mobile): BuilderParlayCard mirroring web (tier badge, team note)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: Tonight screen rewiring (BudgerrApp)

**Files:**
- Modify: `~/dev/BudgerrApp/app/(tabs)/tonight.tsx`

**Interfaces:**
- Consumes: `usePlaystatBuilderParlays` (Task 5), `selectLatestRun` (Task 5), `BuilderParlayCard` (Task 6).

- [ ] **Step 1: Rewire `app/(tabs)/tonight.tsx`.** Drop `ParlayCard`, `usePlaystatAllEdges`, `usePlaystatParlays`; add `BuilderParlayCard`, `selectLatestRun`, `usePlaystatBuilderParlays`, `PlaystatGame`. Replace the `allEdges`/`parlays` hooks with:

```tsx
  const builderParlays = usePlaystatBuilderParlays();
```

Add a `gamesById` memo and the selected list next to `edgesByGame`:

```tsx
  const gamesById = useMemo(() => {
    const map = new Map<number, PlaystatGame>();
    for (const game of slate.data?.games ?? []) map.set(game.game_id, game);
    return map;
  }, [slate.data]);

  const builderConstructions = useMemo(
    () => selectLatestRun(builderParlays.data ?? [], 4),
    [builderParlays.data]
  );
```

Update `refetchAll` to call `builderParlays.refetch()` instead of `parlays.refetch()`. Replace the "Recommended parlays" section JSX with:

```tsx
      <Text style={[styles.sectionTitle, { color: theme.textSecondary }]}>Low-risk builder parlays</Text>
      {builderConstructions.length === 0 ? (
        <Text style={{ color: theme.textMuted, fontSize: 13 }}>
          No builder parlays yet — Playstat precomputes the low-risk parlay each evening.
        </Text>
      ) : (
        builderConstructions.map((construction) => (
          <BuilderParlayCard
            key={construction.parlay_id}
            construction={construction}
            gamesById={gamesById}
            remainingBudget={bettingPeriod?.remaining ?? 0}
          />
        ))
      )}
```

- [ ] **Step 2: Typecheck**

Run: `cd ~/dev/BudgerrApp && npx tsc --noEmit`
Expected: no errors, no unused imports.

- [ ] **Step 3: `graphify update`**

Run: `cd ~/dev/BudgerrApp && graphify update .`

- [ ] **Step 4: Commit**

```bash
cd ~/dev/BudgerrApp
git add "app/(tabs)/tonight.tsx" graphify-out
git commit -m "feat(mobile): Tonight shows builder parlays, retires model parlay UI

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final integration (architect)

- Web: `npm run build` + `npm run test` green; browser verification per Task 4 Step 5 (player flow + `?demo=builder-team`), test rows cleaned up.
- Mobile: `npx tsc --noEmit` green (static only — no simulator).
- Confirm the model `/parlay-recommendations` UI is gone from both Tonight views and `model_prob` appears nowhere.
- The auto-log launchd job and any README `docs/superpowers/plans` note are out of scope (spec §8 follow-ups); the spec + README §15 entry were committed with the design.
- Merge task branches per repo, then push each repo (architect only).
