#!/usr/bin/env bash
# Lock-safe, compressed backup of data/prices.db.
#
# Uses `sqlite3 .dump | gzip` (streamed — no full-size temp file, important since
# the live DB is ~7GB and the partition only has ~14GB free). `.dump` runs inside a
# single read transaction, so the snapshot is consistent even if a fetch slice is
# writing. Keeps the last KEEP backups and skips if free space is too low.
#
# Restore:  gunzip -c data/backups/prices-YYYY-MM-DD.db.sql.gz | sqlite3 restored.db
#
# NOTE: this writes to the SAME partition as the live DB — it protects against logical
# corruption, accidental DELETE/DROP, and a botched VACUUM/retention, but NOT against
# disk failure. For off-box durability, uncomment the rsync line at the bottom and point
# it at a remote target.
set -euo pipefail

cd "$(dirname "$0")/.."                 # repo root
DB="data/prices.db"
DEST="data/backups"
KEEP=2                                  # how many compressed backups to retain
MIN_FREE_KB=$((4 * 1024 * 1024))        # require ≥4 GB free before starting

mkdir -p "$DEST"

avail_kb=$(df --output=avail -k . | tail -1 | tr -d ' ')
if [ "$avail_kb" -lt "$MIN_FREE_KB" ]; then
    echo "$(date -Is) backup_db: SKIP — only ${avail_kb}KB free (< ${MIN_FREE_KB}KB)." >&2
    exit 1
fi

out="$DEST/prices-$(date +%F).db.sql.gz"
tmp="$out.tmp"
echo "$(date -Is) backup_db: dumping $DB -> $out"
sqlite3 "$DB" ".dump" | gzip -c > "$tmp"
gzip -t "$tmp"                          # verify the archive is intact before publishing
mv -f "$tmp" "$out"
echo "$(date -Is) backup_db: done — $(du -h "$out" | cut -f1)"

# Retention: keep the newest $KEEP, delete older.
ls -1t "$DEST"/prices-*.db.sql.gz 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
    echo "$(date -Is) backup_db: pruning $old"
    rm -f "$old"
done

# Off-box copy (optional — set a real target and uncomment):
# rsync -a "$out" user@backup-host:/srv/backups/monitorulpreturilor/
