# Budgerr — Architecture Plan

## 1. Goal

A personal-use app (React Native mobile + a Next.js web mirror) that:
- Connects your real bank accounts and categorizes spending automatically
- Tracks your bets/parlays across sportsbooks as a first-class budget category, not an afterthought
- Shows you a monthly betting allowance, net win/loss, and trend — alongside rent, groceries, etc.
- Ties into the basketball analytics dashboard so "tonight's slate + your remaining betting budget" is one glance, not three apps

Scope for now: **you, personally.** Section 13 covers what changes if this ever goes beyond that.

---

## 2. System Overview

```
Plaid (bank accounts) ──────┐
                             ├──> Backend (FastAPI) ──> PostgreSQL ──> Budgeting Engine ──> Dashboard (RN app / web)
Manual quick-entry ──────────┘
   (bet placed → logged in-app,
    settled → won/lost/push toggle)
```

---

## 3. Data Layer

### 3.1 Bank data — Plaid
- Plaid Link handles the actual bank login; your app only ever sees a token, never your bank password
- Free Trial plan (accounts created after April 15, 2026): up to 10 linked Items with real production data, no cost — plenty for your own accounts
- Build against **Sandbox** (fake data) first, confirm the pipeline works end to end, then switch to real accounts
- Plaid's Transactions API + webhook keeps new transactions flowing in without polling
- Institution name is resolved via Plaid's `/institutions/get_by_id` at link time (not the raw `institution_id`, e.g. `ins_10`) so linked accounts show as "American Express", not an opaque ID
- `GET /plaid/accounts` and `GET /plaid/transactions` surface what's already synced — these didn't exist for a while, so linking a bank account had nowhere to show up. Both frontends now have Accounts and Transactions screens (web: `/accounts`, `/transactions`; mobile: two new tabs)
- `PATCH /plaid/transactions/{txn_id}` sets a transaction's `custom_category` (a category dropdown on web, a bottom-sheet picker on mobile); syncing also best-effort auto-categorizes by matching Plaid's own category string against your category names, without overriding anything you've already set manually
- Every sync, categorization change, and dashboard/Budget-tab load recomputes `budget_periods` for the affected month(s) — a category you create now actually shows spent/remaining instead of silently having no period row until something happened to trigger a recompute
- Bank *linking* (Plaid Link) is still web-only — the mobile client would need the native Plaid Link SDK, a separate, bigger lift. Link via the web app; linked accounts show up on mobile too since both clients share the same backend

### 3.2 Betting data — manual quick-entry

No major sportsbook (DraftKings, FanDuel, bet365, and others show the same
pattern) provides a reliable native export for itemized bet/parlay-leg history —
what exists is a viewable transaction or P/L statement page, not a structured
export with legs, lines, and odds. This holds across books, so rather than
building and maintaining a separate scraper or importer per sportsbook, a single
manual quick-entry flow covers all of them consistently.

**Approach: quick-entry at time of bet**

- One entry screen in the app, used regardless of which sportsbook the bet was
  placed on
- Fields: sportsbook, bet type (`single` | `parlay`), stake, potential payout,
  and per-leg detail (player, stat type, line, side, odds)
- Target: under 15 seconds per bet. Pre-fill where possible — pull tonight's slate
  from the basketball model so you're selecting from a list of tonight's players/props
  rather than typing names and lines by hand
- **Settlement**: a manual won/lost/push toggle (`PATCH /bets/{bet_id}/settle`)
  remains for anything auto-settlement can't resolve — non-prop bets, or props
  on stats playstat doesn't track
- **Auto-settlement — done**: `POST /bets/auto-settle` cross-references pending
  `bet_legs` against playstat's `GET /box-scores?date=` (new endpoint, final
  games only — `games.status = 'FT'`), matched on the bet's `placed_at` date
  and `player_name`. A leg resolves if its `stat_type` appears in the player's
  per-player `stats` map for that date (playstat's generic multi-sport stats —
  MLB hitter/pitcher props included), falling back to the legacy top-level
  `points` | `rebounds` | `assists` fields for NBA rows; parlays win only if
  every leg wins (any loss fails the whole
  bet, a push-only combination pushes). Anything unresolvable — game not final
  yet, unrecognized stat, name mismatch, non-prop bet with no player/stat —
  is left pending and retried on the next call. Scheduled via launchd
  (`com.budgerr.auto-settle`, daily 8:30am — after playstat's 8am box-score
  backfill; see Section 8)
