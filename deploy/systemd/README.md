# Budgerr systemd units

Copy `*.service`/`*.timer` to `/etc/systemd/system/`, then:

    sudo systemctl daemon-reload
    sudo systemctl enable --now budgerr-plaid-sync.timer budgerr-auto-settle.timer \
        budgerr-auto-log.timer budgerr-backup.timer

Create `/etc/budgerr/cron.env` (mode 600, root-owned):

    BUDGERR_CRON_KEY=<the cron key from BUDGERR_API_KEYS>
    BUDGERR_HOME=/home/<user>/dev/Budgerr
    BUDGERR_DB_CONTAINER=budgerr-stack-budgerr-db-1
    BUDGERR_AGE_RECIPIENTS=/etc/budgerr/backup-age.pub
    BUDGERR_BACKUP_DIR=/var/backups/budgerr

These three overrides exist because `backup.sh` defaults to the macOS/launchd
values so the Mac's authoritative 03:00 backup keeps working unchanged until
cutover; the box supplies its own values via the unit's `EnvironmentFile`. Note
that the age **private** key must also be present on the box for restores (per
`backend/ops/restore.md`) and is deliberately not stored alongside the backups.

## playstat ordering (coordinate with the playstat session)

`budgerr-auto-settle`/`budgerr-auto-log` declare `After=playstat-mlb.service` so
they queue behind playstat's morning retrain if it is still running. That unit
is authored on the **playstat** side. `After=` without `Wants=` is a soft
ordering: if `playstat-mlb.service` is absent, these jobs simply run at their
scheduled time. Confirm the real retrain duration on the chosen box and tune the
09:15/09:45 times if needed.

## Verify on the box (cannot run on macOS)

    systemd-analyze verify /etc/systemd/system/budgerr-*.service \
        /etc/systemd/system/budgerr-*.timer
    systemctl list-timers 'budgerr-*'
