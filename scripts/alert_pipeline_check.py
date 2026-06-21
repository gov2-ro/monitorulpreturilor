#!/usr/bin/env python3
"""
Exit 1 (triggering hc_run.sh fail-ping) when the most recent pipeline-check verdict is RED.

Reads the last PIPELINE CHECK entry from data/logs/pipeline-check.log.
Exits 0 when: no log found, last entry is >25h old, or verdict is YELLOW/GREEN.

Wire into cron after a suitable daily time (e.g. 07:15) via hc_run.sh with a dedicated
HC.io check (period=1d, grace=2h). When this exits 1, HC.io fires its alert channel.
"""

import re
import sys
import os
import datetime as dt


def main():
    log_path = "data/logs/pipeline-check.log"
    if not os.path.exists(log_path):
        print("OK  no pipeline-check log found", flush=True)
        sys.exit(0)

    text = open(log_path).read()
    entries = re.split(r'(?=^PIPELINE CHECK)', text, flags=re.MULTILINE)
    entries = [e.strip() for e in entries if e.strip()]
    if not entries:
        print("OK  no pipeline-check entries in log", flush=True)
        sys.exit(0)

    last = entries[-1]

    ts_match = re.search(r'PIPELINE CHECK — (\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC)', last)
    if ts_match:
        ts = dt.datetime.strptime(ts_match.group(1), "%Y-%m-%d %H:%M UTC").replace(
            tzinfo=dt.timezone.utc
        )
        age_h = (dt.datetime.now(dt.timezone.utc) - ts).total_seconds() / 3600
        if age_h > 25:
            print(f"OK  last pipeline-check is {age_h:.1f}h old (>25h, not alerting)", flush=True)
            sys.exit(0)

    verdict_match = re.search(r'^Verdict:\s*(\w+)', last, re.MULTILINE)
    if not verdict_match:
        print("OK  no verdict found in last pipeline-check entry", flush=True)
        sys.exit(0)

    verdict = verdict_match.group(1)
    if verdict != "RED":
        print(f"OK  pipeline-check verdict: {verdict}", flush=True)
        sys.exit(0)

    print(f"RED pipeline-check verdict is RED — alerting", flush=True)
    sys.exit(1)


if __name__ == "__main__":
    main()
