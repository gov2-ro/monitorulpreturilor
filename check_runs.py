#!/usr/bin/env python3
"""check_runs.py — fast post-fetch verification.

Reads the `runs` table and exits non-zero if the most recent COMPLETED run
for a given script is missing, stale, or empty.

Usage:
    python check_runs.py --script fetch_prices --max-age-hours 25
    python check_runs.py --script fetch_gas_prices --max-age-hours 25

Exit codes:
    0 — healthy (or still running, when the lock file is present)
    1 — unhealthy (alert via healthcheck wrapper)
    2 — usage / unexpected error

Notes:
    The `runs` row's `started_at` is anchored to the checkpoint's `fetched_at`,
    which can be days old on a long resume. The reliable freshness signal is
    `finished_at` of the latest row with status='completed'.
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone

# Lock file is created by fetch_prices.py; other scripts (e.g. fetch_gas_prices)
# don't use one, so the lock is only relevant when checking 'fetch_prices'.
LOCK_FILES = {"fetch_prices": "data/prices_fetch.lock"}


def _lock_holder_alive(lock_path):
    """Return True if the PID written into the lock file is still running."""
    try:
        with open(lock_path) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)  # signal 0: existence check only
        return True
    except (OSError, ValueError, FileNotFoundError):
        return False


def parse_iso(s):
    if not s:
        return None
    try:
        # SQLite stores either ISO with TZ or "YYYY-MM-DD HH:MM:SS" (no TZ)
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def check(db_path, script, max_age_hours, lock_file=None):
    if lock_file is None:
        lock_file = LOCK_FILES.get(script)
    if lock_file and os.path.exists(lock_file) and _lock_holder_alive(lock_file):
        return 0, f"OK {script}: lock {lock_file} held by live PID — run in progress, skipping check"

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("""
            SELECT id, started_at, finished_at, status, records_written, notes
            FROM runs
            WHERE script = ? AND status = 'completed'
            ORDER BY id DESC LIMIT 1
        """, (script,)).fetchone()
    finally:
        conn.close()

    if not row:
        return 1, f"FAIL {script}: no completed run on record"

    run_id, started, finished, status, records, notes = row
    finished_dt = parse_iso(finished)
    if not finished_dt:
        return 1, f"FAIL {script}: run #{run_id} has unparseable finished_at={finished!r}"

    now = datetime.now(timezone.utc)
    age_h = (now - finished_dt).total_seconds() / 3600.0
    if age_h > max_age_hours:
        return 1, (f"FAIL {script}: last completed run #{run_id} finished {age_h:.1f}h ago "
                   f"(>{max_age_hours}h); started_at={started}")

    if not records or records <= 0:
        return 1, (f"FAIL {script}: last completed run #{run_id} wrote {records} records "
                   f"(finished {age_h:.1f}h ago)")

    return 0, (f"OK {script}: run #{run_id} finished {age_h:.1f}h ago, "
               f"{records} records written")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--script", required=True, help="Script name as recorded in runs.script")
    ap.add_argument("--max-age-hours", type=float, default=25.0,
                    help="Max age of finished_at to consider healthy (default 25)")
    ap.add_argument("--db", default="data/prices.db")
    ap.add_argument("--lock", default=None,
                    help="Override the auto-selected lock file (default: per-script)")
    args = ap.parse_args()

    code, msg = check(args.db, args.script, args.max_age_hours, args.lock)
    print(msg, flush=True)
    sys.exit(code)


if __name__ == "__main__":
    main()
