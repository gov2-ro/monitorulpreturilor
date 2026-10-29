#!/usr/bin/env python3
"""
Exit 1 (triggering an hc_run.sh fail-ping) when today's audit verdict is RED.

Wire into cron after audit_pipeline.py via hc_run.sh with a dedicated HC.io check
(period=1d, grace=30m). When this exits 1, HC.io fires its alert channel (email/Slack/etc).

Exits 1 also when today's audit file is missing (audit crashed / did not run) — schedule this
at least ~30 min after the audit cron (audit 06:00 -> alert_red 06:30).
Exits 0 (no alert) when the audit is YELLOW or GREEN.
"""

import json
import sys
import datetime as dt


def red_streak(today):
    """Count consecutive days (including today) with overall == RED, walking backwards
    until a day is missing or not RED."""
    streak = 0
    day = today
    while True:
        try:
            data = json.load(open(f"data/logs/audit-{day}.json"))
        except FileNotFoundError:
            break
        if data.get("overall") != "RED":
            break
        streak += 1
        day -= dt.timedelta(days=1)
    return streak


def main():
    today = dt.date.today()
    path = f"data/logs/audit-{today}.json"
    try:
        data = json.load(open(path))
    except FileNotFoundError:
        # Must be scheduled AFTER the audit finishes (it takes ~8-10 min; cron 06:00 -> run this
        # at 06:30). A missing file then means the audit crashed or never ran (e.g. disk full) —
        # that used to be reported as OK, hiding a 4-day outage (2026-10-26..29).
        print(f"RED no audit file for {today} — audit_pipeline.py crashed or did not run", flush=True)
        try:
            tail = [l for l in open("data/logs/audit.log").read().splitlines() if l.strip()][-1]
            print(f"  last audit.log line: {tail[:200]}", flush=True)
        except (OSError, IndexError):
            pass
        sys.exit(1)

    overall = data.get("overall", "UNKNOWN")
    if overall != "RED":
        print(f"OK  pipeline audit {today}: {overall}", flush=True)
        sys.exit(0)

    streak = red_streak(today)
    streak_note = " (RED for 1st day)" if streak <= 1 else f" (RED for {streak} consecutive days)"
    red_checks = [c for c in data.get("checks", []) if c.get("red")]
    print(f"RED pipeline audit {today} — {len(red_checks)} check(s) failing{streak_note}:", flush=True)
    for c in red_checks:
        print(f"  [{c['name']}] {c.get('summary', '')}", flush=True)
    sys.exit(1)


if __name__ == "__main__":
    main()
