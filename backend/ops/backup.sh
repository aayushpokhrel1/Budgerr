#!/usr/bin/env bash
# Nightly encrypted Postgres backup for Budgerr.
#
# Dumps Postgres (custom format, -Fc) and encrypts it with `age` using the
# public recipient in ~/.config/budgerr/backup-age.pub.
#
# Two dump sources, chosen by whether BUDGERR_DB_URL is set:
#   - UNSET (the Mac): `docker exec` into the local Postgres container.
#   - SET (the cloud box): `pg_dump` straight at the URL, because Budgerr's
#     Postgres is managed (Supabase) and there is no local container to exec into.
# This dump is also what keeps the managed Postgres replaceable: as long as a
# plain pg_dump runs nightly and restores, no vendor lock-in accumulates quietly.
# Supabase's free tier has short backup retention and no PITR, so this is not
# redundant with theirs.
#
# Two destinations:
#   - LOCAL (~/Budgerr-Backups): authoritative. Atomic temp->mv + retention;
#     fully reliable under launchd.
#   - iCloud Drive: off-machine redundancy. macOS only lets a launchd-spawned
#     process CREATE files there (rename() and unlink() return EPERM without
#     Full Disk Access), so the iCloud copy is create-only and best-effort: it
#     never fails the backup, and its retention is opportunistic. Grant the
#     backup job Full Disk Access to make the iCloud leg fully reliable
#     (see backend/ops/restore.md).
#
# The private key (~/.config/budgerr/backup-age.key) is the ONLY thing that can
# decrypt these dumps and is deliberately NOT stored with the backups.
# Restore + decrypt procedure: backend/ops/restore.md.
set -euo pipefail

# launchd (and systemd) run with a minimal PATH; docker and age live in
# /usr/local/bin. APPENDED, not prepended: under launchd the inherited PATH has
# no docker/age so the fallback still finds them, and an explicitly-set PATH
# keeps priority, which is what lets test-backup-source.sh put stubs ahead of
# the real binaries. Prepending silently overrode the caller.
export PATH="$PATH:/usr/local/bin:/usr/bin:/bin"

CONTAINER="${BUDGERR_DB_CONTAINER:-budgerr-postgres-1}"
DB_USER="budgerr"
DB_NAME="budgerr"
RECIPIENTS="${BUDGERR_AGE_RECIPIENTS:-$HOME/.config/budgerr/backup-age.pub}"
LOCAL_DEST="${BUDGERR_BACKUP_DIR:-$HOME/Budgerr-Backups}"
ICLOUD_DEST="${BUDGERR_ICLOUD_DEST:-$HOME/Library/Mobile Documents/com~apple~CloudDocs/Budgerr-Backups}"
KEEP=14

if [ ! -f "$RECIPIENTS" ]; then
  echo "$(date): backup FAILED: recipients file $RECIPIENTS missing" >&2
  exit 1
fi

mkdir -p "$LOCAL_DEST"
name="budgerr-$(date +%Y%m%d-%H%M%S).dump.age"
out="$LOCAL_DEST/$name"
tmp="$out.tmp"
trap 'rm -f "$tmp"' EXIT

# Dump and encrypt in one pipe (pipefail catches a failing pg_dump before we
# ever promote the temp file).
if [ -n "${BUDGERR_DB_URL:-}" ]; then
  # pg_dump REJECTS SQLAlchemy's dialect suffix ("postgresql+psycopg://"), so
  # strip it. This lets the same DATABASE_URL the app uses be passed through
  # unedited, which is the whole point: two hand-maintained copies of a
  # connection string drift, and this one only runs at 03:00 where nobody sees
  # it fail.
  dump_url=$(printf '%s' "$BUDGERR_DB_URL" | sed 's|^postgresql+[a-z0-9]*:|postgresql:|')
  pg_dump -Fc "$dump_url" | age -R "$RECIPIENTS" -o "$tmp"
else
  docker exec "$CONTAINER" pg_dump -U "$DB_USER" -Fc "$DB_NAME" \
    | age -R "$RECIPIENTS" -o "$tmp"
fi

# Sanity check: a real encrypted custom-format dump is comfortably >1KB.
if [ ! -s "$tmp" ] || [ "$(wc -c < "$tmp")" -lt 1000 ]; then
  echo "$(date): backup FAILED: output too small, aborting" >&2
  exit 1
fi

mv "$tmp" "$out"
trap - EXIT
echo "$(date): local backup OK -> $out ($(wc -c < "$out") bytes)"

# Local retention: keep the newest $KEEP, delete older. Only after a successful
# write, so a failed run never prunes good backups.
ls -t "$LOCAL_DEST"/budgerr-*.dump.age 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r f; do
  rm -f "$f" && echo "$(date): pruned local $f"
done

# Off-machine copy to iCloud (create-only; best-effort, see header note).
if [ -z "$ICLOUD_DEST" ]; then
  echo "$(date): iCloud off-machine copy disabled (BUDGERR_ICLOUD_DEST empty)"
elif mkdir -p "$ICLOUD_DEST" 2>/dev/null && cp "$out" "$ICLOUD_DEST/$name" 2>/dev/null; then
  echo "$(date): off-machine copy OK -> $ICLOUD_DEST/$name"
  # Opportunistic retention (unlink may EPERM under launchd without FDA; ignore).
  ls -t "$ICLOUD_DEST"/budgerr-*.dump.age 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r f; do
    rm -f "$f" 2>/dev/null && echo "$(date): pruned iCloud $f"
  done
else
  echo "$(date): WARN off-machine iCloud copy failed: local backup is intact; grant Full Disk Access to the backup job to enable the iCloud leg" >&2
fi

# Off-machine copy to any rclone remote (best-effort, same contract as the
# iCloud leg above: it never fails the backup). UNSET is the Mac's state and
# skips this block entirely, so nothing about the Mac's run changes.
#
# This exists because the cloud box sets BUDGERR_ICLOUD_DEST="" (iCloud is
# macOS-only), which would otherwise leave the dumps on one reclaimable boot
# volume with no second copy. Oracle can reclaim an idle Always Free instance;
# the box is stateless, but these dumps are not, so they need to leave it.
# Set e.g. BUDGERR_REMOTE_DEST="oci:budgerr-backups" (see docs/DEPLOY.md §8).
if [ -n "${BUDGERR_REMOTE_DEST:-}" ]; then
  if rclone copyto "$out" "$BUDGERR_REMOTE_DEST/$name" 2>&1; then
    echo "$(date): off-machine copy OK -> $BUDGERR_REMOTE_DEST/$name"
    # Age-based, not count-based like the local leg: one dump a night makes
    # "older than $KEEP days" and "all but the newest $KEEP" the same set, and
    # age is one rclone call instead of listing and sorting a remote.
    rclone delete --min-age "${KEEP}d" --include 'budgerr-*.dump.age' \
      "$BUDGERR_REMOTE_DEST" 2>&1 || true
  else
    echo "$(date): WARN off-machine remote copy to $BUDGERR_REMOTE_DEST failed: local backup is intact" >&2
  fi
fi
