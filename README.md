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
- **Kelly-style stake sizing — done (web + mobile)** — ¼-Kelly suggestion shown on the Tonight parlay cards, computed client-side (`lib/kelly.ts`, ported verbatim to both clients) from `joint_prob` + combined odds against the remaining betting budget; the mobile `ParlayCard` reuses the remaining-budget already fetched for the paper-bet flow and only renders when the suggestion is positive. Display-only guidance; treat with suspicion until the calibration view validates the model's probabilities.
- **NFL-readiness** *(free when playstat ships it)* — settlement reads any stat in the `/box-scores` `stats` map, so NFL props (playstat README §13) need zero Budgerr changes; just sanity-check stat-type naming when the time comes.
- **Bet-slip screenshot import** — backend **done**: `POST /bets/parse-slip` (router `app/routers/bet_import.py`, extraction/parsing logic unit-tested in `app/slip_parser.py`) sends a sportsbook screenshot to Claude (vision, claude-sonnet-5) and returns structured bet fields — nothing is saved; the result pre-fills the quick-entry form for human review. Returns 501 until `ANTHROPIC_API_KEY` is set, same pattern as Section 7.4. Frontend upload UI: **done on both clients** — web `components/bets/BetForm.tsx` (file input) and mobile `app/modal.tsx` (via `expo-image-picker`); both merge the parsed fields into the quick-entry form for human review, never auto-submit, and show a "needs ANTHROPIC_API_KEY" message on 501.
- **Closing-line value (CLV)** *(blocked on playstat)* — compare odds taken vs. the closing line, the sharpest long-term edge signal. Needs playstat to store closing lines first; design it from that side.

### 15.2 Tier B — Get off the laptop

