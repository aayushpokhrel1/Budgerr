# Budgerr Architecture

The system across all three repos. Product decisions and the roadmap live in
`PRODUCT.md`; running and verifying it lives in `docs/OPERATIONS.md`; deploying it
lives in `docs/DEPLOY.md`.

## 1. System overview

```
Plaid (bank accounts) ──────┐
                            ├──> Backend (FastAPI) ──> PostgreSQL ──> Budgeting engine ──> clients
Manual quick-entry ─────────┘                              ^
   (bet placed -> logged in-app,                           │
    settled -> won/lost/push, or auto-settled)             │
                                                  playstat API (separate service)
```

Three repos, one backend, two clients:

| Repo | Role |
| --- | --- |
| `Budgerr` (this one) | FastAPI backend in `backend/`, PostgreSQL, the cross-repo docs |
| [`budgerr-app`](https://github.com/aayushpokhrel1/budgerr-app) | Expo / React Native client |
| [`budgerr-web`](https://github.com/aayushpokhrel1/budgerr-web) | Next.js client |

[`playstat`](https://github.com/aayushpokhrel1/Playstat) is a **fourth, separate
project** with its own backend and its own Postgres. No shared database, no merged
schema. It is read-only from Budgerr's side.

### Client symmetry

The two clients share no code and no build. They deliberately keep some files
**byte-identical** instead, most importantly `lib/builderParlays.ts` and
`lib/kelly.ts`: pure helpers ported verbatim across repos. A change to one is a
change to both, in two commits in two repos. Checking that they are still identical
is part of the operations checklist.

## 2. Data layer

### 2.1 Bank data, via Plaid

- Plaid Link handles the bank login. The app only ever holds a token, never a bank
  password.
- Plaid's Transactions API plus a webhook keeps transactions flowing without polling.
  The webhook is not wired yet (see `PRODUCT.md`); a daily sync stands in.
- Institution names resolve through `/institutions/get_by_id` at link time, so an
  account shows as "American Express" rather than `ins_10`.
- `GET /plaid/accounts` and `GET /plaid/transactions` expose what has synced. Both
  clients have Accounts and Transactions screens (web `/accounts`, `/transactions`;
  mobile two tabs).
- `PATCH /plaid/transactions/{txn_id}` sets `custom_category`. Syncing also
  best-effort auto-categorizes by matching Plaid's own category string against
  category names, and never overrides a manual choice.
- Every sync, categorization change, and dashboard load recomputes `budget_periods`
  for the affected months, so a category created now shows spent and remaining
  immediately rather than waiting for some later trigger to create its period row.

**Bank linking is web-only.** Mobile would need the native Plaid Link SDK, a
separate and much bigger lift. Link on web; linked accounts appear on mobile
because both clients share the backend.

### 2.2 Betting data, via manual quick-entry

No major sportsbook (DraftKings, FanDuel, bet365, and others all behave the same
way) offers a reliable export of itemized bet and leg history. What exists is a
viewable P/L or transaction page, not structured data. Rather than build and
maintain a scraper per book, one manual quick-entry flow covers all of them.

- One entry screen, whatever book the bet was placed on. Fields: sportsbook, bet
  type (`single` or `parlay`), stake, potential payout, and per-leg detail.
- Target is under 15 seconds per bet, so legs pre-fill from playstat's builder feed
  rather than being typed by hand.
- **Settlement** is automatic where it can be: `POST /bets/auto-settle` matches
  pending `bet_legs` against playstat's `GET /box-scores?date=` (final games only,
  `games.status = 'FT'`) on the bet's `placed_at` date and `player_name`. A leg
  resolves if its `stat_type` appears in that player's `stats` map, falling back to
  the legacy top-level `points` / `rebounds` / `assists` fields for NBA rows. A
  parlay wins only if every leg wins; any loss fails the whole bet; a push-only
  combination pushes. Anything unresolvable stays pending and is retried next run.
  `PATCH /bets/{bet_id}/settle` remains for the rest.
- **Paper bets**: `bets.is_paper` logs a hypothetical stake and payout. It
  auto-settles exactly like a real bet but is excluded from real-money P/L in
  `GET /bets/trend`, so recommendations can be tracked without risking money.
- Net betting cash flow (deposits and withdrawals) still comes from the bank side
  via Plaid regardless of book. Quick-entry only changes how `bets` and `bet_legs`
  get populated, never the schema below.

### 2.3 Schema

```sql
-- Bank side
accounts(account_id, plaid_item_id, institution_name, account_type, mask, current_balance)
transactions(txn_id, account_id, date, amount, merchant_name, plaid_category,
             custom_category, is_betting)

-- Betting side
bets(bet_id, sportsbook, placed_at, bet_type,   -- 'single' | 'parlay'
     stake, potential_payout, status, settled_at, net_result,
     is_paper, external_ref)
bet_legs(leg_id, bet_id, player_name, stat_type, line_value, side, odds, leg_status,
         model_prob,
         game_id, player_id, market)            -- all three nullable

-- Budgeting
categories(category_id, name, monthly_limit, is_betting_category)
budget_periods(period_id, category_id, month, spent, limit, remaining)
alerts(alert_id, category_id, month, threshold_pct, triggered_at, message)

-- Credit card rewards
credit_cards(card_id, name, issuer, nickname, linked_account_id)
card_reward_rates(rate_id, card_id, category_id, multiplier,
                  cap_amount, cap_period,          -- 'quarterly' | 'annual' | null
                  effective_start, effective_end)  -- rotating categories
card_reward_progress(card_id, category_id, period_start, period_end,
                     amount_spent_at_bonus_rate)
```

`is_betting` on `transactions` is set by merchant-name matching (DraftKings,
FanDuel, BetMGM, Caesars, ESPN Bet, and similar) so bank-side betting flow and
sportsbook-side bet detail can be reconciled against each other.

Rewards rows reference `categories`, so rewards need no second categorization
system.

#### The three nullable leg columns

`bet_legs.game_id`, `player_id`, and `market` were added by migration
`7161fe789272` as **additive and nullable, with no backfill**. They are join keys,
not features:

- Player legs carry `(game_id, player_id, stat_type, line_value)`.
- Team legs carry `(game_id, market, line_value)` plus side, where `market` is
  `first_inning_runs` or `f5_runs`.

They exist to unblock two things that are **not built**: closing-line value, and
team-market settlement. Read `PRODUCT.md` before building on them, because the
reason they are dark is a deliberate decision, not an omission.

Two consequences worth knowing before touching leg code:

- Team legs store a clean matchup in `player_name` and leave `stat_type` **null**.
  Nulling `stat_type` is what keeps team legs out of auto-settle, and a test depends
  on that. It is load-bearing, not incidental.
- Display reads `betLegMarketLabel(leg.market) ?? leg.stat_type`, so legacy
  pre-migration rows (`market` null) render exactly as before. That fallback is why
  no backfill was needed.

## 3. Categorization and betting detection

- Plaid provides category enrichment; a merchant-name rule set layers on top
  specifically for sportsbooks.
- Net betting spend is deposits minus withdrawals, not gross deposits. A $200
  deposit that returns $150 is not a $200 month.
- `bets` and `bet_legs` answer "what did I bet on"; `transactions` answers "what did
  it cost". Both matter, for different questions.

## 4. Budgeting engine

- Envelope budgeting: a monthly limit per category, rolling spent and remaining.
- Betting is just another category with a limit. The engine special-cases nothing.
- Alert thresholds (80%, 100%) fire during the budget recompute, so they need no
  separate job. Only newly created alerts notify, which is what keeps it to one ping
  per threshold crossing rather than one per sync.
- Trend view compares betting against income and other discretionary categories
  month over month.

## 5. playstat integration

playstat stays a separate backend and a separate Postgres. Budgerr consumes it over
HTTP through a proxy:

- `GET /playstat/{path}` (`app/routers/playstat_proxy.py`) is a thin catch-all
  passthrough. It forwards query params, injects `X-API-Key` **server-side**, passes
  the upstream status through, and returns 502 when playstat is unreachable.
- Both clients point their playstat base URL at `<backend>/playstat`. The playstat
  key therefore never reaches a client, and playstat needs no CORS configuration for
  a browser client.

### Which playstat surfaces are live

This is the part most likely to mislead a reader, so it is stated once, here.
playstat shelved its MLB model, and these surfaces are **frozen**: they keep serving
the last computed rows and never update.

| Surface | State | Budgerr's use |
| --- | --- | --- |
| `GET /parlay-builder/saved?limit=&tier=` | live, the only ranked source | Tonight, quick-entry |
| `GET /games?date=&sport=` | live | slate, matchup resolution |
| `GET /box-scores?date=` | live | auto-settlement |
| `GET /edges` | **frozen** | none at runtime, type kept |
| `GET /game-predictions` | **frozen** | none at runtime, type kept |
| `GET /parlay-recommendations` | **frozen**, winding down | none at runtime |

Budgerr's runtime touches no frozen surface. The `playstatApi.edges` and
`gamePredictions` methods and their TS types are **kept deliberately** so the
contract stays intact and additive, while the hooks that called them were deleted.
An unused method there is not dead code to tidy away.

Ranking and labels use de-vigged `joint_prob` and `market_prob`, and **never
`model_prob`**, which is context-only and frequently null. This is the single most
important rule in the integration: the model-ranked number is the thing playstat
measured at a loss and abandoned.

### Tonight

Tonight fetches the combined builder feed **once** (`listBuilder(100, 'all')`) and
partitions it client-side by `hasTeamLeg`:

- Player constructions feed "Low-risk builder parlays".
- Team constructions feed "Team markets (NRFI/F5), higher variance", styled amber
  with a deliberately non-green badge so higher variance does not read as safe.

Each section resolves games from **its own run's date**, because the player run and
the team run are usually different days, and each hides fully-past runs
independently. An empty team section is a normal state (team markets price near a
coin flip and often do not clear playstat's floor), so it renders as a calm message
rather than an error.

Slate cards show the builder's player-prop legs per game, suppressing legs already
shown in the parlay section above, plus a first-inning line from team legs. Coverage
is intentionally sparse: the builder is a curated short list, so most games show
nothing.

## 6. Credit card rewards

No issuer publishes reward rates as a structured API, so this is a
manually-maintained dataset. That is acceptable for one person's handful of cards,
updated a few times a year.

**Proactive, "which card right now":** pick a category, get the card with the best
*currently active* multiplier, after checking `card_reward_progress` against
`cap_amount`. A card whose bonus category is already capped for the period drops
out, so the next-best surfaces instead. Without that check the answer keeps being a
card earning 1% because its 5% cap was already hit.

**Retrospective, "rewards left on the table":** every transaction is already flowing
in and categorized, so comparing the card used against the optimal card is nearly
free, and rolls up into a trend. Consumer reward apps cannot do this, because they
do not hold real transaction data.

`GET /rewards/expiring-rates?within_days=45` surfaces rates whose `effective_end`
falls in `[today - 7, today + within_days]`, which is what keeps rotating categories
from going stale. `card_reward_progress` resets at each `cap_period` boundary.

**Automated rate lookup is built and switched off.**
`POST /rewards/cards/{card_id}/fetch-rates` asks Claude (with web search) to
research a named card and propose structured rates, saving nothing.
`POST /rewards/cards/{card_id}/reward-rates/confirm` saves a reviewed version,
auto-creating any missing category. Code is `app/rewards_lookup.py` and
`app/routers/rewards.py`. It returns 501 until `ANTHROPIC_API_KEY` is set, because
each lookup costs money.

## 7. Backend and API

Python, FastAPI, PostgreSQL, alembic for migrations.

One API serves both the budgeting data and, through the proxy, playstat's outputs.
Notable endpoint groups beyond those already mentioned:

- `GET /bets/analytics?scope=real|paper`: ROI by sportsbook, bet type, and stat
  type, plus decile-bucketed actual hit rate against predicted probability.
- `GET /bets/bankroll?scope=real|paper`: cumulative P/L over settled bets, max
  drawdown, longest losing streak.
- `POST /bets/auto-log-recommendations`: logs playstat parlays as paper bets,
  deduped by `bets.external_ref`. **Its source is a frozen surface**, so the job
  that called it is disabled. See `PRODUCT.md`.
- `POST /bets/parse-slip` (`app/routers/bet_import.py`, parsing in
  `app/slip_parser.py`): sends a sportsbook screenshot to Claude vision and returns
  structured fields, saving nothing. The result pre-fills quick-entry for human
  review and never auto-submits. Returns 501 without `ANTHROPIC_API_KEY`.
- `GET /plaid/recurring-charges`: groups transactions by normalized merchant,
  greedily clusters by amount (within 10% of the cluster's running median), and
  flags a cluster as recurring at 3 or more occurrences with a 20 to 40 day median
  gap. It also detects a roughly 365-day annual cadence and flags upward price
  trends, adding `cadence`, `price_hiked`, `price_hike_amount`, `price_hike_pct`.
  Detection is pure and lives in `app/recurring.py`, unit-tested without a database;
  the endpoint queries once and delegates. Note `monthly_estimate` still returns
  `avg_amount` and is **not** amortized for annual charges.

**Structure rule worth keeping:** `app/recurring.py` and `app/slip_parser.py` are
pure logic behind thin routers, which is exactly why they are testable without a
database or an API key. New analysis belongs in that shape.

`app/notify.py` is a best-effort ntfy poster: a no-op when `NTFY_TOPIC` is unset,
and it never raises, so it cannot break the request that triggered it. It is wired
into auto-settle (real bets only), auto-log confirmations, and newly fired budget
alerts.

## 8. Clients

Both are full clients against the same backend, not a viewer and an owner.

**Mobile** (`budgerr-app`): Expo, TypeScript, Expo Router, React Query. The Budget
tab is the primary day-to-day surface, because logging a bet happens in the moment.
Distributed as a side-loaded APK, so no store listing.

**Web** (`budgerr-web`): Next.js App Router, TypeScript, Tailwind, React Query. A
full mirror: dashboard, bets, rewards, categories, analytics, and the Plaid Link
flow. Better for longer bet history, editing reward rates, and deeper trends.

Both attach `X-API-Key` from their environment (`NEXT_PUBLIC_BUDGERR_API_KEY`,
`EXPO_PUBLIC_BUDGERR_API_KEY`) to every backend and `/playstat/*` call.

A quarter-Kelly stake suggestion appears on Tonight parlay cards, computed
client-side in `lib/kelly.ts` from `joint_prob` and combined odds against the
remaining betting budget, and rendered only when positive. It is display-only
guidance and should stay untrusted until calibration data validates the underlying
probabilities.

## 9. Security

Single-user does not mean unsecured. These are non-negotiable:

- HTTPS everywhere, no exceptions.
- Plaid keys and database credentials in environment variables, never committed.
- Auth on the API itself. `app/auth.py` applies a global
  `Depends(require_api_key)`, with an `AUTH_ENABLED` kill-switch and
  `BUDGERR_API_KEYS` as comma-separated `name:key` pairs compared with
  `secrets.compare_digest`. Keys are per consumer (`web`, `mobile`, `cron`) so any
  one rotates alone.
- **`/health` is the only auth-exempt path**, declared as
  `AUTH_EXEMPT_PATHS = frozenset({"/health"})`, because container and systemd
  healthchecks need it. FastAPI's generated `/docs`, `/redoc`, and `/openapi.json`
  were once exempt by accident and leaked the schema; the app is now constructed
  with `docs_url=redoc_url=openapi_url=None` and re-adds those three as ordinary
  key-gated routes. Do not reintroduce them by passing those kwargs back.
- Encrypted, tested backups. See `docs/OPERATIONS.md`.
- Keep dependencies patched. Real bank data deserves real hygiene.

## 10. Scope

One person, by design. `PRODUCT.md` records what would have to change for that to
stop being true, and why none of it is being built.
