#!/usr/bin/env bash
# Nightly encrypted Postgres backup for Budgerr.
#
# Dumps the Docker Postgres (custom format, -Fc) and encrypts it with `age`
# using the public recipient in ~/.config/budgerr/backup-age.pub.
#
# Two destinations:
#   - LOCAL (~/Budgerr-Backups): authoritative. Atomic temp->mv + retention;
#     fully reliable under launchd.
#   - iCloud Drive: off-machine redundancy. macOS only lets a launchd-spawned
#     process CREATE files there — rename() and unlink() return EPERM without
#     Full Disk Access — so the iCloud copy is create-only and best-effort: it
#     never fails the backup, and its retention is opportunistic. Grant the
#     backup job Full Disk Access to make the iCloud leg fully reliable
#     (see backend/ops/restore.md).
#
# The private key (~/.config/budgerr/backup-age.key) is the ONLY thing that can
# decrypt these dumps and is deliberately NOT stored with the backups.
# Restore + decrypt procedure: backend/ops/restore.md.
set -euo pipefail

# launchd runs with a minimal PATH; docker and age live in /usr/local/bin.
export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"

CONTAINER="${BUDGERR_DB_CONTAINER:-budgerr-postgres-1}"
DB_USER="budgerr"
DB_NAME="budgerr"
RECIPIENTS="${BUDGERR_AGE_RECIPIENTS:-$HOME/.config/budgerr/backup-age.pub}"
LOCAL_DEST="${BUDGERR_BACKUP_DIR:-$HOME/Budgerr-Backups}"
ICLOUD_DEST="$HOME/Library/Mobile Documents/com~apple~CloudDocs/Budgerr-Backups"
KEEP=14

if [ ! -f "$RECIPIENTS" ]; then
  echo "$(date): backup FAILED — recipients file $RECIPIENTS missing" >&2
  exit 1
fi

mkdir -p "$LOCAL_DEST"
name="budgerr-$(date +%Y%m%d-%H%M%S).dump.age"
out="$LOCAL_DEST/$name"
tmp="$out.tmp"
trap 'rm -f "$tmp"' EXIT

# Dump straight from the container and encrypt in one pipe (pipefail catches a
# failing pg_dump before we ever promote the temp file).
docker exec "$CONTAINER" pg_dump -U "$DB_USER" -Fc "$DB_NAME" \
  | age -R "$RECIPIENTS" -o "$tmp"

# Sanity check: a real encrypted custom-format dump is comfortably >1KB.
if [ ! -s "$tmp" ] || [ "$(wc -c < "$tmp")" -lt 1000 ]; then
  echo "$(date): backup FAILED — output too small, aborting" >&2
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

# Off-machine copy to iCloud (create-only; best-effort — see header note).
if mkdir -p "$ICLOUD_DEST" 2>/dev/null && cp "$out" "$ICLOUD_DEST/$name" 2>/dev/null; then
  echo "$(date): off-machine copy OK -> $ICLOUD_DEST/$name"
  # Opportunistic retention (unlink may EPERM under launchd without FDA; ignore).
  ls -t "$ICLOUD_DEST"/budgerr-*.dump.age 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r f; do
    rm -f "$f" 2>/dev/null && echo "$(date): pruned iCloud $f"
  done
else
  echo "$(date): WARN off-machine iCloud copy failed — local backup is intact; grant Full Disk Access to the backup job to enable the iCloud leg" >&2
fi
