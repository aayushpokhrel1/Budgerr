# Team-Market Tier (NRFI/F5) in Tonight — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface Playstat's now-populated team-market tier (NRFI/F5) in Budgerr's Tonight view as a separate, clearly-labeled higher-variance section, reusing the already-built team branch.

**Architecture:** Switch the single existing builder fetch to `?tier=all`, then partition the combined feed client-side by `hasTeamLeg` into the shipped low-risk section (player constructions) and a new "Team markets" section (team constructions). Each section keeps its own run-date game resolution and fully-past hiding. Team bets stay log-only. No backend, no changes to the pure `lib/builderParlays.ts` module.

**Tech Stack:** Next.js + React Query + Tailwind (`budgerr-web`); Expo/React Native + React Query (`BudgerrApp`). Spec: `docs/superpowers/specs/2026-07-28-team-market-tier-design.md`.

## Global Constraints

- **Frontend only.** No backend, migrations, launchd, or settlement-whitelist changes. Team-market settlement stays on HOLD.
- **`lib/builderParlays.ts` must stay byte-identical between `budgerr-web` and `BudgerrApp`.** It currently *is* identical and needs **no change** in this work — do not edit it.
- **`model_prob` must never appear in the UI.** Rank/label by `joint_prob`/`market_prob` only.
- **Team bets are log-only** — they log as paper bets and must not auto-settle (`BetLegInput` has no `game_id`/`market`; the existing helper already handles this).
- **Do not touch Playstat** (`~/dev/playstat` is read-only) or the `PlaystatParlay*` / `/parlay-recommendations` types (contract intact).
- **graphify:** orient with `graphify query "<question>"` before grepping raw source; `graphify update .` currently refuses in both frontend repos (known stale-graph tooling issue) — do not force it.
- **Commits:** commit on your branch, **never push**; end every commit message with:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- **Verified API facts (2026-07-28, do not re-derive):** `?tier=all` is a union feed — every construction is purely player or purely team (zero mixed-leg constructions), ordered `created_at` desc. Its player partition's latest run == `?tier=player`'s (top-4 `[181,182,183,184]`, 07-28); its team partition's latest run == `?tier=team`'s (top-4 `[166,161,167,162]`, 07-26). The latest team run is currently stale (07-26), so `isRunFullyPast` hides it and the team section shows its empty state until a fresh team run lands.

---

## File map

**`budgerr-web`:**
- Modify `lib/playstat.ts` — add `tier` arg to `parlays.listBuilder`.
- Modify `lib/queries.ts` — `usePlaystatBuilderParlays()` calls `listBuilder(100, 'all')`.
- Modify `components/tonight/BuilderParlayCard.tsx` — add `variant` prop.
- Modify `app/tonight/page.tsx` — partition, dual games resolution, team section, repoint the `?demo=builder-team` affordance.
- Add test in `lib/builderParlays.test.ts` (existing Vitest file) — partition-then-select regression lock.

**`BudgerrApp`:**
- Modify `lib/playstat.ts` — same `tier` arg.
- Modify `lib/queries.ts` — same hook switch.
- Modify `components/tonight/BuilderParlayCard.tsx` — add `variant` prop (RN styling).
- Modify `app/(tabs)/tonight.tsx` — partition, dual games resolution, team section (no demo affordance on mobile).

**Both:** `lib/builderParlays.ts` — **unchanged** (must stay byte-identical).

---

## Task 1 (budgerr-web): Data layer — `?tier=all` fetch

**Files:**
- Modify: `lib/playstat.ts:160-161`
- Modify: `lib/queries.ts:294-299`
- Test: `lib/builderParlays.test.ts` (existing)

**Interfaces:**
- Produces: `playstatApi.parlays.listBuilder(limit?: number, tier?: 'player' | 'team' | 'all')` and `usePlaystatBuilderParlays()` returning the combined `?tier=all` feed (`PlaystatBuilderConstruction[]`).
- Consumes: existing `hasTeamLeg`, `selectLatestRun` from `lib/builderParlays.ts`.

