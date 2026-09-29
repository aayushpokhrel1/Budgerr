# Budgerr Product

Audience, scope, what is built, what is next, and what is deliberately not being
built. How it works is `docs/ARCHITECT.md`.

## 1. Who it is for

One person: the owner. That is not a placeholder for a real user base, it is the
design constraint that makes the whole thing tractable. Manual reward-rate upkeep,
a side-loaded APK, and a single hardcoded account are all reasonable at n=1 and all
break at n=2.

## 2. What it is for

A personal finance app that treats betting as a first-class budget category rather
than an embarrassing line item hidden in "Entertainment":

- Real bank accounts connected and spending categorized automatically.
- Bets and parlays tracked per leg, with net win and loss as its own trend.
- A monthly betting allowance sitting next to rent and groceries, in the same
  envelope-budgeting mechanism, with no special-casing.
- Tonight's slate and the remaining betting budget in **one glance**, not three
  apps. This was the original motivating idea and it is built.

A credit card rewards tracker rides along, because the transaction data needed to
answer "which card should I have used" is already flowing in.

## 3. The honest position on betting

This matters more than any feature and is easy to lose.

Budgerr consumes playstat, which measured its own model and found it **loses money**
(roughly -49% over 54 paper bets, and playstat's own MLB model measured around -57%
ROI). playstat responded by shelving the model, freezing those surfaces, and
pivoting to ranking on de-vigged market prices instead.

What that means for Budgerr as a product:

- Ranking and labelling use `joint_prob` and `market_prob`. **Never `model_prob`.**
- Higher-variance sections are styled to look higher-variance. The team-markets tier
  runs near a coin flip, so it gets amber and a non-green badge. A product that is
  structurally negative-EV can still be honest; one that dresses variance up as
  safety is the genuinely harmful version.
- The quarter-Kelly stake suggestion is **display-only guidance and should not be
  trusted** until calibration data validates the underlying probabilities.
- Paper bets exist so recommendations can be measured without risking money. That
  measurement is the point of the whole loop.

The value in this half of the app is the measuring, not the winning.

## 4. What is built

The original build order is complete: schema, Plaid pipeline, bet quick-entry,
categorization, budgeting engine, rewards tracker, mobile Budget tab, web mirror,
and the playstat tie-in. So is the layer after it:

- **Scheduled jobs** for Plaid sync and auto-settlement.
- **Multi-sport settlement**, reading any stat in playstat's per-player `stats` map,
  MLB hitter and pitcher props included, with an NBA fallback.
- **The Tonight glance view**, the original one-glance idea.
- **Bet performance analytics**, including actual hit rate against predicted
  probability in deciles, with a real and paper toggle on both clients.
- **Recurring-charge detection**, plus price-hike and annual-cadence detection.
- **Rotating-category reminders** via expiring rates, surfaced as dashboard banners.
  No push infrastructure, deliberately: it is a single-user app checked daily.
- **API auth**, per-consumer keys, with `/health` the only exempt route.
- **The playstat proxy**, keeping that key server-side.
- **Encrypted backups**, with the restore drill actually performed.
- **Push notifications** over ntfy for settled real bets, auto-log confirmations,
  and newly crossed budget thresholds.
- **CI** on all three repos, green.
- **Bankroll curve and drawdown**.
- **Bet-slip screenshot import**, backend and both clients, gated on
  `ANTHROPIC_API_KEY`.
- **The builder-parlay migration**: Tonight and quick-entry both read playstat's
  builder feed, and no frozen surface is touched at runtime.
- **Team-markets (NRFI/F5) tier** in Tonight, log-only.
- **The completed bet-leg model**: `game_id`, `player_id`, `market`.

## 5. Decisions worth not relitigating

**Manual bet entry, not scraping.** No sportsbook offers a real export of itemized
leg history, and the pattern holds across books. One manual flow beats a scraper per
book that breaks on every redesign.

**Two clients, no shared package.** They share no build, and keep a few pure helper
files byte-identical instead. A shared package for two consumers in two repos costs
more coordination than it saves.

**playstat stays a separate service with a separate database.** No shared Postgres,
no merged schema. Revisit only if it ever demonstrably matters.

**Manual reward-rate upkeep.** No issuer publishes rates as an API. A few updates a
year for a handful of cards is cheaper than maintaining a scraper, and the expiring-rate
banner keeps it from going stale.

**Paid API calls stay off by default.** `ANTHROPIC_API_KEY` is unset and the two
features that need it return 501 with a handled message. Cost is opt-in.

**No push infrastructure.** ntfy plus a subscribed phone covers it.

**Additive, nullable, no backfill** is the migration style, with a display fallback
so old rows render unchanged.

## 6. What is next

Roughly in value-per-effort order. Nothing here is started.

### Close the model loop

