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
- **Settlement**: start with a manual won/lost/push toggle once a bet settles;
  automate later by cross-referencing final box scores (already in your
  `player_game_stats` table from API-Basketball) against open `bet_legs`

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

Since you're already building the stats/edge/parlay-optimizer dashboard as a separate project, the natural tie-in point is a single view that shows, before you open any betting app:
- Tonight's games and player projections vs. lines (from the basketball project)
- Your remaining betting budget for the month (from this project)

These can stay two backends sharing one Postgres instance and one frontend, or merge later — worth deciding once both are far enough along to see how much they actually overlap.

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
  tied to `effective_end` dates is enough to keep this from going stale
- `card_reward_progress` resets automatically at each `cap_period` boundary

---

## 8. Backend / API

- Python, FastAPI
- Scheduled jobs (cron or a simple task queue): Plaid transaction sync, alert threshold checks, box-score cross-reference for auto-settlement
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
10. (Optional) Wire in the basketball dashboard's tonight's-slate view

---

## 13. Future Plans — Beyond Personal Use

Not part of the current build, but worth having on paper if it ever comes up:

- **Plaid Production approval**: the free Trial plan caps at 10 Items. Supporting other users means a full Production application — business verification, security review, and usage-based billing once you're past Trial/Limited Production.
- **Multi-user architecture**: per-user auth, data isolation, and likely a proper OAuth flow rather than a single hardcoded account.
- **Compliance considerations**: handling other people's bank data and betting activity brings in real obligations — financial data handling regulations, and (depending on how "betting" features are framed) potential gambling-related regulatory questions per state. This is a legal-review conversation, not a weekend feature add.
- **SharpSports/BetSync at scale**: at $500/mo it only makes sense once there's a user base to spread that cost across — worth reconsidering if this ever has real users.
- **Hosting cost model**: moves from a $5/mo VPS to something that scales with users — a deliberate re-architecture, not a config change.

None of this blocks anything in the personal build — it's here so a future "should we open this up" conversation starts from a plan instead of a scramble.
