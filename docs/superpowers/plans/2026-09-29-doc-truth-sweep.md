# Plan: make PRODUCT.md, ARCHITECT.md and OPERATIONS.md true

Executes items 5, 6 and 7 of
[`../specs/2026-09-29-cloud-deploy-design.md`](../specs/2026-09-29-cloud-deploy-design.md) §7.
Read that spec first for the reasoning. Read `docs/DEPLOY.md` for the runbook the
decisions now live in.

**The single most important constraint: nothing is deployed yet.** The design is
chosen, the code is changed and the runbook is rewritten, but no Oracle instance
exists, no Supabase database has been created, and no data has moved. Do not
write a single sentence that implies otherwise. Describe decisions as decided and
work as pending, never as shipped.

Edit exactly three files: `PRODUCT.md`, `docs/ARCHITECT.md`, `docs/OPERATIONS.md`.
Touch nothing else. Match each file's existing voice and heading style. Correct
stale claims **where they sit** rather than appending a newer note underneath,
because the next reader hits the old line first. **No em dashes or en dashes
anywhere; use a comma, colon, parentheses, or two sentences.**

## 1. `PRODUCT.md`, the "Get off the laptop" section

Its **Deploy** bullet is now substantially false. It currently says the work is
"**Gated on the owner**, who picks the box (Raspberry Pi 5 8GB, or an old laptop
on Ubuntu) after seeing the Pi's real retrain time", and ends "Nothing is
deployed, no hardware, no data migrated."

Rewrite that bullet to say, in the file's own compressed style:

- The owner gave the go-ahead on 2026-09-29, so it is no longer owner-gated.
- The hardware choice is resolved by dissolving it rather than answering it: the
  target is an Oracle Cloud Always Free ARM instance (4 OCPU, 24 GB), which has
  more CPU and roughly 3x the RAM of the Pi that prompted the retrain caveat. The
  Pi-versus-laptop decision and the retrain measurement are no longer prerequisites
  for anything. Delete those as open questions, do not restate them.
- Postgres is managed (Supabase) rather than a compose service, chosen partly so
  the box stays stateless and disposable.
- Budgerr deploys alone. playstat follows in a later session, onto the same box,
  using compose services that are already authored. The old "both APIs move
  together" coupling is explicitly dropped, and that coupling is what stalled this
  for two months.
- The database starts **empty**, which is a product decision with visible user
  cost: every bank account must be re-linked, and transaction history, bet history
  and bankroll all start from zero on the cloud instance. This belongs in PRODUCT.md
  because it is a product consequence, not an ops detail.
- Still pending: executing the runbook. Nothing is deployed.

Then check the rest of the file for other claims the above falsifies. At minimum
look at the **Ordering** subsection near the end and any other line mentioning
hardware, the Pi, the laptop, or the deploy being gated. Fix what is now wrong;
leave what is still right alone.

## 2. `docs/ARCHITECT.md`, section 2 (Data layer)

Add a short, clearly-headed passage recording the Postgres portability
constraint. This is the durable half of the Supabase decision, and it exists to
be read **before** someone adds a dependency, so put it where the data layer is
described rather than in a deploy doc.

Content, in the file's voice:

- Budgerr's only coupling to Postgres is a `DATABASE_URL` consumed by plain
  SQLAlchemy with psycopg3, with alembic owning the schema. See
  `backend/app/config.py`.
- There is deliberately no `supabase-py`, no PostgREST, no Supabase Auth, no
  Supabase storage or realtime, and no RLS policy the application depends on.
  Budgerr's own auth is its `X-API-Key` check in `backend/app/auth.py`, which is
  unrelated to the database provider.
- Therefore moving to a different Postgres, self-hosted or another provider, is
  changing one environment variable and restoring a `pg_dump`.
- **This is a constraint to preserve, not just a description.** Adopting any
  Supabase-specific feature would convert that one-variable move into a rewrite.
  Say so directly, so the next person reads it as a rule.
- The nightly `age`-encrypted `pg_dump` (`backend/ops/backup.sh`) is the
  mechanical enforcement: as long as a plain dump runs and restores, lock-in
  cannot accumulate quietly. Note that it is not redundant with Supabase's own
  backups, whose free tier has short retention and no point-in-time recovery.

If section 9 (Security) or section 7 (Backend and API) makes any claim about
where Postgres runs that this contradicts, fix it there too.

## 3. `docs/OPERATIONS.md`, section 1 (Which machine)

**Careful: most of this section is still true and must stay.** The project does
still run on the Mac, the scheduled jobs are still launchd, and a Windows
checkout still cannot verify anything by running it. Do not rewrite it into the
future tense.

Make exactly two changes:

1. Add a brief forward pointer near the top of section 1 saying a cloud deploy is
   designed and its runbook is ready (`docs/DEPLOY.md`), that the target is an
   Oracle Always Free instance with managed Supabase Postgres, and that **it has
   not been executed**, so everything in this section still describes reality.
   State plainly what changes when it does ship: the Mac stops being the only
   machine that can run Budgerr, and a checkout anywhere becomes able to verify
   against the deployed API. One short paragraph, not a section.
2. The sentence "Python venvs and `node_modules` embed absolute paths and are not
   portable, so moving any of these projects means rebuilding those, not copying
   the folder" is still correct. Leave it.

Do **not** change sections 2 or 3 (local services, scheduled jobs). They describe
the Mac, which remains authoritative until the owner verifies the cloud instance
and decides to cut over.

## Done when

All three files are accurate as of today, no file claims anything is deployed,
`PRODUCT.md` no longer presents the hardware choice or the retrain measurement as
open questions, and `docs/ARCHITECT.md` states the Postgres portability
constraint as a rule at the data layer.
