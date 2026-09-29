# Plan: rewrite docs/DEPLOY.md for the Oracle + Supabase deploy

Executes item 4 of
[`../specs/2026-09-29-cloud-deploy-design.md`](../specs/2026-09-29-cloud-deploy-design.md) §7.
**Read that spec first — it is authoritative.** Where this plan and the spec
disagree, the spec wins.

Also read, because they are already changed and the doc must describe them
accurately rather than what it remembers:

- `deploy/docker-compose.yml` — `budgerr-db` and its volume are GONE; `DATABASE_URL`
  is no longer overridden; playstat services remain but are not started.
- `backend/app/config.py` — the `database_url` comment on Supabase pooler choice.
- `backend/ops/backup.sh` — new `BUDGERR_DB_URL` dump source; `BUDGERR_DB_CONTAINER`
  is only used when that is unset.

Edit **only** `docs/DEPLOY.md`. Do not touch any other file. Preserve the file's
existing voice, heading numbering, and its habit of explaining *why* a step
exists. Keep every section that is still true — this is a surgical revision of a
496-line runbook, not a rewrite from scratch. **Do not use em dashes or en
dashes anywhere; use a comma, colon, parentheses, or two sentences.**

## Section-by-section

**Header.** Delete the "This is prep only. Nothing is deployed." gate and the
paragraph qualifying it. The owner gave the go-ahead on 2026-09-29. Replace with
a short statement that this is a live runbook, that the target is an Oracle Cloud
Always Free instance with managed Supabase Postgres, and that Budgerr deploys
alone with playstat following in a later session. Point at the new spec for the
design rationale, keeping the existing pointer to the July design as superseded
on hardware only.

**§1 Scope & gate.** Rewrite. Drop "both APIs move together, onto one box, in one
Compose stack" and "no hardware has been bought" — both are obsolete. Keep the
playstat review gate, but scope it to when playstat actually moves. State that
Budgerr goes first and alone, and why that is safe (spec §6).

**§2 Choose the box.** Delete the Pi-vs-laptop table and the entire "Pi retrain
caveat" block, including the "do not commit to the Pi without timing the retrain"
paragraph. Do not leave them above a newer note: they are false now and the next
reader would hit them first. Replace with Oracle Cloud Always Free:
`VM.Standard.A1.Flex`, 4 ARM OCPUs and 24 GB RAM, Ubuntu 24.04 ARM64. Say plainly
that this has more CPU and roughly 3x the RAM of the Pi that prompted the old
caveat, so the retrain question stops gating anything. Then add Oracle's
idle-instance reclamation policy (roughly: 95th-percentile CPU, network and
memory all under 20% across 7 days) as a real risk, and why it is tolerable here:
the box is stateless, so reclaim means rebuilding from this runbook, not losing
data. Tell the reader to keep secrets recoverable off-box.

**§3 OS prep.** Replace the two-target structure with single-target Oracle steps.
Keep the existing commands (apt upgrade, Docker via get.docker.com, `usermod -aG
docker`, Tailscale install, `timedatectl set-timezone`, `systemctl enable --now
docker`). The default user on Oracle's Ubuntu image is `ubuntu`. Add two things:

1. `sudo apt install -y postgresql-client-16` — `backup.sh` now runs `pg_dump`
   on the box instead of inside a container, so the client must exist, and its
   major version must be at least the server's (Supabase is 16).
2. A warning that OCI Ubuntu images ship restrictive `iptables` rules (persisted
   in `/etc/iptables/rules.v4`) *and* that the VCN has its own security lists, so
   inbound ports are blocked in two independent places. Then the payoff: Tailscale
   Funnel is outbound-initiated and needs no inbound port, so **do not** open
   either. People lose hours to this.

**§4 Get the repos.** Only Budgerr is cloned now. Keep the note that build
contexts resolve relative to `deploy/`, and that playstat must be a sibling
(`~/dev/playstat`) when it is added later, since that constrains the layout today.

**§5 Secrets.** The important change: `DATABASE_URL` is now **required and
owned by `backend/.env`**, because compose no longer overrides it. Replace the
table row saying it is ignored. It must be Supabase's **session pooler** (port
5432 on `...pooler.supabase.com`), and state both reasons from the spec §3: the
direct connection is IPv6-only on recent projects, and the transaction pooler on
6543 breaks psycopg3 prepared statements. Describe each failure mode, not just
the rule, since neither is obvious from the error. `PLAYSTAT_BASE_URL` is still
compose-overridden, so its row stays. Everything else in the table stays.