- **Paper bets**: `bets.is_paper` logs a bet with a *hypothetical*
  stake/payout — it auto-settles exactly like a real bet but is excluded from
  the real-money P/L in `GET /bets/trend`, so model parlay recommendations can
  be tracked risk-free. Legs store `model_prob` (playstat's predicted win
  probability at log time) for the future hit-rate-vs-model calibration check
  (Section 14 item 4). Both frontends expose "Log as paper bet" on the Tonight
  view's parlay cards (convention: `sportsbook = "paper"`, default $10
  hypothetical stake)

**What stays automated**

- Net betting cash flow (deposits/withdrawals) still comes from the bank side via
  Plaid, regardless of which sportsbook it's tied to
- This only changes *how* `bets` / `bet_legs` get populated — the schema from
  Section 3.3 is unchanged

### 3.3 Schema sketch (PostgreSQL)

```sql
-- Bank side
accounts(account_id, plaid_item_id, institution_name, account_type, mask, current_balance)
transactions(txn_id, account_id, date, amount, merchant_name, plaid_category, custom_category, is_betting)

-- Betting side
bets(bet_id, sportsbook, placed_at, bet_type,        -- 'single' | 'parlay'
     stake, potential_payout, status, settled_at, net_result)
bet_legs(leg_id, bet_id, player_name, stat_type, line_value, side, odds, leg_status)

-- Budgeting
categories(category_id, name, monthly_limit, is_betting_category)
budget_periods(period_id, category_id, month, spent, limit, remaining)
alerts(alert_id, category_id, threshold_pct, triggered_at, message)
```

`is_betting` on `transactions` gets set by merchant-name matching (DraftKings, FanDuel, BetMGM, Caesars, ESPN Bet, etc.) so bank-side betting deposits/withdrawals and sportsbook-side bet detail can be reconciled against each other later if you want that level of detail.

---

## 4. Categorization & Betting Detection

- Plaid provides category enrichment out of the box; layer a merchant-name rule set on top specifically for sportsbook merchants
- Net betting spend = deposits to sportsbooks − withdrawals from sportsbooks (not gross deposits) — a $200 deposit that comes back as $150 isn't a $200 month
- `bets`/`bet_legs` give you the granular "what did I actually bet on" view; `transactions` gives you the "what did it cost me" view — both matter for different questions

---

## 5. Budgeting Engine

- Standard envelope budgeting: monthly limit per category, rolling spent/remaining
- Betting is just another category with its own limit — same mechanism as groceries or dining out, no special-casing needed in the engine itself
- Alert thresholds (e.g., 80%, 100% of monthly betting limit) trigger a notification
- Trend view: betting spend vs. income vs. other discretionary categories, month over month

---

## 6. Basketball Dashboard Integration

