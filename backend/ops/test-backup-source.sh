#!/usr/bin/env bash
# Checks backup.sh picks the right dump source and hands pg_dump a URL it will
# actually accept. Exists because this code path runs at 03:00 under a timer,
# where a mistake is invisible until a restore is needed and fails.
#
# Run: bash backend/ops/test-backup-source.sh
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# Stub out everything backup.sh shells out to, so nothing real is dumped,
# encrypted or deleted. Each stub records how it was called.
mkdir -p "$work/bin"
cat > "$work/bin/pg_dump" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$STUB_LOG/pg_dump.args"
printf 'x%.0s' $(seq 2000)   # a plausibly-sized fake dump
EOF
cat > "$work/bin/docker" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$STUB_LOG/docker.args"
printf 'x%.0s' $(seq 2000)
EOF
cat > "$work/bin/age" <<'EOF'
#!/usr/bin/env bash
# -R <recipients> -o <out>; just copy stdin to the output path.
out=""; while [ $# -gt 0 ]; do [ "$1" = "-o" ] && out="$2"; shift; done
cat > "$out"
EOF
cat > "$work/bin/rclone" <<'EOF'
#!/usr/bin/env bash
echo "$*" >> "$STUB_LOG/rclone.args"
EOF
chmod +x "$work/bin"/*

export STUB_LOG="$work"
touch "$work/recipients.pub"

run_backup() {
  rm -f "$work"/*.args
  # Stubs first: backup.sh APPENDS its fallback dirs rather than prepending, so
  # what is set here keeps priority over any real docker/pg_dump/age on the box.
  PATH="$work/bin:$PATH" \
  BUDGERR_AGE_RECIPIENTS="$work/recipients.pub" \
  BUDGERR_BACKUP_DIR="$work/out" \
  BUDGERR_ICLOUD_DEST="" \
    bash "$here/backup.sh" > "$work/stdout" 2>&1
}

fail() { echo "FAIL: $1"; echo "--- output:"; cat "$work/stdout"; exit 1; }

# 1. BUDGERR_DB_URL set -> pg_dump against the URL, docker never invoked, and the
#    SQLAlchemy "+psycopg" dialect suffix stripped (pg_dump rejects it).
BUDGERR_DB_URL="postgresql+psycopg://u:p@db.pooler.supabase.com:5432/postgres" run_backup
[ -f "$work/pg_dump.args" ] || fail "URL mode did not call pg_dump"
[ -f "$work/docker.args" ] && fail "URL mode should not call docker exec"
grep -q 'postgresql://u:p@db.pooler.supabase.com:5432/postgres' "$work/pg_dump.args" \
  || fail "dialect suffix not stripped: $(cat "$work/pg_dump.args")"
grep -q '+psycopg' "$work/pg_dump.args" && fail "pg_dump got a URL it will reject"
ls "$work/out"/budgerr-*.dump.age >/dev/null 2>&1 || fail "URL mode wrote no backup"

# 2. BUDGERR_DB_URL unset -> the Mac's path: docker exec, pg_dump not called direct.
rm -rf "$work/out"
run_backup
[ -f "$work/docker.args" ] || fail "container mode did not call docker exec"
[ -f "$work/pg_dump.args" ] && fail "container mode should not call pg_dump directly"
grep -q 'pg_dump' "$work/docker.args" || fail "docker exec did not run pg_dump"
ls "$work/out"/budgerr-*.dump.age >/dev/null 2>&1 || fail "container mode wrote no backup"

# 3. BUDGERR_REMOTE_DEST unset -> rclone never runs. This is the Mac's state, and
#    the whole point of the switch is that the Mac's run is unchanged.
[ -f "$work/rclone.args" ] && fail "remote leg ran with BUDGERR_REMOTE_DEST unset"

# 4. BUDGERR_REMOTE_DEST set -> the dump is copied to the remote, and retention
#    runs against that same remote.
rm -rf "$work/out"
BUDGERR_REMOTE_DEST="oci:budgerr-backups" run_backup
[ -f "$work/rclone.args" ] || fail "remote leg did not call rclone"
name=$(basename "$(ls "$work/out"/budgerr-*.dump.age)")
grep -q "copyto .*$name oci:budgerr-backups/$name" "$work/rclone.args"   || fail "rclone did not copy the dump to the remote: $(cat "$work/rclone.args")"
grep -q "delete .*oci:budgerr-backups" "$work/rclone.args"   || fail "rclone did not prune the remote: $(cat "$work/rclone.args")"

# 5. A failing remote must not fail the backup: the local dump is authoritative.
rm -rf "$work/out"
cat > "$work/bin/rclone" <<'EOF'
#!/usr/bin/env bash
echo "simulated remote failure" >&2
exit 1
EOF
chmod +x "$work/bin/rclone"
BUDGERR_REMOTE_DEST="oci:budgerr-backups" run_backup   || fail "a failing remote leg aborted the backup"
ls "$work/out"/budgerr-*.dump.age >/dev/null 2>&1   || fail "local backup missing after remote leg failed"
grep -q "WARN off-machine remote copy" "$work/stdout"   || fail "failing remote leg did not warn"

echo "PASS: both dump sources and the off-machine remote leg behave correctly"
