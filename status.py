#!/usr/bin/env python3
"""status.py — CLI pipeline status digest.

Prints in one shot:
  1. last N script runs (table)
  2. per-script summary over the last D days
  3. latest data-quality audit verdict (read from data/logs/audit-*.json)

Informational only — always exits 0. For cron healthchecks, see check_runs.py.

Usage:
    python status.py
    python status.py --runs 20 --days 14
    python status.py --db data/prices.db --no-color
"""

import argparse
import glob
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from check_runs import parse_iso as _parse_iso_raw

STALE_HOURS = 25.0  # mirrors check_runs.py default


def parse_iso(s):
    """parse_iso + force UTC. Python 3.11's fromisoformat parses
    'YYYY-MM-DD HH:MM:SS' as naive, which breaks arithmetic against
    TZ-aware values present in the same column.
    """
    dt = _parse_iso_raw(s)
    if dt is not None and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

# ANSI colors — set to empty strings when --no-color or non-TTY
class C:
    reset = "\033[0m"
    dim = "\033[2m"
    bold = "\033[1m"
    green = "\033[32m"
    yellow = "\033[33m"
    red = "\033[31m"
    cyan = "\033[36m"


def disable_color():
    for attr in ("reset", "dim", "bold", "green", "yellow", "red", "cyan"):
        setattr(C, attr, "")


STATUS_COLOR = {
    "completed": "green",
    "interrupted": "yellow",
    "running": "cyan",
    "abandoned": "red",
    "error": "red",
}


