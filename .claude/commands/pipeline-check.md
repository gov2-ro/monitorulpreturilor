---
description: Read-only health check of fetchers, audit, logs, and crontab. Ends with a single GREEN/YELLOW/RED verdict, with trend and drill-down on RED.
---

You are doing a read-only pipeline health check for the monitorulpreturilor repo. Do **not** write, edit, restart, or kill anything. Only inspect.

Run the steps below using parallel Bash calls where independent, then output a single concise report at the end.

## Steps

1. **Status digest** — `source venv/bin/activate && python status.py`. Note latest run, per-script ok/fail, audit verdict line.

2. **In-flight + lock** — `ps -ef | grep -E "fetch_prices|fetch_gas_prices" | grep -v grep` and `ls -la data/prices_fetch.lock 2>/dev/null`. Live PID + lock = healthy slice. Lock with no PID = stale (flag). Also compute time-to-next retail slice (cron `*/30 * * * *`):
   ```bash
   python3 -c "import datetime as dt; n=dt.datetime.utcnow(); m=(30-n.minute%30)%30 or 30; print(f'next retail slice in ~{m}m')"
   ```

   **Backfill progress** — always run when a lock is held (regardless of verdict):
   ```bash
   python3 - <<'PY'
   import json, os, subprocess, datetime as dt
   cp_path = "data/prices_checkpoint.json"
   try:
       cp = json.load(open(cp_path))
       done = len(cp.get("done", []))
       fetched_at = cp.get("fetched_at", "?")
       status = cp.get("status", "?")
       # Estimate total work units: anchors × batches is not stored; use done as lower bound
       # Check if a manual backfill log exists and get last SUMMARY line from it
       last_summary = ""
       for log in ["data/logs/fetch-prices-backfill.log", "data/logs/fetch-prices.log"]:
           if os.path.exists(log):
               result = subprocess.run(["grep", "-a", "SUMMARY", log], capture_output=True, text=True)
               lines = [l for l in result.stdout.strip().splitlines() if "SUMMARY" in l]
               if lines:
                   last_summary = f"  last SUMMARY ({os.path.basename(log)}): {lines[-1].split('SUMMARY')[1].strip()}"
                   break
       print(f"  Checkpoint: {done} done keys  status={status}  fetched_at={fetched_at}")
       if last_summary:
           print(last_summary)
   except FileNotFoundError:
       print("  No checkpoint file found.")
   PY
   ```
   If `fetched_at` date is older than today, flag it: the run is stamping prices with a stale date — a `--fresh` run is needed.

3. **Audit + trend (today vs yesterday vs 7d-ago)** — single python block that loads up to three JSON files and prints today's checks with a day-over-day delta:
   ```bash
   python3 - <<'PY'
   import json, datetime as dt
   today = dt.date.today()
   keys = {"today": today, "y1": today - dt.timedelta(days=1), "y7": today - dt.timedelta(days=7)}
   data = {}
   for k, d in keys.items():
       try: data[k] = json.load(open(f"data/logs/audit-{d}.json"))
       except FileNotFoundError: pass
   if "today" not in data:
       print("MISSING today's audit JSON"); raise SystemExit
   t = data["today"]
   print("overall:", t["overall"])
   for c in t["checks"]:
       sym = "RED" if c["red"] else "ok "
       extras = []
       for k in ("y7", "y1"):
           if k in data:
               prev = next((x for x in data[k]["checks"] if x["name"] == c["name"]), None)
               if prev is None: continue
               if c["name"] == "store_freshness":
                   extras.append(f"{prev['stale_pct']}%")
               else:
                   extras.append("RED" if prev["red"] else "ok")
       extras.append(f"{c.get('stale_pct', 'RED' if c['red'] else 'ok')}{'%' if c['name']=='store_freshness' else ''}")
       print(f"  [{sym}] {c['name']}: {c['summary']}  trend: {' -> '.join(map(str, extras))}")
   PY
   ```
   Missing today's file → YELLOW. Missing yesterday/7d files → just skip that data point in the trend.