- [ ] **Step 1: Add a failing partition-then-select test**

Append to `lib/builderParlays.test.ts`. This locks the core client-side split behavior using only existing exports (no module change). It builds a `?tier=all`-shaped feed: two player constructions (latest date 2026-07-28) and two team constructions (latest date 2026-07-26), each purely one kind.

```ts
import { hasTeamLeg, selectLatestRun } from './builderParlays';
import type { PlaystatBuilderConstruction } from './playstat';

function playerCon(id: number, date: string, jp: number): PlaystatBuilderConstruction {
  return {
    parlay_id: id, created_at: `${date} 09:00:00-04:00`, target_payout: 1.4,
    joint_prob: jp, combined_odds: 1.4, n_legs: 1,
    legs: [{ kind: 'player', game_id: 1, label: 'A runs over 0.5', side: 'over', line: 0.5,
      odds: -120, market_prob: 0.9, model_prob: null, player_id: 7, stat_type: 'runs', market: null }],
  };
}
function teamCon(id: number, date: string, jp: number): PlaystatBuilderConstruction {
  return {
    parlay_id: id, created_at: `${date} 09:00:00-04:00`, target_payout: 2.0,
    joint_prob: jp, combined_odds: 2.7, n_legs: 1,
    legs: [{ kind: 'team', game_id: 2, label: 'first_inning_runs under 0.5', side: 'under', line: 0.5,
      odds: -150, market_prob: 0.57, model_prob: null, player_id: null, stat_type: null, market: 'first_inning_runs' }],
  };
}

describe('tier=all client-side partition', () => {
  const feed = [
    playerCon(181, '2026-07-28', 0.92), playerCon(182, '2026-07-28', 0.90),
    teamCon(166, '2026-07-26', 0.32), teamCon(161, '2026-07-26', 0.31),
  ];

  it('splits the feed into player-only and team-only partitions', () => {
    const player = feed.filter((c) => !hasTeamLeg(c));
    const team = feed.filter((c) => hasTeamLeg(c));
    expect(player.map((c) => c.parlay_id)).toEqual([181, 182]);
    expect(team.map((c) => c.parlay_id)).toEqual([166, 161]);
  });

  it('selectLatestRun on each partition picks that partition\'s own latest run', () => {
    const player = feed.filter((c) => !hasTeamLeg(c));
    const team = feed.filter((c) => hasTeamLeg(c));
    expect(selectLatestRun(player, 4).map((c) => c.parlay_id)).toEqual([181, 182]);
    expect(selectLatestRun(team, 4).map((c) => c.parlay_id)).toEqual([166, 161]);
  });
});
```

- [ ] **Step 2: Run the test to verify it passes** (it uses only existing exports — this is a regression lock, expected GREEN immediately)

Run: `cd ~/dev/budgerr-web && npx vitest run lib/builderParlays.test.ts`
Expected: PASS (existing tests + 2 new). If the two new tests fail, stop — an existing helper regressed.

- [ ] **Step 3: Add the `tier` argument to `listBuilder`**

In `lib/playstat.ts`, replace lines 160-161:

```ts
    listBuilder: async (
      limit = 10,
      tier?: 'player' | 'team' | 'all'
    ): Promise<PlaystatBuilderConstruction[]> =>
      fetchJson<PlaystatBuilderConstruction[]>(
        `/parlay-builder/saved?limit=${limit}${tier ? `&tier=${tier}` : ''}`
      ),
```

- [ ] **Step 4: Switch the hook to the combined feed**

In `lib/queries.ts`, replace the body of `usePlaystatBuilderParlays` (lines 294-299):

```ts
export function usePlaystatBuilderParlays() {
  return useQuery({
    queryKey: ['playstat-builder-parlays', 'all'],
    queryFn: () => playstatApi.parlays.listBuilder(100, 'all'),
  });
}
```

