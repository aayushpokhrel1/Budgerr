# Budgerr backup & restore

Nightly encrypted backups are produced by [`backup.sh`](backup.sh), scheduled
via the launchd job `com.budgerr.backup` (plist in `~/Library/LaunchAgents`,
not in git — see README §15.2 / the launchd memory).

- **What**: `pg_dump -Fc` of the Docker Postgres, encrypted with `age`.
- **Where**:
  - `~/Budgerr-Backups/` — **authoritative** local copy (atomic write + retention, reliable under launchd).
  - `~/Library/Mobile Documents/com~apple~CloudDocs/Budgerr-Backups/` — off-machine iCloud copy (encrypted at rest).
  - Retention: newest 14 in each. macOS only lets the launchd job *create* files in iCloud (rename/unlink → EPERM without Full Disk Access), so the iCloud leg is create-only + best-effort and its pruning is opportunistic; the local copy is the source of truth. Grant the backup job Full Disk Access if you want the iCloud leg fully managed.
- **Key**: encrypted to the public recipient in `~/.config/budgerr/backup-age.pub`.
  The private identity `~/.config/budgerr/backup-age.key` (mode 600) is the ONLY
  thing that can decrypt them and is intentionally kept off the backup location.

> ⚠️ **Key custody.** If `~/.config/budgerr/backup-age.key` is lost, every backup
> is permanently unrecoverable. Keep a copy of that file somewhere safe and
> separate (e.g. a password manager) — the backups themselves are useless without it.

## Decrypt a backup

```sh
age -d -i ~/.config/budgerr/backup-age.key \
  "$HOME/Budgerr-Backups/budgerr-YYYYMMDD-HHMMSS.dump.age" \
  > /tmp/budgerr-restore.dump
```

## Restore drill (safe — does NOT touch the live DB)

Restores the latest backup into a throwaway database in the same container and
checks it round-trips, then drops it. This is the drill that must actually be
run once (per README §10) and re-run whenever the pipeline changes.

```sh
# 1. Decrypt the newest backup
latest=$(ls -t "$HOME/Budgerr-Backups"/budgerr-*.dump.age | head -1)
age -d -i ~/.config/budgerr/backup-age.key "$latest" > /tmp/budgerr-restore.dump

# 2. Create a scratch DB and restore into it
docker exec budgerr-postgres-1 psql -U budgerr -d budgerr -c "DROP DATABASE IF EXISTS budgerr_restore_test;"
docker exec budgerr-postgres-1 psql -U budgerr -d budgerr -c "CREATE DATABASE budgerr_restore_test;"
docker exec -i budgerr-postgres-1 pg_restore -U budgerr -d budgerr_restore_test < /tmp/budgerr-restore.dump

# 3. Sanity-check row counts against the live DB, then drop the scratch DB
docker exec budgerr-postgres-1 psql -U budgerr -d budgerr_restore_test -c "SELECT count(*) FROM bets;"
docker exec budgerr-postgres-1 psql -U budgerr -d budgerr -c "SELECT count(*) FROM bets;"
docker exec budgerr-postgres-1 psql -U budgerr -d budgerr -c "DROP DATABASE budgerr_restore_test;"
rm -f /tmp/budgerr-restore.dump
```

## Real disaster recovery (clobbers the live DB — only when actually recovering)

```sh
age -d -i ~/.config/budgerr/backup-age.key "<backup>.dump.age" > /tmp/budgerr-restore.dump
# Stop the backend first so nothing writes mid-restore:
launchctl bootout gui/$(id -u)/com.budgerr.backend
docker exec budgerr-postgres-1 psql -U budgerr -d postgres -c "DROP DATABASE budgerr;"
docker exec budgerr-postgres-1 psql -U budgerr -d postgres -c "CREATE DATABASE budgerr;"
docker exec -i budgerr-postgres-1 pg_restore -U budgerr -d budgerr < /tmp/budgerr-restore.dump
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.budgerr.backend.plist
rm -f /tmp/budgerr-restore.dump
```