4. **Drill-down on RED** — only run if today's overall is RED. For each red check:
   - `store_freshness`: top 5 networks by stale-store count + oldest stale age (uses date-only diff to match the audit predicate, but evaluates *now* — count typically differs from the audit JSON snapshot since the retail cron refreshes throughout the day):
     ```bash
     sqlite3 -separator '|' data/prices.db "WITH sl AS (SELECT store_id, MAX(last_checked_at) AS last_date FROM prices_current GROUP BY store_id) SELECT COALESCE(n.name,'Unknown') net, COUNT(*) stale, MAX(CAST(julianday(date('now'))-julianday(date(sl.last_date)) AS INT)) oldest_d FROM sl JOIN stores s ON s.id=sl.store_id LEFT JOIN retail_networks n ON s.network_id=n.id WHERE julianday(date('now'))-julianday(date(sl.last_date)) > 2 GROUP BY s.network_id ORDER BY stale DESC LIMIT 5;"
     ```
     Label the result as "live stale (now)" vs the audit JSON's "snapshot at HH:MM". A big gap (live << snapshot) means the cron is actively recovering.
   - `run_history`: surface the `samples` array from today's JSON (id, script, status, notes).
   - `coverage_gaps`: surface the `gaps` array.
   - `anomaly_drift`: surface `today_count` vs `baseline_median`.

5. **Log tails** — last 30 lines of `data/logs/{fetch-prices,fetch-gas-prices,audit,check-runs}.log`. Look for `Traceback`, `ERROR`, `5\d\d` HTTP codes, `oom`. Quote one smoking-gun line if found; otherwise "no errors in tail".

6. **Crontab drift** — `diff <(crontab -l | grep -v '^#' | grep -v '^$') <(grep -v '^#' scripts/crontab.template | grep -v '^$')`. Empty = aligned. Flag any drift.

7. **Cron heartbeat** — `stat -c '%y' <log>` or `tail -1` per cron line:
   - retail (`fetch-prices.log`): activity <30 min OR last `completed` <36 h
   - gas (`fetch-gas-prices.log`), audit (`audit.log`), check-runs (`check-runs.log`): <25 h

8. **Log the report** — after composing the final report, append it verbatim to `data/logs/pipeline-check.log` (create dir if missing):
   ```bash
   mkdir -p data/logs
   tee -a data/logs/pipeline-check.log <<'REPORT'
   <full report text here>
   REPORT
   ```
   Use the actual report text in the heredoc. This is a write to a log file only — consistent with the read-only spirit of the check.

## Output format

```
PIPELINE CHECK — <UTC time>
Verdict: GREEN | YELLOW | RED

  In-flight: <PID xxx since HH:MM | none, next retail slice in Xm>
  Backfill:  <N done keys, status=in_progress, fetched_at=YYYY-MM-DD [STALE DATE — needs --fresh]>
             last SUMMARY: stores=N prices=N elapsed=Ns
             (omit entire Backfill line when no lock is held)
  Audit (today): <ok | RED: <which checks>>
  Trend: <store_freshness A% -> B% -> C% (improving/regressing/flat); other-check status>
  Cron drift: <none | <list>>
  Heartbeat: retail Xm, gas Yh, audit Zh, check-runs Wh ago
  Logs: <clean | "<quoted error line>">

[Drill-down — only printed when verdict is RED]
  store_freshness top stale networks:
    PROFI: 412 stale, oldest 5d
    MEGA IMAGE: 198 stale, oldest 4d
    ...
  <other red checks with their data>

<2-3 lines: what (if anything) needs attention; if YELLOW-downgraded, explain why>
```

**Verdict rules:**
- RED: any audit RED check, any cron line >2× expected interval, traceback in last 30 log lines, stale lock with no PID.
- YELLOW: cron drift; missing audit file for today; fetch errors that retried OK; last `completed` >24 h but <36 h.
- GREEN: everything within bounds.

**Trend-aware downgrade (narrow):** if `store_freshness` is the *only* RED check today and its `stale_pct` improved by >20 points day-over-day (active backfill recovery), downgrade RED → YELLOW with note "recovering: A% -> B%". Other RED checks always keep RED.

Keep the final report under ~25 lines including drill-down. No extra sections unless explaining a downgrade.
