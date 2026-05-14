---
description: Read-only health check of fetchers, audit, logs, and crontab. Ends with a single GREEN/YELLOW/RED verdict.
---

You are doing a read-only pipeline health check for the monitorulpreturilor repo. Do **not** write, edit, restart, or kill anything. Only inspect.

Run the steps below, then output a single concise report at the end. Use parallel Bash calls where the steps don't depend on each other.

## Steps

1. **Pipeline status digest** — run `source venv/bin/activate && python status.py`. Note the latest run, per-script ok/fail counts, and the audit verdict line.

2. **In-flight processes & lock** — `ps -ef | grep -E "fetch_prices|fetch_gas_prices" | grep -v grep` and `ls -la data/prices_fetch.lock 2>/dev/null`. A live PID + lock = healthy in-flight slice. Lock with no PID = stale lock (worth flagging).

3. **Today's audit JSON** — read `data/logs/audit-$(date -u +%F).json` if it exists. Surface every check where `red: true` with its `summary`. If the file is missing, that itself is a YELLOW signal (audit cron didn't run today).

4. **Recent log tails** — last 30 lines each of `data/logs/fetch-prices.log`, `data/logs/fetch-gas-prices.log`, `data/logs/audit.log`, `data/logs/check-runs.log`. Look for `Traceback`, `ERROR`, `5\d\d` HTTP codes, or `oom`. Quote the smoking gun if found; otherwise say "no errors in tail".

5. **Crontab drift** — diff active crontab vs the template:
   ```bash
   diff <(crontab -l | grep -v '^#' | grep -v '^$') <(grep -v '^#' scripts/crontab.template | grep -v '^$')
   ```
   Empty diff = aligned. Flag any drift (missing line, changed UUID, changed path).

6. **Cron heartbeat** — for each cron line, find the last log entry and check it's within the expected interval:
   - retail (`fetch-prices.log`): expect activity within last ~30 min during a session, or last `completed` run within 36 h
   - gas (`fetch-gas-prices.log`): expect within last 25 h
   - audit (`audit.log`): expect within last 25 h
   - check-runs (`check-runs.log`): expect within last 25 h
   Use `stat -c '%y' <file>` for last-modified, or `tail -1` for content recency.

## Output format

End with one short report. Don't dump raw output — synthesize.

```
PIPELINE CHECK — <UTC time>
Verdict: GREEN | YELLOW | RED

  In-flight: <fetch_prices PID xxx, started HH:MM | none>
  Audit (today): <ok | RED: <which checks>>
  Cron drift: <none | <list>>
  Heartbeat: <retail Xm ago, gas Yh ago, audit Zh ago>
  Logs: <clean | <quoted error>>

<2-3 lines on what (if anything) needs attention>
```

**Verdict rules:**
- RED: any audit RED check, any cron line with no heartbeat in 2× expected interval, any traceback in last 30 log lines, stale lock with no PID.
- YELLOW: cron drift, missing audit file for today, fetch errors that retried OK, last completed run >24h but <36h ago.
- GREEN: everything within bounds.

Keep the final report under ~15 lines. No headers/sections beyond the block above unless something needs explaining.