- [ ] **Step 5: Typecheck + build**

Run: `cd ~/dev/budgerr-web && npm run build`
Expected: build succeeds (the page still compiles; behavior change is verified in Task 3).

- [ ] **Step 6: Commit**

```bash
cd ~/dev/budgerr-web && git add lib/playstat.ts lib/queries.ts lib/builderParlays.test.ts
git commit -m "feat: fetch builder parlays via ?tier=all (combined feed)

Add optional tier arg to listBuilder; switch usePlaystatBuilderParlays to
listBuilder(100,'all') so one fetch feeds both the low-risk (player) and
team-market (team) sections. Regression test locks the client-side partition.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2 (budgerr-web): `BuilderParlayCard` variance variant

**Files:**
- Modify: `components/tonight/BuilderParlayCard.tsx`

**Interfaces:**
- Produces: `BuilderParlayCard` accepts `variant?: 'lowrisk' | 'variance'` (default `'lowrisk'`). `'lowrisk'` is the existing emerald look, unchanged. `'variance'` uses an amber border and a muted (non-green) joint-prob badge.
- Consumes: nothing new.

- [ ] **Step 1: Add the `variant` prop to the signature**

In `components/tonight/BuilderParlayCard.tsx`, change the props block (lines 12-20) to add `variant`:

```tsx
export function BuilderParlayCard({
  construction,
  gamesById,
  remainingBudget,
  variant = 'lowrisk',
}: {
  construction: PlaystatBuilderConstruction;
  gamesById: Map<number, PlaystatGame>;
  remainingBudget?: number;
  variant?: 'lowrisk' | 'variance';
}) {
```

- [ ] **Step 2: Branch the border color**

Replace the outer `<div>` (line 43) with a variant-aware border:

```tsx
    <div
      className={`rounded-xl border p-4 ${
        variant === 'variance'
          ? 'border-amber-300 dark:border-amber-900'
          : 'border-emerald-200 dark:border-emerald-900'
      }`}
    >
```

- [ ] **Step 3: Branch the joint-prob badge (drop green for variance)**

Replace the joint-prob badge span (lines 52-54) with a variant-aware version:

```tsx
          <span
            className={`text-xs font-medium px-2 py-1 rounded whitespace-nowrap font-mono tabular-nums ${
              variant === 'variance'
                ? 'bg-surface text-muted'
                : 'bg-emerald-50 dark:bg-emerald-950 text-emerald-600 dark:text-emerald-400'
            }`}
          >
            {Math.round(construction.joint_prob * 100)}% to hit
          </span>
```

- [ ] **Step 4: Build**

Run: `cd ~/dev/budgerr-web && npm run build`
Expected: build succeeds.

- [ ] **Step 5: Commit**

```bash
cd ~/dev/budgerr-web && git add components/tonight/BuilderParlayCard.tsx
git commit -m "feat: add variance variant to BuilderParlayCard

variant='variance' uses an amber border and a muted joint-prob badge (no green)
so higher-variance team cards don't read as 'safe'. Default 'lowrisk' unchanged.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3 (budgerr-web): Tonight page — team section + partition + demo repoint

**Files:**
- Modify: `app/tonight/page.tsx`

**Interfaces:**
- Consumes: `usePlaystatBuilderParlays()` (now `?tier=all`), `hasTeamLeg`, `selectLatestRun`, `runDate`, `isRunFullyPast`, `usePlaystatGames`, `BuilderParlayCard` (with `variant`).

- [ ] **Step 1: Update imports**

In `app/tonight/page.tsx`: add `hasTeamLeg` to the `builderParlays` import (line 9), and **remove** the fixture import (line 10, `builderTeamConstruction`). Result:

```tsx
import { hasTeamLeg, isRunFullyPast, runDate, selectLatestRun } from '@/lib/builderParlays';
```
(delete the `import { builderTeamConstruction } ...` line entirely.)

- [ ] **Step 2: Partition the feed + resolve both runs' games**

Replace the builder block (lines 64-96, from `const builderParlays = ...` through the `builderConstructions` useMemo) with:

```tsx
  const builderParlays = usePlaystatBuilderParlays(); // ?tier=all combined feed

  // tier=all constructions are never mixed — partition cleanly by hasTeamLeg.
  const playerCons = useMemo(
    () => (builderParlays.data ?? []).filter((c) => !hasTeamLeg(c)),
    [builderParlays.data]
  );
  const teamCons = useMemo(
    () => (builderParlays.data ?? []).filter((c) => hasTeamLeg(c)),
    [builderParlays.data]
  );

  const latestRun = useMemo(() => selectLatestRun(playerCons, 4), [playerCons]);
  const latestTeamRun = useMemo(() => selectLatestRun(teamCons, 4), [teamCons]);

  // Each section resolves games from its OWN run's date (player run and team run
  // are typically different days), so matchups + settlement dates are correct.
  const builderGames = usePlaystatGames(runDate(latestRun));
  const builderGamesById = useMemo(() => {
    const map = new Map<number, PlaystatGame>();
    for (const game of builderGames.data ?? []) map.set(game.game_id, game);
    return map;
  }, [builderGames.data]);

  const teamGames = usePlaystatGames(runDate(latestTeamRun));
  const teamGamesById = useMemo(() => {
    const map = new Map<number, PlaystatGame>();
    for (const game of teamGames.data ?? []) map.set(game.game_id, game);
    return map;
  }, [teamGames.data]);

  // Dev-only: `?demo=builder-team` reveals the real latest team run even when it
  // is fully past, so the real team card can be driven in the browser.
  const [revealTeam, setRevealTeam] = useState(false);
  useEffect(() => {
    if (
      process.env.NODE_ENV !== 'production' &&
      new URLSearchParams(window.location.search).get('demo') === 'builder-team'
    ) {
      setRevealTeam(true);
    }
  }, []);

  const builderConstructions = useMemo(() => {
    if (latestRun.length === 0) return [];
    if (!builderGames.data) return []; // wait for the run's games before deciding
    if (isRunFullyPast(latestRun, builderGamesById)) return []; // hide a stale past run
    return latestRun;
  }, [latestRun, builderGames.data, builderGamesById]);

  const teamConstructions = useMemo(() => {
    if (latestTeamRun.length === 0) return [];
    if (!teamGames.data) return [];
    if (!revealTeam && isRunFullyPast(latestTeamRun, teamGamesById)) return [];
    return latestTeamRun;
  }, [latestTeamRun, teamGames.data, teamGamesById, revealTeam]);
```

- [ ] **Step 3: Add the team-market section JSX**

Immediately after the low-risk section block (after its closing `)}` on line ~128, before the slate-heading `<p>` on line ~130), insert:

```tsx
      <div>
        <p className="text-sm font-medium text-muted">
          Team markets (NRFI/F5) — higher variance
        </p>
        <p className="text-xs text-muted">
          ~30–50% to hit · logs as paper, won&apos;t auto-settle.
        </p>
      </div>
      {teamConstructions.length === 0 ? (
        <p className="text-sm text-muted">
          No team-market parlays in tonight&apos;s build — the team tier is often empty.
        </p>
      ) : (
        <div className="space-y-3">
          {teamConstructions.map((construction) => (
            <BuilderParlayCard
              key={construction.parlay_id}
              construction={construction}
              gamesById={teamGamesById}
              remainingBudget={bettingPeriod?.remaining}
              variant="variance"
            />
          ))}
        </div>
      )}
```

- [ ] **Step 4: Build**

Run: `cd ~/dev/budgerr-web && npm run build`
Expected: build succeeds, no unused-import warnings (fixture import removed).

- [ ] **Step 5: Commit**

```bash
cd ~/dev/budgerr-web && git add app/tonight/page.tsx
git commit -m "feat: add higher-variance team-market section to Tonight

Partition the ?tier=all feed by hasTeamLeg: player -> low-risk section
(unchanged), team -> new 'Team markets (NRFI/F5) - higher variance' section with
its own run-date game resolution and fully-past hiding. Repoint ?demo=builder-team
to reveal the real (possibly-past) team run for browser verification.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4 (BudgerrApp): Data layer — `?tier=all` fetch

**Files:**
- Modify: `lib/playstat.ts:155-156`
- Modify: `lib/queries.ts:88-92`

**Interfaces:**
- Produces: identical `listBuilder(limit?, tier?)` signature and `usePlaystatBuilderParlays()` returning the `?tier=all` feed.

- [ ] **Step 1: Add the `tier` argument to `listBuilder`**

In `lib/playstat.ts`, replace lines 155-156:

```ts
    listBuilder: async (
      limit = 10,
      tier?: 'player' | 'team' | 'all'
    ): Promise<PlaystatBuilderConstruction[]> =>
      fetchJson<PlaystatBuilderConstruction[]>(
        `/parlay-builder/saved?limit=${limit}${tier ? `&tier=${tier}` : ''}`
      ),
```

- [ ] **Step 2: Switch the hook to the combined feed**

In `lib/queries.ts`, replace the `usePlaystatBuilderParlays` body (lines 88-92):

```ts
export function usePlaystatBuilderParlays() {
  return useQuery({
    queryKey: ['playstat-builder-parlays', 'all'],
    queryFn: () => playstatApi.parlays.listBuilder(100, 'all'),
  });
}
```

- [ ] **Step 3: Typecheck**

Run: `cd ~/dev/BudgerrApp && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
cd ~/dev/BudgerrApp && git add lib/playstat.ts lib/queries.ts
git commit -m "feat: fetch builder parlays via ?tier=all (combined feed)

Mirror budgerr-web: add optional tier arg to listBuilder; switch
usePlaystatBuilderParlays to listBuilder(100,'all').

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5 (BudgerrApp): `BuilderParlayCard` variance variant (RN)

**Files:**
- Modify: `components/tonight/BuilderParlayCard.tsx`

**Interfaces:**
- Produces: `BuilderParlayCard` accepts `variant?: 'lowrisk' | 'variance'` (default `'lowrisk'`). `'variance'` uses the theme's amber `warning`/`warningBg` tokens for the border and joint-prob badge instead of the green `edge`/`edgeBorder`.

- [ ] **Step 1: Add the `variant` prop**

In `components/tonight/BuilderParlayCard.tsx`, change the props block (lines 15-23):

```tsx
export function BuilderParlayCard({
  construction,
  gamesById,
  remainingBudget = 0,
  variant = 'lowrisk',
}: {
  construction: PlaystatBuilderConstruction;
  gamesById: Map<number, PlaystatGame>;
  remainingBudget?: number;
  variant?: 'lowrisk' | 'variance';
}) {
```

- [ ] **Step 2: Compute variant colors after `const theme = ...` (line 24)**

Add directly below line 24:

```tsx
  const isVariance = variant === 'variance';
  const cardBorder = isVariance ? theme.warning : theme.edgeBorder;
  const jointBadgeBg = isVariance ? theme.warningBg : theme.edgeBg;
  const jointBadgeText = isVariance ? theme.warning : theme.edge;
```

- [ ] **Step 3: Apply the border**

Replace the card `<View>` (line 43):

```tsx
    <View style={[styles.card, { backgroundColor: theme.card, borderColor: cardBorder }]}>
```

- [ ] **Step 4: Apply the joint-prob badge colors**

Replace the joint-prob badge block (lines 54-58):

```tsx
          <View style={[styles.badge, { backgroundColor: jointBadgeBg }]}>
            <Text style={[styles.badgeText, { color: jointBadgeText }]}>
              {Math.round(construction.joint_prob * 100)}% to hit
            </Text>
          </View>
```

- [ ] **Step 5: Typecheck**

Run: `cd ~/dev/BudgerrApp && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
cd ~/dev/BudgerrApp && git add components/tonight/BuilderParlayCard.tsx
git commit -m "feat: add variance variant to BuilderParlayCard (mobile)

variant='variance' uses the theme amber warning tokens for the border and
joint-prob badge (not the green edge tokens). Default 'lowrisk' unchanged.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6 (BudgerrApp): Tonight screen — team section + partition

**Files:**
- Modify: `app/(tabs)/tonight.tsx`

**Interfaces:**
- Consumes: `usePlaystatBuilderParlays()` (`?tier=all`), `hasTeamLeg`, `selectLatestRun`, `runDate`, `isRunFullyPast`, `usePlaystatGames`, `BuilderParlayCard` (with `variant`). No demo affordance on mobile (no URL params).

- [ ] **Step 1: Add `hasTeamLeg` to the imports**

In `app/(tabs)/tonight.tsx`, change line 10:

```tsx
import { hasTeamLeg, isRunFullyPast, runDate, selectLatestRun } from '@/lib/builderParlays';
```

- [ ] **Step 2: Partition + resolve both runs' games**

Replace the builder block (lines 43-62, from `const builderParlays = ...` through the `builderConstructions` useMemo) with:

```tsx
  const builderParlays = usePlaystatBuilderParlays(); // ?tier=all combined feed

  const playerCons = useMemo(
    () => (builderParlays.data ?? []).filter((c) => !hasTeamLeg(c)),
    [builderParlays.data]
  );
  const teamCons = useMemo(
    () => (builderParlays.data ?? []).filter((c) => hasTeamLeg(c)),
    [builderParlays.data]
  );

  const latestRun = useMemo(() => selectLatestRun(playerCons, 4), [playerCons]);
  const latestTeamRun = useMemo(() => selectLatestRun(teamCons, 4), [teamCons]);

  // Resolve each section's games from its OWN run's date (player and team runs
  // are typically different days).
  const builderGames = usePlaystatGames(runDate(latestRun));
  const builderGamesById = useMemo(() => {
    const map = new Map<number, PlaystatGame>();
    for (const game of builderGames.data ?? []) map.set(game.game_id, game);
    return map;
  }, [builderGames.data]);

  const teamGames = usePlaystatGames(runDate(latestTeamRun));
  const teamGamesById = useMemo(() => {
    const map = new Map<number, PlaystatGame>();
    for (const game of teamGames.data ?? []) map.set(game.game_id, game);
    return map;
  }, [teamGames.data]);

  const builderConstructions = useMemo(() => {
    if (latestRun.length === 0) return [];
    if (!builderGames.data) return []; // wait for the run's games before deciding
    if (isRunFullyPast(latestRun, builderGamesById)) return []; // hide a stale past run
    return latestRun;
  }, [latestRun, builderGames.data, builderGamesById]);

  const teamConstructions = useMemo(() => {
    if (latestTeamRun.length === 0) return [];
    if (!teamGames.data) return [];
    if (isRunFullyPast(latestTeamRun, teamGamesById)) return [];
    return latestTeamRun;
  }, [latestTeamRun, teamGames.data, teamGamesById]);
```

- [ ] **Step 3: Add `teamGames.refetch()` to `refetchAll`**

In `refetchAll` (lines 90-98), add after `builderGames.refetch();`:

```tsx
    teamGames.refetch();
```

- [ ] **Step 4: Add the team-market section JSX**

After the low-risk section block (after its closing `)}` on line ~136, before the slate-heading `<Text>` on line ~138), insert:

```tsx
      <Text style={[styles.sectionTitle, { color: theme.textSecondary }]}>
        Team markets (NRFI/F5) — higher variance
      </Text>
      <Text style={{ color: theme.textMuted, fontSize: 11, marginTop: -6, marginBottom: 10 }}>
        ~30–50% to hit · logs as paper, won&apos;t auto-settle.
      </Text>
      {teamConstructions.length === 0 ? (
        <Text style={{ color: theme.textMuted, fontSize: 13 }}>
          No team-market parlays in tonight&apos;s build — the team tier is often empty.
        </Text>
      ) : (
        teamConstructions.map((construction) => (
          <BuilderParlayCard
            key={construction.parlay_id}
            construction={construction}
            gamesById={teamGamesById}
            remainingBudget={bettingPeriod?.remaining ?? 0}
            variant="variance"
          />
        ))
      )}