- **Auth on the API — done and enforced (2026-07-15)**: `app/auth.py` mirrors playstat exactly — a global `Depends(require_api_key)` on the FastAPI app, `AUTH_ENABLED` kill-switch, `BUDGERR_API_KEYS` as comma-separated `name:key` pairs, constant-time `secrets.compare_digest`, no-op when disabled. Live keys are per-consumer in `backend/.env` (`web`, `mobile`, `cron`) so any one rotates independently. Both clients attach `X-API-Key` from their env (`NEXT_PUBLIC_BUDGERR_API_KEY` / `EXPO_PUBLIC_BUDGERR_API_KEY`) on every backend + `/playstat/*` call; the three launchd curl jobs send the `cron` key. **Every route now requires the key, including `/docs`, `/openapi.json`, and `/playstat/*`** — to disable, set `AUTH_ENABLED=false` in `backend/.env` and restart. CORS already handled the custom-header preflight (`allow_headers=["*"]`, credentials off). **Correction (deployment prep, 2026-07-17):** the claim above was *aspirational* when first written — FastAPI's auto-generated `/docs`/`/openapi.json`/`/redoc` were actually *bypassing* the app-level `Depends(require_api_key)` and returned 200 with no key (a real schema leak, latent because the API wasn't yet public). Fixed by constructing the app with `docs_url/redoc_url/openapi_url=None` and re-adding those three as normal key-gated routes; `/health` is now the single deliberate auth-exempt path (container/systemd healthchecks need it). `app/auth.py` exposes `AUTH_EXEMPT_PATHS = frozenset({"/health"})`. Verified in-container during the full-stack smoke: `/health`→200, `/openapi.json` no key→401.
- **Proxy playstat through the Budgerr backend — done**: a thin catch-all `GET /playstat/{path}` passthrough (`app/routers/playstat_proxy.py`) forwards query params to playstat and injects the `X-API-Key` server-side (reusing `settings.playstat_api_key`); passes upstream status through, returns 502 if playstat is unreachable. Both frontends now default their playstat base URL to `<backend>/playstat` (`lib/playstat.ts`, reusing the existing backend API-URL env), so the playstat key never touches the client and playstat's CORS requirement is gone. The proxy is now behind the Budgerr auth item above (both frontends attach the Budgerr `X-API-Key` to `/playstat/*` too).
- **Encrypted Postgres backups — done (2026-07-15)**: `backend/ops/backup.sh` runs `pg_dump -Fc` from the Docker Postgres, encrypts with `age` (public recipient in `~/.config/budgerr/backup-age.pub`; private identity `~/.config/budgerr/backup-age.key`, mode 600, kept off the backup location — **losing it makes every backup unrecoverable**). Writes an authoritative local copy to `~/Budgerr-Backups/` (atomic + newest-14 retention) and a best-effort off-machine copy to iCloud Drive. Scheduled via launchd `com.budgerr.backup` nightly 3:00am. macOS gotcha: a launchd process can *create* files in iCloud but not `rename`/`unlink` them without Full Disk Access — hence the local-authoritative + create-only-iCloud split. Restore procedure + the safe scratch-DB restore drill (performed once, row counts matched live) are in `backend/ops/restore.md`.
- **Deploy** — **design approved 2026-07-16; artifact prep COMPLETE 2026-07-17 on branch `deploy/prep`** (spec: `docs/superpowers/specs/2026-07-16-deployment-design.md`; runbook: `docs/DEPLOY.md`). Committed this pass: the `/health` auth-exemption + docs-gating fix above; `backend/Dockerfile` + `.dockerignore` (multi-arch, **editable** `pip install -e .` so `app.main`'s `../static` mount resolves); `deploy/docker-compose.yml` (four services, two separate Postgres) + `deploy/.env.example`; `deploy/systemd/` (four `.timer`/`.service` pairs + README, morning jobs `After=playstat-mlb.service`, times shifted 09:15/09:45); `backend/ops/backup.sh` made env-overridable so it runs unchanged on macOS *and* on the Linux box; and `docs/DEPLOY.md`. **Full-stack smoke passed** (first time all four services built + ran together — playstat-api via their Dockerfile): budgerr reachable + auth-gated, playstat internal-only (no host port), and the cross-service proxy `budgerr-api → http://playstat-api:8000/health` returns 200. **Still owner-gated — nothing is deployed, no hardware, no data migrated.** Hardware **deferred — design for both** (Raspberry Pi 5 8GB ARM64 *or* old laptop → Ubuntu x86_64; multi-arch images run on either; owner picks the box after seeing the Pi's real XGBoost-retrain time, which is materially slower on the Pi). One combined `deploy/docker-compose.yml`: `budgerr-api` + its Postgres + `playstat-api` + its **separate** Postgres (701 MB), playstat consumed only server-side over the internal network. **`budgerr-web` is public (Vercel)**, so the Budgerr API is exposed via **Tailscale Funnel** (public HTTPS, key-gated); **playstat stays internal-only** (zero public surface, key never leaves the box); the phone uses the Funnel URL (no Tailscale app needed). launchd → **systemd timers**, with the key ordering win that Budgerr's auto-settle/auto-log run `After=` playstat's morning retrain instead of racing it. Migrate both Postgres volumes with existing dump/restore tooling. playstat (separate session, never modified here) authors its own Dockerfile + auth-exempt `/health` at its deploy green-light; its migration `005` merges ~7/18. **Deployment is gated on the owner** — this pass only authors + commits artifacts; nothing goes live.
- **Push notifications — done (2026-07-15, ntfy.sh)**: `app/notify.py` is a best-effort `httpx.post` helper (no-op when `NTFY_TOPIC` is unset, never raises so it can't break the triggering request). Wired into three places: auto-settle (per newly-settled *real* bet — paper bets excluded), auto-log confirmations, and the budget recompute's `alerts_fired` (only newly-created 80%/100% alerts, so one ping per threshold crossing). Config `NTFY_BASE_URL`/`NTFY_TOPIC`; the live topic is in `backend/.env` — **subscribe to it in the ntfy app on the phone** to receive them. Verified end-to-end (message round-tripped through ntfy). Not yet wired: expiring-rotating-category pings (the data exists via `/rewards/expiring-rates`; add later if wanted).
- **Plaid webhooks** — replace the daily 7am polling sync with `SYNC_UPDATES_AVAILABLE` webhooks once a public HTTPS URL exists (post-deployment). Keep the daily sync as a fallback sweep.
- **CI — done and green (2026-07-16)**: GitHub Actions on all three repos, running on push to `main` and on PRs. Backend `pip install -e .[dev]` + pytest (editable install is required — `app.main` mounts `StaticFiles` from `../static`, which only exists in the source tree, not a copied site-packages install; DB-independent, no Postgres service needed). Web `npm ci` + `npm run build`; mobile `npm ci` + `tsc --noEmit`. All three verified passing on GitHub's runners. (Pushing workflows needed the `workflow` OAuth scope on the git token — `gh auth refresh -s workflow` — now granted.)

### 15.3 Tier C — Smarter money analysis

- **Price-hike + annual-subscription detection — done (2026-07-15)**: `app/recurring.py` now flags clusters whose amounts trend upward (first-third vs last-third mean beyond the 10% tolerance) and detects a ~365-day annual cadence alongside monthly. `/plaid/recurring-charges` gained additive fields `cadence` (`"monthly"`|`"annual"`), `price_hiked`, `price_hike_amount`, `price_hike_pct`. Unit-tested in `test_recurring.py`. (Note: `monthly_estimate` still returns `avg_amount` — not amortized for annual charges; revisit if a dashboard wants a normalized monthly figure.) Frontends don't surface the new fields yet.
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

The 15.6 loose ends are now cleared (playstat key wired, proxy shipped, both frontends' Tier A UI complete, model loop verified end-to-end). Next: auth + backups (small, overdue) → deploy → push notifications → webhooks + price-hike detection + reconciliation → Kelly sizing trusted only after calibration data validates `model_prob`.

### 15.6 State of play & immediate loose ends (as of 2026-07-15)

Snapshot for whoever picks this up next — the working set is three repos (`~/dev/Budgerr` backend, `~/dev/budgerr-web`, `~/dev/BudgerrApp`), all on clean pushed `main`; backend suite is **74 passing tests** (53 + 6 playstat proxy + 4 auth + 3 notify + 8 recurring/price-hike).

**Resolved this session — the playstat-auth blocker and the Tier A frontend loose ends are all cleared and verified end-to-end:**
  - **playstat API key wired.** `PLAYSTAT_API_KEY` is set in `backend/.env` (owner provisioned the `budgerr` key inside playstat's `PLAYSTAT_API_KEYS`). Verified: `GET /playstat/edges` and `/games` return 200 through the backend proxy (401 without the key), auto-settle/auto-log now get real data.
  - **Backend `/playstat/*` proxy shipped** (§15.2): catch-all passthrough, key injected server-side, both frontends repointed at `<backend>/playstat`. Now behind the Budgerr auth item below.
  - **API auth built and enforced** (§15.2 Tier B): global `X-API-Key` dependency, `AUTH_ENABLED=true` + per-consumer `BUDGERR_API_KEYS` (web/mobile/cron) live in `backend/.env`; both frontends attach their key, the three launchd curl jobs send the `cron` key. Verified end-to-end: unauth'd → 401 (all routes incl. `/playstat/*` and `/docs`), each key → 200, CORS preflight for the custom header passes, the web Tonight view renders through the authed proxy, and the `auto-settle` launchd job runs clean with its key.
  - **Web screenshot-import UI** in `components/bets/BetForm.tsx` — file input + merge-into-form-for-review (never auto-submit); backend `POST /bets/parse-slip` verified returning 501 "ANTHROPIC_API_KEY is not configured" (the state the UI handles).
  - **Mobile ¼-Kelly + screenshot import** — `lib/kelly.ts` ported, ¼-Kelly line on `components/tonight/ParlayCard.tsx`, `expo-image-picker` screenshot flow in `app/modal.tsx`. Verified statically (`tsc --noEmit` clean); **no simulator available, so no runtime check on mobile.**
  - **`POST /bets/auto-log-recommendations` verified end-to-end**: first call logged 10 paper parlays (bet_ids 20–29), second call logged 0 with `skipped_existing: 10` (external_ref dedup works). `POST /bets/auto-settle` correctly no-ops (no final games yet). The 9:00am launchd run this morning logged nothing because the key wasn't set yet, so the manual call above was the correct same-day catch-up — those 10 paper rows are real calibration data, keep them.

**Also shipped later this session (autonomous run):**
  - **Push notifications (ntfy)** — §15.2; live-verified round-trip. Live topic in `backend/.env` (`NTFY_TOPIC`); subscribe to it in the ntfy app to receive settlement/alert/auto-log pings.
  - **Price-hike + annual-subscription detection** — §15.3; additive fields on `/plaid/recurring-charges`, verified live.
  - **CI workflows — done and green** — §15.2; GitHub Actions on all three repos, all passing on GitHub's runners. Also added `backend/tests/conftest.py` pinning auth off as the test baseline (the live `AUTH_ENABLED=true` in `.env` had started 401-ing the TestClient route tests), and CI required an editable install so `app.main` can mount `../static`.

**iPhone runtime test — blocked by Expo's frozen App Store Expo Go, not by our code (2026-07-16):** First attempt to run `BudgerrApp` on the owner's physical iPhone via Expo Go failed with *"Project is incompatible with this version of Expo Go … requires a newer version."* Root cause: the app is on **Expo SDK 57** (`expo ~57.0.4`, `react-native 0.86.0`, `react 19.2.3`), but the **App Store Expo Go is frozen at SDK 54** (v54.0.2, released 2025-09-23 — confirmed via `itunes.apple.com/lookup?bundleId=host.exp.Exponent`). Per Expo's [May 2026 policy change](https://expo.dev/changelog/expo-go-and-app-store-may-2026), they no longer regularly ship Expo Go SDK updates to the App Store (as of May 2026 even SDK 55 was still unapproved with no timeline) and now steer real apps to dev builds — **"free testing on physical iPhones for newer SDKs is no longer available."** So "wait for the App Store" is effectively indefinite, not a viable plan. **Our config is correct and verified** — the phone path (`http://192.168.1.31:8001/health` with the mobile `X-API-Key`) returns `200`, LAN IP matches `EXPO_PUBLIC_API_URL`, and the app's key matches the backend's `mobile:` key; the block is purely SDK-version. Free physical-device paths all require **full Xcode** (this Mac has Command Line Tools only): (a) local dev build `npx expo run:ios --device` signed with a **free** personal Apple ID (7-day cert, re-sign weekly), or (b) full Xcode → iOS **Simulator** (not the physical phone). The clean paid path is an **Apple Developer account ($99/yr)** → `eas go` custom Expo Go via TestFlight — and that same account is needed for the eventual standalone TestFlight release anyway, so it double-counts. Throwaway-downgrading the project to SDK 54 to match frozen Expo Go is possible but a three-version regression on an app that's never run — not recommended. **Decision (2026-07-16): defer the mobile runtime test to the deploy/TestFlight milestone** — that milestone needs the $99 Apple Developer account anyway, at which point `eas go` (or the standalone build) runs SDK 57 on the physical iPhone. No mobile code changes needed; the network config is already verified. Pivoted to deployment prep instead.

**Deploy — artifact prep COMPLETE (2026-07-17), on branch `deploy/prep`.** Full design in `docs/superpowers/specs/2026-07-16-deployment-design.md`; the followable runbook is now `docs/DEPLOY.md`; see §15.2's Deploy bullet for the committed-artifact list. Hardware **still deferred (design for both)** because playstat's daily XGBoost retrain (~1M rows) is materially slower on a Pi 5 than the x86 laptop — owner picks the box after seeing real retrain time on the target (playstat's architect independently wants the same timing before committing). playstat coordination **landed** (their commit `82de4db`): they authored their own Dockerfile (python:3.11-slim + libgomp1 for xgboost's runtime OpenMP link; amd64 build-verified, arm64 expected-good) and an auth-exempt `/health` ({"status":"ok","database":"ok"}, 503 if DB down). One image serves both their API and their daily `mlb` batch chain as different commands; their Postgres 14.22 (~701 MB, migration `005` merges ~7/18 — don't snapshot before it lands) needs a real dump/restore, not a fresh DB; `API_BASKETBALL_KEY` is required at import. **They want to review our `playstat-*` compose blocks before anything goes live** (their preferred timing ~7/18+, after their merges + a few days of settled paper results). A **full-stack smoke** (all four services, first time playstat-api built alongside us) verified the topology end-to-end: budgerr auth-gated + reachable, playstat internal-only, cross-service proxy 200. **Remaining before this is "done": merge `deploy/prep` → `main` + push (architect), then — at the owner's green-light only — execute `docs/DEPLOY.md`.** **Prep-only; deployment is gated on the owner — nothing goes live, no hardware, no data migrated.** Auth and encrypted backups are done — the two Section 10 non-negotiables are met. Backend suite is **76 passing** on `deploy/prep` (74 on `main` + 2 new `/health`-exemption/docs-gating tests).

**Standing environment facts:** backend runs as launchd service `com.budgerr.backend` on port **8001** (playstat is 8000) — restart with `launchctl kickstart -k gui/$(id -u)/com.budgerr.backend`. Other launchd jobs: `plaid-sync` 7:00am, `auto-settle` 8:30am, `auto-log-parlays` 9:00am, `backup` 3:00am (`backend/ops/backup.sh` → `~/Budgerr-Backups` + iCloud). The three curl jobs now send `-H "X-API-Key: <cron key>"` in their plists (`~/Library/LaunchAgents`, not in git), so if you rotate the `cron` key in `BUDGERR_API_KEYS` you must update those three plists too. **API auth is enforced**: every request needs a valid `X-API-Key` (keys in `backend/.env` `BUDGERR_API_KEYS`; frontends read `NEXT_PUBLIC_/EXPO_PUBLIC_BUDGERR_API_KEY` from their gitignored env files); flip `AUTH_ENABLED=false` + restart to disable. Push notifications go to ntfy: `NTFY_TOPIC` in `backend/.env` (subscribe to it in the ntfy app to receive them); `notify()` is a no-op if that's unset. Postgres runs in Docker (port 5433); Docker Desktop `AutoStart` was enabled 2026-07-14 but is **not reliable** — the daemon went down on its own mid-session 2026-07-15 (every DB route 500s until `open -a Docker && docker compose up -d`), so treat a wave of 500s as "Docker died" first. `ANTHROPIC_API_KEY` is still unset, so `POST /bets/parse-slip` and §7.4 rate lookup return 501 by design. Pending paper parlays in the DB include the original Rocchio/Gasper (7/17 games) plus the 10 auto-logged 7/15 — all legitimate, do not delete.