**§6 Build & start.** There is no `budgerr-db` to wait on, so this is now
`docker compose build budgerr-api` then `docker compose up -d budgerr-api`. Drop
the `service_healthy` gating discussion for Budgerr. Keep the `docker compose
config --quiet` guidance and the warning about bare `docker compose config`
leaking resolved secrets to stdout.

**§7 Database migration.** Retitle to "Database setup". Delete the Budgerr
restore option (A) and the whole playstat migration subsection; playstat is a
later session, and the chosen path is an empty start. Budgerr is now just
`docker compose exec budgerr-api alembic upgrade head`. Then add, prominently,
the consequences from spec §4, because they are easy to under-read:

- Every bank account must be re-linked through `/link-bank`, since Plaid access
  tokens are rows in the `plaid_items` table and an empty database has none. The
  backend cannot sync anything until this is done. Present it as a required step.
- Transaction history does not come back (Plaid backfills a limited window only),
  and bet history and bankroll start at zero.
- The Mac's database and the cloud database diverge permanently the moment the
  cloud one is used, with no merge path.

Keep a short pointer to `backend/ops/restore.md` for future disaster recovery,
and note that restoring the Mac's latest encrypted backup is the escape hatch if
the history turns out to matter, but only before real data accumulates in the
cloud instance.

**§8 systemd.** The unit files and `systemctl enable` commands are unchanged.
`/etc/budgerr/cron.env` changes:

- **Remove** `BUDGERR_DB_CONTAINER` — there is no local Postgres container.
- **Add** `BUDGERR_DB_URL` set to the same Supabase session-pooler URL as
  `DATABASE_URL`. Explain that `backup.sh` switches to a direct `pg_dump` when
  this is set, and that it strips the SQLAlchemy `+psycopg` suffix itself, so the
  value can be copied across verbatim.
- **Add** `BUDGERR_ICLOUD_DEST=""` and say why: the default is a macOS iCloud
  path, which on Linux is merely a junk directory the script would create. Empty
  disables the off-machine leg explicitly.

Keep the age public/private key guidance and the key-custody warning verbatim,
they are still exactly right. Keep the "do not hand-edit backup.sh" note but
correct the variable list it cites.

**§9 Tailscale exposure.** Substantively unchanged. Add one line that Funnel is
now carrying more weight than before, since it is what lets the OCI firewall and
VCN security lists stay fully closed (§3).

**§10 Repoint clients.** Unchanged.

**§11 Smoke tests.** Keep tests 1, 2, 3 and 5 as they are. **Test 4
(`/playstat/edges`) now expects `502`, and that is a PASS.** Say so explicitly
with the reason: playstat is not running yet, `PLAYSTAT_BASE_URL` fails DNS, and
the proxy converts that to a clean 502 by design. Without this note the next
reader will chase a non-bug.

**§12 Security note.** Keep all of it, including the two-edit cron key rotation
warning (update its variable names if they changed). Add: the Supabase database
password now lives in `backend/.env`, and rotating it means rotating in the
Supabase dashboard, then updating both `backend/.env` and `BUDGERR_DB_URL` in
`/etc/budgerr/cron.env`, which is the same two-place trap as the cron key.

**§13 Rollback.** `docker compose down` now leaves only `playstat_pgdata`
behind. Keep the "the Mac remains the source of truth until cutover is verified"
paragraph, and sharpen it: with an empty start, the Mac's data is not migrated at
all, so the Mac is the only place that history exists.

**§14 Troubleshooting.** Keep the playstat `API_BASKETBALL_KEY` entry and the
arm64 build-verification entry (still relevant for playstat later, and the box is
now definitely ARM64, so say that it matters more, not less). Drop the Mac
Docker Desktop line only if §14 no longer targets the Mac; otherwise keep it.
Add entries for: Oracle's double firewall (iptables plus VCN security lists);
the two Supabase connection traps and their symptoms; Oracle idle reclaim; and
`pg_dump: server version mismatch` if `postgresql-client` is older than the
Supabase server.

## Done when

`docs/DEPLOY.md` contains no surviving reference to `budgerr-db`, the Pi, the old
laptop, the retrain caveat, "prep only", or restoring the Mac's dump as the
chosen path, and every command in it is one that would actually run on an Oracle
Ubuntu 24.04 ARM64 box against Supabase.
