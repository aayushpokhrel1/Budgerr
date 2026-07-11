# Budgerr — Architecture Plan

## 1. Goal

A personal-use app (web + your existing React Native shell) that:
- Connects your real bank accounts and categorizes spending automatically
- Tracks your bets/parlays across sportsbooks as a first-class budget category, not an afterthought
- Shows you a monthly betting allowance, net win/loss, and trend — alongside rent, groceries, etc.
- Ties into the basketball analytics dashboard so "tonight's slate + your remaining betting budget" is one glance, not three apps

Scope for now: **you, personally.** Section 12 covers what changes if this ever goes beyond that.

---

## 2. System Overview

```
Plaid (bank accounts) ──────┐
                             ├──> Backend (FastAPI) ──> PostgreSQL ──> Budgeting Engine ──> Dashboard (RN app / web)
Sportsbook CSV exports ──────┘         ▲
      (dropped in watch folder)        │
                                  Watcher/Parser
                                  (automated)
```

---

## 3. Data Layer

### 3.1 Bank data — Plaid
- Plaid Link handles the actual bank login; your app only ever sees a token, never your bank password
- Free Trial plan (accounts created after April 15, 2026): up to 10 linked Items with real production data, no cost — plenty for your own accounts
- Build against **Sandbox** (fake data) first, confirm the pipeline works end to end, then switch to real accounts
- Plaid's Transactions API + webhook keeps new transactions flowing in without polling

### 3.2 Betting data — CSV export + watch folder
- No live API for personal-scale bet syncing (SharpSports/BetSync exists but is $500/mo, business-tier)
- You manually export your bet/transaction history from each sportsbook (DraftKings: Account Center → Financial Center → Transaction History; FanDuel: History tab → Download as CSV) — a 15-second task, not worth automating around login credentials
- A watcher script monitors a local folder (or a simple upload endpoint) for new export files
- On file detection: identify the source sportsbook by column signature → normalize into your schema → load into Postgres
- Optional: an iOS/Android Shortcut that jumps straight to each sportsbook's export page, so the manual step is one tap

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

## 7. Backend / API

- Python, FastAPI
- Scheduled jobs (cron or a simple task queue): Plaid transaction sync, CSV watcher, alert threshold checks
- One internal API serving both the budgeting data and (optionally) the basketball model outputs to a shared frontend

## 8. Frontend

- Your existing React Native Android app is the natural home — add a Budget tab alongside the stats tab
- A second, lighter web view (Streamlit or a small Next.js page) is useful for anything you'd rather check on a laptop, e.g. reviewing a CSV import

---

## 9. Security & Non-Negotiables (personal use, still matters)

- HTTPS everywhere — no exceptions, even for a single-user app
- Plaid keys and DB credentials in environment variables/secrets, never committed to the repo
- Some form of auth on the app itself, even single-user — don't leave a bank-data endpoint open on the internet with no login
- Regular encrypted backups of the Postgres DB
- Keep dependencies patched — real bank data deserves real hygiene, hobby project or not

---

## 10. Deployment (Personal Use)

Two realistic options:

| Option | Notes |
|---|---|
| **Small cloud VPS** ($5–6/mo — DigitalOcean, Railway, Fly.io, Render) | Reachable from your phone anywhere, not just home wifi. Simplest for always-on scheduled jobs. |
| **Home server / Raspberry Pi + Tailscale** | Free, but only as reliable as your home internet/power. Tailscale gets you secure remote access without exposing anything publicly. |

**Mobile app**: since it's just for you, build the APK and side-load it directly onto your Android phone (enable "install from unknown sources"). No Play Store listing, no $25 developer fee, no review process — that's only needed if this ever goes public.

---

## 11. Build Order

1. Postgres schema (bank + betting + budgeting tables)
2. Plaid Sandbox integration → confirm pipeline → switch to real accounts
3. CSV watcher/parser for your sportsbooks (start with whichever you use most)
4. Categorization rules (betting merchant detection, net win/loss calc)
5. Budgeting engine (categories, limits, alerts)
6. Frontend tab in the RN app
7. Deploy to VPS or home server
8. (Optional) Wire in the basketball dashboard's tonight's-slate view

---

## 12. Future Plans — Beyond Personal Use

Not part of the current build, but worth having on paper if it ever comes up:

- **Plaid Production approval**: the free Trial plan caps at 10 Items. Supporting other users means a full Production application — business verification, security review, and usage-based billing once you're past Trial/Limited Production.
- **Multi-user architecture**: per-user auth, data isolation, and likely a proper OAuth flow rather than a single hardcoded account.
- **Compliance considerations**: handling other people's bank data and betting activity brings in real obligations — financial data handling regulations, and (depending on how "betting" features are framed) potential gambling-related regulatory questions per state. This is a legal-review conversation, not a weekend feature add.
- **SharpSports/BetSync at scale**: at $500/mo it only makes sense once there's a user base to spread that cost across — worth reconsidering if this ever has real users.
- **Hosting cost model**: moves from a $5/mo VPS to something that scales with users — a deliberate re-architecture, not a config change.

None of this blocks anything in the personal build — it's here so a future "should we open this up" conversation starts from a plan instead of a scramble.
