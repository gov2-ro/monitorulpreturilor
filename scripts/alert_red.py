#!/usr/bin/env python3
"""
Exit 1 (triggering an hc_run.sh fail-ping) when today's audit verdict is RED.

Wire into cron after audit_pipeline.py via hc_run.sh with a dedicated HC.io check
(period=1d, grace=30m). When this exits 1, HC.io fires its alert channel (email/Slack/etc).

Exits 0 (no alert) when: audit file missing (not yet run), YELLOW, or GREEN.
"""

import json
import sys
import datetime as dt


def main():
    today = dt.date.today()
    path = f"data/logs/audit-{today}.json"
    try:
        data = json.load(open(path))
    except FileNotFoundError:
        # Audit hasn't run yet — don't false-alarm
        print(f"OK  no audit file yet for {today}", flush=True)
        sys.exit(0)

    overall = data.get("overall", "UNKNOWN")
    if overall != "RED":
        print(f"OK  pipeline audit {today}: {overall}", flush=True)
        sys.exit(0)

    red_checks = [c for c in data.get("checks", []) if c.get("red")]
    print(f"RED pipeline audit {today} — {len(red_checks)} check(s) failing:", flush=True)
    for c in red_checks:
        print(f"  [{c['name']}] {c.get('summary', '')}", flush=True)
    sys.exit(1)


if __name__ == "__main__":
    main()