```

- [ ] **Step 5: Typecheck**

Run: `cd ~/dev/BudgerrApp && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
cd ~/dev/BudgerrApp && git add "app/(tabs)/tonight.tsx"
git commit -m "feat: add higher-variance team-market section to Tonight (mobile)

Mirror budgerr-web: partition the ?tier=all feed by hasTeamLeg into the low-risk
(player) section and a new team-market (NRFI/F5) section with its own run-date
game resolution and fully-past hiding.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7 (architect, not a worker): End-to-end verification + README + module byte-identity

**Files:**
- Modify: `README.md` (§15) in `~/dev/Budgerr`.

- [ ] **Step 1: Confirm `lib/builderParlays.ts` byte-identity**

Run: `diff -q ~/dev/BudgerrApp/lib/builderParlays.ts ~/dev/budgerr-web/lib/builderParlays.ts`
Expected: no output (identical, unchanged).

- [ ] **Step 2: Web build + Vitest**

Run: `cd ~/dev/budgerr-web && npm run build && npx vitest run`
Expected: build passes; all Vitest tests (existing 16 + 2 new) pass.

- [ ] **Step 3: Drive `/tonight` in the browser pane**

`preview_start "budgerr-web"`, navigate to `/tonight`:
- Confirm the **low-risk section is unchanged** (player cards from the 07-28 run).
- Confirm the **team section shows its empty state** ("the team tier is often empty") — the real 07-26 run is hidden as fully-past.
- Navigate to `/tonight?demo=builder-team`: confirm the **real team card renders** in the amber variance variant with a resolved matchup and the "won't auto-settle" note; log it with a stake; confirm a **pending** paper bet is created (no auto-settle), then delete the test row (`DELETE FROM bets WHERE bet_id=…` via `docker exec budgerr-postgres-1 psql -U budgerr -d budgerr`). If Docker is down: `open -a Docker && cd ~/dev/Budgerr && docker compose up -d`.

- [ ] **Step 4: Mobile typecheck**

Run: `cd ~/dev/BudgerrApp && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 5: Update README §15** documenting the team-market tier section (single `?tier=all` fetch, client-side partition, variance variant, log-only, empty-by-nature), in the same commit as merge.

- [ ] **Step 6: Merge worker branches, commit README, push both repos.**

---

## Self-review

- **Spec coverage:** §4.1 → Tasks 1/4; §4.2 → Tasks 1/4; §4.3 → Tasks 3/6; §4.4 → Tasks 3/6; §4.5 → Tasks 2/5; §4.6 → Task 3 (mobile has no demo, noted); §4.7 → Tasks 4/5/6; §6 acceptance → Task 7; §7 verification → Task 7. All covered.
- **Placeholders:** none — every code step shows full code; README step names the exact content to add.
- **Type consistency:** `listBuilder(limit?, tier?)`, `variant?: 'lowrisk' | 'variance'`, `hasTeamLeg`, `selectLatestRun`, `runDate`, `isRunFullyPast`, `teamGamesById`, `teamConstructions`, `latestTeamRun` used consistently across web and mobile tasks.
- **Byte-identity guard:** `lib/builderParlays.ts` is edited by no task and diff-checked in Task 7.
