#!/usr/bin/env bash
# Pull data/ from the VPS to the local backup volume, verify, and notify via ntfy.
# Run from the Mac (not the VPS). See docs/local-backup.md.
#
# Env (all optional):
#   NTFY_TOPIC   ntfy.sh topic to notify (unset = no notifications, log only)
#   NTFY_SERVER  default https://ntfy.sh
#   DEST         default /Volumes/geana/bckpz/WRK/gov2/monitorulpreturilor/data
#   SKIP_DB=1    sync only the small files (logs, checkpoints, reference)
set -uo pipefail

VPS="pax@78.46.142.169"
SRC="g2-dev/monitorulpreturilor/data"
DEST="${DEST:-/Volumes/geana/bckpz/WRK/gov2/monitorulpreturilor/data}"
NTFY_SERVER="${NTFY_SERVER:-https://ntfy.sh}"

notify() {  # notify <priority> <title> <message>
    echo "$(date -Is) [$2] $3"
    [ -z "${NTFY_TOPIC:-}" ] && return 0
    curl -fsS -m 10 -o /dev/null -H "Priority: $1" -H "Title: $2" -d "$3" \
        "$NTFY_SERVER/$NTFY_TOPIC" || true
}
fail() { notify high "monitorulpreturilor backup FAILED" "$1"; exit 1; }

# 1. Preflight
[ -d "$(dirname "$DEST")" ] || fail "backup volume not mounted: $(dirname "$DEST")"
mkdir -p "$DEST"
ssh -o BatchMode=yes -o ConnectTimeout=10 "$VPS" true || fail "VPS unreachable"
# Never copy a DB that is being written: fetch holds a lock file / process while running.
if ssh -o BatchMode=yes "$VPS" 'pgrep -f "[f]etch_prices.py|[f]etch_gas_prices.py|[b]ackup_db.sh" >/dev/null'; then
    fail "VPS fetch/backup running; DB would be torn. Retry later."
fi

# 2. Small files (skip DB and the on-VPS dump backups)
rsync -az --exclude='prices.db*' --exclude='backups/' "$VPS:$SRC/" "$DEST/" \
    || fail "rsync of data files failed"

# 3. DB: --inplace so the delta algorithm reuses the existing local copy
# Since 2026-10-29 the VPS prices.db is a 60-day slim DB; the local copy is the only full
# history. Syncing it over would DESTROY that history, so the DB step is opt-in (SKIP_DB=0)
# until a merge-new-rows strategy exists (see docs/backlog.md).
if [ "${SKIP_DB:-1}" != "1" ]; then
    rsync -a --inplace --partial "$VPS:$SRC/prices.db" "$DEST/prices.db" \
        || fail "rsync of prices.db failed"
    # WAL should be empty when idle; copy it if present so the pair is consistent.
    rsync -a "$VPS:$SRC/prices.db-wal" "$DEST/" 2>/dev/null || rm -f "$DEST/prices.db-wal" "$DEST/prices.db-shm"

    # 4. Verify (quick_check is minutes, not the ~hour of integrity_check on 15 GB)
    chk=$(sqlite3 "file:$DEST/prices.db?immutable=1" "PRAGMA quick_check;" 2>&1 | head -1)
    [ "$chk" = "ok" ] || fail "quick_check on local prices.db: $chk"
    local_max=$(sqlite3 "file:$DEST/prices.db?immutable=1" "SELECT max(price_date) FROM prices;")
    remote_max=$(ssh -o BatchMode=yes "$VPS" "sqlite3 'file:$SRC/prices.db?mode=ro' 'SELECT max(price_date) FROM prices;'")
    [ "$local_max" = "$remote_max" ] || fail "price_date mismatch: local=$local_max vps=$remote_max"
    notify default "monitorulpreturilor backup OK" "prices.db synced, latest price_date=$local_max, $(du -h "$DEST/prices.db" | cut -f1)"
else
    notify default "monitorulpreturilor backup OK (no DB)" "small files synced"
fi