- **Repoint the auto-log job.** It is disabled because it pulled frozen surfaces.
  Pointing it at the builder feed restarts calibration data accruing with zero taps.
  This is the highest-value small item, since everything about measuring honestly
  depends on that data existing.
- **Closing-line value.** Blocked, and the block is a real one: playstat has **no
  closing line**. Its last snapshot lands roughly 100 minutes before first pitch, so
  it captures line *movement*, not a close. The join-key plumbing is shipped; the
  number stays dark until playstat finalizes a read surface, on a horizon they
  estimated at three to four weeks from 2026-08-13 and said they would flag. Do not
  name a field for a closing line that does not exist, and note the signal measured
  negative.
- **Team-market settlement.** On a documented hold. The structural blocker is gone
  (legs now carry `game_id` and `market`), so this needs `team_game_stats` keyed on
  game, market, and side. Worth building when playstat's team legs actually flow in
  volume, not before.
- **NFL readiness.** Effectively free: settlement reads any stat in the `stats` map,
  so NFL props need no Budgerr change. Sanity-check stat-type naming when playstat
  ships it.

### Get off the laptop

- **Deploy.** Design approved and every artifact authored, committed, and smoke-tested
  together: Dockerfile, compose, systemd timer pairs, and the runbook in
  `docs/DEPLOY.md`. The owner gave the go-ahead on 2026-09-29, so this is no longer
  owner-gated. The hardware question is dissolved rather than answered: the target is
  an Oracle Cloud Always Free ARM instance (4 OCPU, 24 GB), which has more CPU and
  roughly 3x the RAM of the Pi that prompted the retrain caveat, so the
  Pi-versus-laptop choice and the retrain measurement are no longer prerequisites for
  anything. Postgres is managed (Supabase) rather than a compose service, chosen partly
  so the box stays stateless and disposable. Budgerr deploys alone; playstat follows in
  a later session onto the same box, using compose services that are already authored.
  The old "both APIs move together" coupling is dropped, and that coupling is what
  stalled this for two months. The database starts **empty**, which is a product
  decision with visible user cost: every bank account must be re-linked, and
  transaction history, bet history and bankroll all start from zero on the cloud
  instance. Still pending: executing the runbook. Nothing is deployed.
- **Plaid webhooks.** Replace daily polling with `SYNC_UPDATES_AVAILABLE` once a
  public HTTPS URL exists, keeping the daily sync as a fallback sweep. Post-deploy
  by definition.

### Smarter money analysis

- **Sportsbook reconciliation.** Compare bank-side betting outflow against logged
  bets per book, and surface deposits with no corresponding bets: the bet log lying
  by omission. Needs merchant-to-sportsbook normalization, which the `is_betting`
  matcher already half has.
- **Cash-flow forecast.** Detect income via the same cadence machinery as recurring
  charges, then project each category's month-end position from run rate. Pure
  analysis, no schema change.
- **Turn on the Claude rate lookup.** Already built; set the key. Pairs with the
  expiring-rate banner at quarter rollover: banner fires, one click researches, confirm
  saves.
- **Card-aware "left on the table".** `credit_cards.linked_account_id` exists;
  finish the loop so the retrospective report uses the card *actually used* per
  transaction instead of assuming the optimal card was available. This is what makes
  the most under-appreciated feature in the app trustworthy.
- **Surface the price-hike fields.** The backend returns `cadence`, `price_hiked`,
  and the hike amounts; no client shows them yet. Also `monthly_estimate` is not
  amortized for annual charges.

### Quality of life

- **Monthly digest** on the first of the month over the ntfy channel.
- **Native Plaid Link on mobile**, closing the last web-only gap.
- **Biometric lock on mobile.** It is bank data on a side-loaded APK.
- **CSV export and a year-in-review.**
- **Expiring-rate push pings.** The data exists; only the wiring is missing.

### Ordering

Repoint auto-log, then deploy, then webhooks, then the analysis items.
Kelly sizing stays untrusted until calibration data validates the probabilities, and
closing-line value waits on playstat regardless of what else happens.

## 7. Beyond one person, and why not

Not being built. On paper so that a future "should this open up" conversation starts
from a plan rather than a scramble.

- **Plaid production approval.** The free Trial plan caps at 10 Items. More users
  means a full production application: business verification, security review, and
  usage-based billing.
- **Multi-user architecture.** Per-user auth, data isolation, and a real OAuth flow
  instead of one hardcoded account.
- **Compliance.** Holding other people's bank data and betting activity brings real
  obligations: financial data handling rules, and depending on how the betting
  features are framed, gambling-related questions that vary by state. That is a
  legal review, not a weekend feature.
- **Paid bet-sync services** at around $500 a month only make sense spread across a
  user base.
- **Hosting** moves from a $5 VPS to something that scales with users, which is a
  deliberate re-architecture rather than a config change.

None of this blocks anything in the personal build.