def fmt_duration(start, end):
    if not start or not end:
        return "—"
    sd, ed = parse_iso(start), parse_iso(end)
    if not sd or not ed:
        return "—"
    secs = (ed - sd).total_seconds()
    if secs < 0:
        return "—"
    if secs < 60:
        return f"{int(secs)}s"
    if secs < 3600:
        return f"{int(secs // 60)}m"
    if secs < 86400:
        h, m = divmod(int(secs // 60), 60)
        return f"{h}h {m}m"
    d, rem = divmod(int(secs), 86400)
    h = rem // 3600
    return f"{d}d {h}h"


def fmt_records(n):
    if n is None:
        return "—"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def fmt_age(dt):
    if not dt:
        return "never"
    secs = (datetime.now(timezone.utc) - dt).total_seconds()
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{secs / 3600:.1f}h ago"
    return f"{secs / 86400:.1f}d ago"


def fmt_ts(s):
    """Shorten an ISO timestamp to 'YYYY-MM-DD HH:MM' for table display."""
    dt = parse_iso(s)
    return dt.strftime("%Y-%m-%d %H:%M") if dt else (s or "—")


def color_status(status):
    name = STATUS_COLOR.get(status, "dim")
    return f"{getattr(C, name)}{status:<11}{C.reset}"


def section_recent_runs(conn, limit):
    # Pull a wider slice so we can collapse zombie-sweep bursts (many rows
    # sharing the same script+status+finished_at) without losing context.
    rows = conn.execute("""
        SELECT id, script, started_at, finished_at, status, records_written
        FROM runs
        ORDER BY COALESCE(finished_at, started_at) DESC, id DESC
        LIMIT ?
    """, (limit * 5,)).fetchall()

    groups = []  # list of [latest_id, script, started, finished, status, records, count]
    for run_id, script, started, finished, status, records in rows:
        if groups:
            g = groups[-1]
            if g[1] == script and g[3] == finished and g[4] == status and not records and not g[5]:
                g[6] += 1
                continue
        groups.append([run_id, script, started, finished, status, records, 1])
        if len(groups) >= limit:
            break

    print(f"\n{C.bold}Last {len(groups)} runs:{C.reset}")
    if not groups:
        print(f"  {C.dim}no runs on record{C.reset}")
        return

    for run_id, script, started, finished, status, records, count in groups:
        finished_or_started = finished or started
        # abandoned/error rows have a stale started_at (anchored to the resume
        # checkpoint, see check_runs.py header) — duration is meaningless.
        dur = "—" if status in ("abandoned", "error") else fmt_duration(started, finished)
        recs = f"{fmt_records(records)} records" if records else "—"
        id_label = f"#{run_id}×{count}" if count > 1 else f"#{run_id}"
        print(
            f"  {id_label:<7} {script:<18} "
            f"{fmt_ts(finished_or_started):<16}  "
            f"{color_status(status or 'unknown')}  "
            f"{dur:<8}  "
            f"{recs}"
        )


def section_per_script(conn, days):
    rows = conn.execute(f"""
        SELECT script, status, started_at, finished_at
        FROM runs
        WHERE (finished_at IS NULL OR finished_at >= datetime('now', '-{days} days'))
        ORDER BY id DESC
    """).fetchall()

    # Also pull the most-recent completed row per script across all history
    # so we can show "last ok" even when nothing succeeded inside the window.
    last_ok_rows = conn.execute("""
        SELECT script, MAX(finished_at)
        FROM runs
        WHERE status = 'completed'
        GROUP BY script
    """).fetchall()
    last_ok = {script: parse_iso(ts) for script, ts in last_ok_rows}

    by_script = defaultdict(lambda: defaultdict(int))
    for script, status, _, _ in rows:
        by_script[script][status or "unknown"] += 1

    print(f"{C.bold}Per-script (last {days}d):{C.reset}")
    if not by_script:
        print(f"  {C.dim}no runs in window{C.reset}")
        return

    for script in sorted(by_script):
        counts = by_script[script]
        ok = counts.get("completed", 0)
        bad_parts = []
        for st in ("interrupted", "abandoned", "error", "running"):
            if counts.get(st):
                bad_parts.append(f"{counts[st]} {st}")
        bad_str = " / ".join(bad_parts) if bad_parts else "0 fail"

        ok_color = C.green if ok else C.dim
        bad_color = C.red if any(st in counts for st in ("abandoned", "error")) else (
            C.yellow if "interrupted" in counts else C.dim
        )

        last = last_ok.get(script)
        age_str = fmt_age(last)
        stale = ""
        if last and (datetime.now(timezone.utc) - last).total_seconds() / 3600 > STALE_HOURS:
            stale = f"  {C.red}STALE{C.reset}"
        elif not last:
            stale = f"  {C.red}NEVER COMPLETED{C.reset}"

        print(
            f"  {script:<18} "
            f"{ok_color}{ok} ok{C.reset} / "
            f"{bad_color}{bad_str}{C.reset}"
            f"   last ok: {age_str}{stale}"
        )


def section_latest_audit(log_dir):
    files = sorted(glob.glob(str(Path(log_dir) / "audit-*.json")))
    if not files:
        print(f"\n{C.bold}Latest audit:{C.reset} {C.dim}no audit on record yet{C.reset}")
        return

    latest = files[-1]
    try:
        with open(latest) as f:
            report = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"\n{C.bold}Latest audit:{C.reset} {C.red}failed to read {latest}: {exc}{C.reset}")
        return

    overall = report.get("overall", "?")
    overall_color = C.red if overall == "RED" else C.green
    name = Path(latest).stem.replace("audit-", "")
    checks = report.get("checks", [])
    red_count = sum(1 for c in checks if c.get("red"))
    tally = f" ({red_count}/{len(checks)} failing)" if checks else ""
    print(f"\n{C.bold}Latest audit ({name}):{C.reset} {overall_color}{overall}{C.reset}{tally}")
    for c in checks:
        marker = f"{C.red}RED {C.reset}" if c.get("red") else f"{C.green}ok  {C.reset}"
        print(f"  [{marker}] {c.get('name', '?')}: {c.get('summary', '')}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data/prices.db")
    ap.add_argument("--runs", type=int, default=10, help="Number of recent runs to list (default 10)")
    ap.add_argument("--days", type=int, default=7, help="Window for per-script summary (default 7)")
    ap.add_argument("--log-dir", default="data/logs", help="Where audit-*.json files live")
    ap.add_argument("--no-color", action="store_true")
    args = ap.parse_args()

    if args.no_color or not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        disable_color()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"{C.bold}PIPELINE STATUS — {now}{C.reset}\n")

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    try:
        section_per_script(conn, args.days)
        section_recent_runs(conn, args.runs)
    finally:
        conn.close()

    section_latest_audit(args.log_dir)


if __name__ == "__main__":
    main()