The basketball stats/edge/parlay-optimizer project ([`playstat`](https://github.com/aayushpokhrel1/Playstat)) stays a fully separate backend and Postgres instance — no shared database, no merged schema. Both Budgerr frontends (`budgerr-app`, `budgerr-web`) call playstat's API directly (no backend proxy):

- `GET /edges` (today's positive-edge legs — player, stat, line, side, odds) — the Log-a-bet form's "Tonight's edges" panel uses this to pre-fill a leg in one click instead of typing player/line/odds by hand
- `GET /games?date=&sport=` (added for this feature — every game on the date, not just legs with a positive edge, so the slate shows up even before `prop_lines`/`edges` have real data)
- **Tonight glance view — done**: a dedicated screen (`app/(tabs)/tonight.tsx` on mobile, `/tonight` on web) combining the remaining betting-budget card with tonight's full slate (`/games`) and any positive edges per game (`/edges`, grouped by `game_id`) — the original single-glance vision from Section 1, now built

- Playstat needs `CORSMiddleware` configured (`CORS_ORIGINS` env var) so the browser-based `budgerr-web` client can call it directly
- Still true either way: two backends, no shared Postgres, decide on merging later only if it turns out to matter

---

## 7. Credit Card Rewards Tracker

No card issuer publishes reward category rates as a structured API — this is
inherently a manually-maintained dataset, but that's fine given it's just your
own handful of cards, updated a few times a year when rotating categories change.

### 7.1 Schema

```sql
credit_cards(card_id, name, issuer, nickname)

card_reward_rates(
  rate_id, card_id, category_id, multiplier,
  cap_amount, cap_period,        -- 'quarterly' | 'annual' | null (uncapped)
  effective_start, effective_end -- handles rotating categories (e.g. Chase Freedom, Discover it)
)

card_reward_progress(
  card_id, category_id, period_start, period_end, amount_spent_at_bonus_rate
)
```

`category_id` reuses the categories table from Section 5 — no separate
categorization system needed.

### 7.2 Two modes

**Proactive — "which card right now"**
Pick a category → return the card with the best *currently active* multiplier
for it, checking `card_reward_progress` against `cap_amount` first. If a card's
bonus category is already capped out for the period, it drops out of
consideration and the next-best card surfaces instead — otherwise you'd keep
getting recommended a card that's earning 1% instead of 5% because the cap was
already hit.

**Retrospective — "rewards left on the table"**
Since every transaction is already flowing in and categorized via Plaid
(Section 3.1 / Section 4), this is close to free: for each past transaction,
compare the card actually used against what the optimal card would have earned,
and surface the gap. Rolls up into a monthly/quarterly trend — a genuinely
useful view most consumer reward apps don't give you, since they don't have
your real transaction data to check against.

### 7.3 Maintenance

- Rotating-category cards (5% quarterly categories) need a quick manual update
  each quarter when the new categories are announced — a reminder/notification
  tied to `effective_end` dates is enough to keep this from going stale. This is
  now served by `GET /rewards/expiring-rates` for dashboard banners in both
  frontends
- `card_reward_progress` resets automatically at each `cap_period` boundary

### 7.4 Automated rate lookup — built, not yet enabled

Since no issuer exposes reward rates as a structured API, `POST
/rewards/cards/{card_id}/fetch-rates` has Claude (Anthropic API, with web
search) research a named card's current reward categories and propose
structured rates — nothing is saved at this step. `POST
/rewards/cards/{card_id}/reward-rates/confirm` then saves a reviewed/edited
version of that proposal (auto-creating any category that doesn't already
exist). Code is in `app/rewards_lookup.py` and `app/routers/rewards.py`.

**Not turned on yet** — `ANTHROPIC_API_KEY` is intentionally left unset in
`backend/.env` (see `.env.example`) since each lookup costs money. Add a key
from console.anthropic.com when ready to actually use this; until then
`fetch-rates` returns a 501.

---

## 8. Backend / API

- Python, FastAPI
- Scheduled jobs — all wired via launchd LaunchAgents: `com.budgerr.auto-settle` (daily 8:30am, after playstat's 8am box-score backfill) hits `POST /bets/auto-settle`, and `com.budgerr.plaid-sync` (daily 7:00am) hits `POST /plaid/sync-all`, which syncs every linked Plaid item (tolerating per-item failures) and recomputes budget periods per touched month — alert-threshold checks run inside that recompute, so they need no separate job
- One internal API serving both the budgeting data and (optionally) the basketball model outputs to a shared frontend

## 9. Frontend

Two separate clients, both talking to the same FastAPI backend — no shared frontend code, each repo owns its own UI.

### 9.1 Mobile — React Native (Expo)

- Separate repo: [`budgerr-app`](https://github.com/aayushpokhrel1/budgerr-app) — Expo + TypeScript, Expo Router for navigation, React Query for data fetching
- A Budget tab (betting allowance, other category tiles, recent bets + quick-entry log, best-card tip, net profit vs. bank cash flow) — the primary day-to-day surface, especially for logging a bet in the moment
- Future: a Stats tab once the basketball analytics dashboard project is far enough along to merge in (see Section 6)
- Side-loaded APK for personal use, per Section 11 — no Play Store listing needed

### 9.2 Web — Next.js

- Separate repo: [`budgerr-web`](https://github.com/aayushpokhrel1/budgerr-web) — Next.js App Router, TypeScript, Tailwind CSS, React Query for data fetching
- A full mirror of the mobile app's functionality, not just a read-only viewer: dashboard, bets (log/settle), rewards (cards/rates/best-card lookup/left-on-table), categories, and the Plaid Link flow — same FastAPI backend, same data
- Useful for anything more comfortable on a bigger screen: reviewing longer bet history, adjusting reward card rates, deeper trend views

Both clients require the backend's CORS origins (`CORS_ORIGINS` in `backend/.env`) to include wherever they're served from during development.

---

## 10. Security & Non-Negotiables (personal use, still matters)

- HTTPS everywhere — no exceptions, even for a single-user app
- Plaid keys and DB credentials in environment variables/secrets, never committed to the repo
- Some form of auth on the app itself, even single-user — don't leave a bank-data endpoint open on the internet with no login
- Regular encrypted backups of the Postgres DB
- Keep dependencies patched — real bank data deserves real hygiene, hobby project or not

---

## 11. Deployment (Personal Use)

### 11.1 Current setup: always-on local backend (interim)

Until real deployment happens, the backend runs locally via a `launchd` LaunchAgent (`~/Library/LaunchAgents/com.budgerr.backend.plist`, not tracked in git) so it survives logout/reboot and auto-restarts on crash, with Postgres running in Docker (`restart: unless-stopped`).

All four related projects (`Budgerr`, `budgerr-app`, `budgerr-web`, and the separate `playstat` project) live under `~/dev`, not `~/Documents` — `~/Documents` is iCloud-synced on this machine, and that sync caused intermittent file-read deadlocks (`OSError: [Errno 11]`) specifically for `launchd`-spawned processes. Python venvs and `node_modules` aren't portable between the two (they embed absolute paths), so moving any of these projects again means rebuilding those, not just copying the folder.

### 11.2 Eventual real deployment

Two realistic options:

| Option | Notes |
|---|---|
| **Small cloud VPS** ($5–6/mo — DigitalOcean, Railway, Fly.io, Render) | Reachable from your phone anywhere, not just home wifi. Simplest for always-on scheduled jobs. |
| **Home server / Raspberry Pi + Tailscale** | Free, but only as reliable as your home internet/power. Tailscale gets you secure remote access without exposing anything publicly. |

**Mobile app**: since it's just for you, build the APK and side-load it directly onto your Android phone (enable "install from unknown sources"). No Play Store listing, no $25 developer fee, no review process — that's only needed if this ever goes public.

---

## 12. Build Order

1. Postgres schema (bank + betting + budgeting tables)
2. Plaid Sandbox integration → confirm pipeline → switch to real accounts
3. Manual quick-entry screen for bets/parlays (single form covering all sportsbooks)
4. Categorization rules (betting merchant detection, net win/loss calc)
5. Budgeting engine (categories, limits, alerts)
6. Credit card rewards tracker (schema + proactive/retrospective lookup)
7. Budget tab in the RN app (`budgerr-app` repo)
8. Web mirror in Next.js (`budgerr-web` repo)
9. Deploy to VPS or home server
10. Basketball dashboard tie-in — done: playstat `/edges` → bet quick-entry pre-fill, and the combined tonight's-slate + remaining-budget glance view (Section 6)

---

## 13. Future Plans — Beyond Personal Use

Not part of the current build, but worth having on paper if it ever comes up:

- **Plaid Production approval**: the free Trial plan caps at 10 Items. Supporting other users means a full Production application — business verification, security review, and usage-based billing once you're past Trial/Limited Production.
- **Multi-user architecture**: per-user auth, data isolation, and likely a proper OAuth flow rather than a single hardcoded account.
- **Compliance considerations**: handling other people's bank data and betting activity brings in real obligations — financial data handling regulations, and (depending on how "betting" features are framed) potential gambling-related regulatory questions per state. This is a legal-review conversation, not a weekend feature add.
- **SharpSports/BetSync at scale**: at $500/mo it only makes sense once there's a user base to spread that cost across — worth reconsidering if this ever has real users.
- **Hosting cost model**: moves from a $5/mo VPS to something that scales with users — a deliberate re-architecture, not a config change.

None of this blocks anything in the personal build — it's here so a future "should we open this up" conversation starts from a plan instead of a scramble.

---

## 14. Feature Roadmap (personal scope)

The Section 12 build order is done; this is the next layer, roughly in value-per-effort order:

1. ~~Schedule the built-but-unscheduled jobs~~ — **done** (Section 8): auto-settle (launchd, daily 8:30am) and Plaid sync via the new `POST /plaid/sync-all` (launchd, daily 7:00am); alert-threshold checks run inside sync's budget-period recompute.
2. ~~Multi-sport stat types from playstat~~ — **done** (Section 3.2): settlement reads `stats[leg.stat_type]` from playstat's per-player `stats` map (any stat playstat tracks, MLB hitter/pitcher props included), with the legacy top-level `points|rebounds|assists` fields as an NBA fallback. Unresolvable legs still stay pending. Covered by `backend/tests/test_auto_settlement.py`.
3. ~~The original "one glance" view~~ — **done** (Section 6): the Tonight screen combines remaining betting budget, the full slate via playstat's `/games`, and per-game edges. With MLB now ingested in playstat, it has live content in July rather than only during the NBA season.
4. ~~Bet performance analytics~~ — **done**: `GET /bets/analytics?scope=real|paper` returns ROI by sportsbook / bet type / stat type, plus decile-bucketed *actual hit rate vs. the model's predicted probability* over settled bets. Both frontends have an Analytics view on top of it (web `/analytics`, mobile Analytics tab) with a real/paper toggle and predicted-vs-actual calibration bars.
5. ~~Recurring-charge detection~~ — **done**: `GET /plaid/recurring-charges` groups transactions by merchant (case/whitespace-normalized), greedily clusters by amount (within 10% of the cluster's running median), and flags a cluster as recurring when it has >=3 occurrences with a 20-40 day median gap between dates. Pure detection logic lives in `app/recurring.py` (unit-tested without a DB in `backend/tests/test_recurring.py`); the endpoint just queries `transactions` once and delegates to it.
6. ~~Rotating-category reminder~~ — **done**: `GET /rewards/expiring-rates?within_days=45` surfaces `card_reward_rate` rows whose `effective_end` falls in `[today - 7 days, today + within_days]`, for dashboard banners in both frontends — no push infra, a deliberate choice to keep this simple for a single-user app you check daily anyway.

All six items above are shipped. Section 15 is the next layer.

---

## 15. Future Roadmap (second layer)

Everything in Sections 12 and 14 is done. This is the forward plan, grouped into four directions (full write-up with effort/value calls lives in the session artifact "Budgerr — What's Next").

### 15.1 Tier A — Close the model loop *(in progress)*

- **Auto-log paper bets** — `POST /bets/auto-log-recommendations` pulls playstat's `/parlay-recommendations`, enriches legs with line/date from `/edges`, and logs each as a $10 paper bet; `bets.external_ref` (`"playstat-parlay-{id}"`, unique) dedupes across runs. Scheduled via launchd daily at 9:00am, after playstat's ~8:30am optimizer run — calibration data accrues with zero taps.
- **Bankroll curve & drawdown** — `GET /bets/bankroll?scope=real|paper`: cumulative-P/L time series over settled bets plus max drawdown and longest losing streak; charted on both Analytics views.
- **Kelly-style stake sizing** — ¼-Kelly suggestion shown on the Tonight parlay cards, computed client-side (`lib/kelly.ts`) from `joint_prob` + combined odds against the remaining betting budget. **Web done; mobile TODO** (port `lib/kelly.ts` and the ParlayCard line — see 15.6). Display-only guidance; treat with suspicion until the calibration view validates the model's probabilities.
- **NFL-readiness** *(free when playstat ships it)* — settlement reads any stat in the `/box-scores` `stats` map, so NFL props (playstat README §13) need zero Budgerr changes; just sanity-check stat-type naming when the time comes.
- **Bet-slip screenshot import** — backend **done**: `POST /bets/parse-slip` (router `app/routers/bet_import.py`, extraction/parsing logic unit-tested in `app/slip_parser.py`) sends a sportsbook screenshot to Claude (vision, claude-sonnet-5) and returns structured bet fields — nothing is saved; the result pre-fills the quick-entry form for human review. Returns 501 until `ANTHROPIC_API_KEY` is set, same pattern as Section 7.4. Frontend upload UI: web/mobile still TODO (see 15.6).
- **Closing-line value (CLV)** *(blocked on playstat)* — compare odds taken vs. the closing line, the sharpest long-term edge signal. Needs playstat to store closing lines first; design it from that side.

### 15.2 Tier B — Get off the laptop

- **Auth on the API** — Section 10 requires it; none exists today. Mirror playstat's approach (it now enforces an `X-API-Key` header via a global FastAPI dependency, keys in env as comma-separated `name:key` pairs, `AUTH_ENABLED` kill-switch) so the two backends share one mental model. Both clients attach the key from their env (`NEXT_PUBLIC_…` / `EXPO_PUBLIC_…` is acceptable for a single-user app; the key protects a localhost/Tailscale service, not a public secret). Prerequisite for deployment.
- **Proxy playstat through the Budgerr backend** — Section 6 originally chose direct browser→playstat calls; playstat's new API-key auth changes the calculus. A thin `/playstat/*` passthrough in the Budgerr backend keeps the playstat key server-side only, collapses two client API configs into one, and removes playstat's CORS dependency. Decide alongside the auth item; if staying direct, both frontends need the playstat key in their env instead.
- **Encrypted Postgres backups** — nightly `pg_dump -Fc`, encrypt with `age`, copy off-machine (iCloud Drive folder is fine — it's encrypted output, and ~/Documents sync issues don't apply to a copy job). launchd timer + a documented restore drill actually performed once. The other unmet Section 10 non-negotiable.
- **Deploy** — the Section 11.2 decision: small VPS, or Pi + Tailscale. Move playstat and Budgerr **together** (auto-settle/auto-log assume playstat on the same host). Both stacks are already Docker-friendly (Postgres in compose); add containers for the two APIs, Caddy for TLS if VPS, launchd jobs become cron/systemd timers. Unlocks the phone outside home wifi.
- **Push notifications** — settlement results ("Parlay hit · +$25"), 80%/100% budget alerts (the `alerts` rows are already created in the budget recompute — they just don't go anywhere), expiring rotating categories, auto-log confirmations. ntfy.sh is the pragmatic single-user route: one `httpx.post` helper in the backend, subscribe on the phone; no APNs/FCM setup. Expo push is the native alternative if the app should own the notification surface.
- **Plaid webhooks** — replace the daily 7am polling sync with `SYNC_UPDATES_AVAILABLE` webhooks once a public HTTPS URL exists (post-deployment). Keep the daily sync as a fallback sweep.
- **CI** — GitHub Actions on all three repos: backend pytest, web `npm run build`, mobile `tsc --noEmit`. Sub-hour setup; catches what a dead subagent or interrupted session would otherwise leave broken.

### 15.3 Tier C — Smarter money analysis

- **Price-hike + annual-subscription detection** — extend `app/recurring.py`: flag a cluster whose amounts trend upward over its lifetime (compare first-third vs last-third means, not just the 10% membership tolerance), and add a second cadence pass for ~365-day gaps (annual renewals — the ones that actually ambush you). Surface hikes as their own dashboard line ("Netflix up $0.23 since January").
- **Sportsbook reconciliation** — for each month, compare bank-side `is_betting` net outflow against logged-bet activity per sportsbook (deposits imply stakes should exist within a window). Surface unmatched deposits as "money sent to DraftKings with no logged bets" — the bet log lying by omission. Needs merchant→sportsbook normalization (the `is_betting` matcher already knows the merchant names).
- **Cash-flow forecast** — detect income (large, regular credit inflows via the same cadence machinery as `recurring.py`), project each category's end-of-month position from day-of-month run rate vs. historical same-day pace, and flag "on pace to overshoot Dining by $40 by the 31st." Pure analysis; no schema change.
- **Turn on the Claude rate lookup** — Section 7.4 is built; just set `ANTHROPIC_API_KEY` in `backend/.env` (the same key also activates `POST /bets/parse-slip`). Pairs with the expiring-rate banner at quarter rollovers: banner fires → one click researches the new categories → confirm saves them.
- **Card-aware "left on the table"** — `credit_cards.linked_account_id` already exists in the web types; complete the loop so the retrospective report uses the card *actually used* per transaction (via its account) instead of assuming the optimal card was available. Makes the most under-appreciated feature in the app trustworthy.

### 15.4 Tier D — Quality of life

- **Monthly digest** — generated first-of-month summary (spend vs. budget, bet P/L, calibration, subscription changes), delivered over the Tier B notification channel.
- **Native Plaid Link on mobile** — closes the last web-only gap (Section 3.1).
- **Biometric lock on mobile** — `expo-local-authentication` gate; it's bank data on a side-loaded APK.
- **CSV export & year-in-review** — transactions/bets export, plus a December summary.

### 15.5 Recommended sequence

Finish the 15.6 loose ends first (playstat key wiring is blocking the whole model loop) → auth + backups (small, overdue) → deploy → push notifications → webhooks + price-hike detection + reconciliation → Kelly sizing trusted only after calibration data validates `model_prob`.

### 15.6 State of play & immediate loose ends (as of 2026-07-14)

Snapshot for whoever picks this up next — the working set is three repos (`~/dev/Budgerr` backend, `~/dev/budgerr-web`, `~/dev/BudgerrApp`), all on clean pushed `main`; backend suite is 53 passing tests.

**Blocking — playstat now enforces API-key auth.** playstat added an `X-API-Key` requirement (`api/auth.py` in that repo: `AUTH_ENABLED` + `PLAYSTAT_API_KEYS="name:key,…"` env vars). Until keys are wired, Budgerr's auto-settle/auto-log get empty results (they fail soft, returning zeros) and both frontends' Tonight/edges panels get 401s. To fix:
  1. Provision a `budgerr` key in playstat's `PLAYSTAT_API_KEYS` (owner does this — never modify playstat from a Budgerr session).
  2. Set `PLAYSTAT_API_KEY=<that key>` in `backend/.env` and restart the backend — the client (`app/playstat_client.py`) already sends the header when set.
  3. Frontends: decide the Tier B "proxy playstat through the backend" question. Recommended: add the thin backend passthrough and point both frontends at it (keeps the key server-side, kills the CORS requirement). Quick alternative: `NEXT_PUBLIC_PLAYSTAT_API_KEY` / `EXPO_PUBLIC_PLAYSTAT_API_KEY` attached in `lib/playstat.ts` fetchers.

**Loose ends from the Tier A build** (a session-limit outage killed three subagents mid-flight; completed parts were salvaged and shipped):
  - Web: screenshot-import UI in `components/bets/BetForm.tsx` — the `api.bets.parseSlip` fetcher and `ParsedSlip` types are already in `lib/api.ts`; add the file input, loading state, and merge-into-form-for-review flow (never auto-submit). 501 → "needs ANTHROPIC_API_KEY".
  - Mobile: ¼-Kelly line on `components/tonight/ParlayCard.tsx` (port web's `lib/kelly.ts`; Tonight screen already passes budget data for the paper-stake flow) and the screenshot-import flow in `app/modal.tsx` (needs `expo-image-picker`, not yet installed; `ParsedSlip` types already in `lib/api.ts`).
  - Verify `POST /bets/auto-log-recommendations` end-to-end once the playstat key is set (launchd job `com.budgerr.auto-log-parlays`, daily 9:00am, is already loaded) — it should log that morning's recommendations exactly once, and a second call should report them all in `skipped_existing`.

**Standing environment facts:** backend runs as launchd service `com.budgerr.backend` on port **8001** (playstat is 8000) — restart with `launchctl kickstart -k gui/$(id -u)/com.budgerr.backend`. Other launchd jobs: `plaid-sync` 7:00am, `auto-settle` 8:30am, `auto-log-parlays` 9:00am. Postgres runs in Docker (port 5433); Docker Desktop `AutoStart` was enabled 2026-07-14 but verify after the next reboot. One real pending paper parlay (Rocchio/Gasper, 7/17 games) is in the DB — do not delete it.
