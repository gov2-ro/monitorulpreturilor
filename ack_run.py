"""
Acknowledge abandoned/error run(s) so audit_pipeline skips them in run_history.

Usage:
  python ack_run.py --list                  # show unacknowledged bad runs
  python ack_run.py 363 388 413 438         # acknowledge by run ID
  python ack_run.py --before 2026-05-28     # acknowledge all bad runs before a date
"""

import argparse
import sys

from db import init_db


def _list_bad(conn):
    rows = conn.execute("""
        SELECT id, script, status, started_at, finished_at, notes
        FROM runs
        WHERE status IN ('abandoned', 'error')
          AND acknowledged_at IS NULL
        ORDER BY id DESC
    """).fetchall()
    if not rows:
        print("No unacknowledged bad runs.")
        return
    print(f"{'ID':>6}  {'Script':<22}  {'Status':<10}  {'Started':<24}  Notes")
    print("-" * 80)
    for r in rows:
        notes = (r[5] or "")[:40]
        print(f"{r[0]:>6}  {r[1]:<22}  {r[2]:<10}  {str(r[3]):<24}  {notes}")


def _ack(conn, ids):
    if not ids:
        print("No IDs specified.")
        return
    placeholders = ",".join("?" * len(ids))
    conn.execute(
        f"UPDATE runs SET acknowledged_at = datetime('now')"
        f" WHERE id IN ({placeholders}) AND status IN ('abandoned', 'error')",
        ids,
    )
    conn.commit()
    affected = conn.execute(
        f"SELECT id, script, status FROM runs WHERE id IN ({placeholders})", ids
    ).fetchall()
    for r in affected:
        print(f"Acknowledged run #{r[0]}  {r[1]}  {r[2]}")
    if not affected:
        print("No matching runs found (check IDs and status).")


def _ack_before(conn, before_date):
    rows = conn.execute("""
        SELECT id FROM runs
        WHERE status IN ('abandoned', 'error')
          AND acknowledged_at IS NULL
          AND finished_at < ?
    """, (before_date,)).fetchall()
    ids = [r[0] for r in rows]
    if not ids:
        print(f"No unacknowledged bad runs before {before_date}.")
        return
    _ack(conn, ids)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default="data/prices.db", metavar="PATH")
    parser.add_argument("ids", nargs="*", type=int, metavar="ID",
                        help="run IDs to acknowledge")
    parser.add_argument("--list", action="store_true",
                        help="list unacknowledged bad runs")
    parser.add_argument("--before", metavar="DATE",
                        help="acknowledge all bad runs with finished_at before DATE (YYYY-MM-DD)")
    args = parser.parse_args()

    if not args.list and not args.ids and not args.before:
        parser.print_help()
        sys.exit(1)

    conn = init_db(args.db)
    try:
        if args.list:
            _list_bad(conn)
        if args.before:
            _ack_before(conn, args.before)
        if args.ids:
            _ack(conn, args.ids)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
