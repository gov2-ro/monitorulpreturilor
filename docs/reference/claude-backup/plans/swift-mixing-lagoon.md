# Monitoring & Audit Strategy for monitorulpreturilor

## Context

This pipeline runs unattended via cron on a VPS. Today's audit revealed three structural issues:
1. **Silent failures**: healthchecks.io pings fire unconditionally (via cron `;`), so the dashboard shows green even when a fetch crashed. Gas had no ping at all.
2. **Logs are scattered**: cron writes to `~/g2-dev/logs/` (external), manual runs write to project-local `logs/`. `fetch-prices.log` grew to 121 MB unbounded.
3. **No data-quality audit**: a fetch can "complete" with zero records, missing networks, or stale stores — and no one knows until the user looks at the site.

The goal is a small, durable monitoring layer that uses what's already there (`runs` table, `generate_pipeline_report.py`, healthchecks.io) and adds two thin scripts plus a wrapper. No new infra dependencies.

User-confirmed decisions: logs in `data/logs/`, alerts via healthchecks.io `/fail` pings, tiered audit (fast post-fetch + slow daily). venv work deferred.

---

## Approach

Four components, in increasing scope:

### A. Move logs to `data/logs/`
- Create `data/logs/` (auto-gitignored by `data/*`).
- Update all cron lines to write to `data/logs/<script>.log` instead of `~/g2-dev/logs/`.
- One-time migration of existing logs is optional; recommend starting fresh in the new location.
- Add `/etc/logrotate.d/monitorulpreturilor` with `size 50M, rotate 5, compress, copytruncate` (copytruncate matters because fetch_prices holds the FD open for ~23h via shell redirect).

### B. `check_runs.py` — fast post-fetch verification
**New file**: `/home/pax/g2-dev/monitorulpreturilor/check_runs.py`

CLI: `python check_runs.py --script fetch_prices --max-age-hours 25`

Verification (per Plan agent correction — `started_at` is anchored to the original `fetched_at` on resumes, so it's the wrong signal):

```python
# Primary: most recent COMPLETED finish for this script
row = conn.execute("""
    SELECT finished_at, records_written, status, notes
    FROM runs
    WHERE script = ? AND status = 'completed'
    ORDER BY id DESC LIMIT 1
""", (script,)).fetchone()
```

- Pass: `finished_at` within `max_age_hours` AND `records_written > 0`.
- Fail: missing row, stale `finished_at`, zero records, or notes contain error markers.
- Special case: if `data/prices_fetch.lock` exists, treat as "still running" (exit 0 with a note) — don't fail a long resume that's mid-flight.

Exits 0 on healthy, 1 on unhealthy. Prints a one-line summary.

### C. `audit_pipeline.py` — daily deep audit
**New file**: `/home/pax/g2-dev/monitorulpreturilor/audit_pipeline.py`

Imports from `generate_pipeline_report.py` (the loaders are pure functions, no refactor needed):
```python
from generate_pipeline_report import (
    load_store_freshness, load_run_stats, compute_outlier_summary,
    compute_price_velocity, load_promo_sanity_issues,
    STALE_DAYS, OUTLIER_Z, PROMO_DEPTH_PCT,
)
```

Checks (red threshold = exit 1):
1. **Store freshness**: >10% of stores stale (>STALE_DAYS old).
2. **Run history**: any `abandoned` or `error` row in last 7 days.
3. **Coverage gaps**: any retail network with zero `prices_current` rows in the last 7 days.
4. **Anomaly drift**: `price_flags` count today >3× the 30-day median.

Outputs:
- Text trail: `data/logs/audit-YYYY-MM-DD.txt`
- JSON summary: `data/logs/audit-YYYY-MM-DD.json` (for grep/aggregate later)
- Exit code drives healthcheck ping.

### D. `scripts/hc_run.sh` — healthcheck wrapper
**New file**: `/home/pax/g2-dev/monitorulpreturilor/scripts/hc_run.sh`

```bash
#!/usr/bin/env bash
# Usage: hc_run.sh <healthcheck-uuid> <command...>
# Pings /start, runs the command, pings success or /fail based on exit code.
set -u
UUID="$1"; shift
PING="https://hc-ping.com/${UUID}"
curl -fsS -m 10 --retry 3 -o /dev/null "${PING}/start" || true
"$@"
status=$?
if [ $status -eq 0 ]; then
    curl -fsS -m 10 --retry 3 -o /dev/null "${PING}" || true
else
    curl -fsS -m 10 --retry 3 -o /dev/null --data-raw "exit=$status" "${PING}/fail" || true
fi
exit $status
```

One healthcheck UUID per cron line (combined fetch + check). The wrapper only pings success if BOTH the fetch and the post-check exit 0 — failure of either trips `/fail`. The user creates new UUIDs in the healthchecks.io UI for: gas (currently has none) and audit (new).

### E. Cron rewrite
New shape (one line per logical job, all using the wrapper):

```cron
0 4 * * * cd /home/pax/g2-dev/monitorulpreturilor && scripts/hc_run.sh UUID_RETAIL bash -c 'source venv/bin/activate && python fetch_prices.py --max-runtime 82800 >> data/logs/fetch-prices.log 2>&1 && python check_runs.py --script fetch_prices --max-age-hours 25 >> data/logs/check-runs.log 2>&1'
0 3 * * * cd /home/pax/g2-dev/monitorulpreturilor && scripts/hc_run.sh UUID_GAS bash -c 'source venv/bin/activate && python fetch_gas_prices.py >> data/logs/fetch-gas-prices.log 2>&1 && python check_runs.py --script fetch_gas_prices --max-age-hours 25 >> data/logs/check-runs.log 2>&1'
0 6 * * * cd /home/pax/g2-dev/monitorulpreturilor && scripts/hc_run.sh UUID_AUDIT bash -c 'source venv/bin/activate && python audit_pipeline.py >> data/logs/audit.log 2>&1 && python generate_pipeline_report.py >> data/logs/audit.log 2>&1'
0 3 * * 1 cd /home/pax/g2-dev/monitorulpreturilor && scripts/hc_run.sh UUID_REFERENCE bash -c 'source venv/bin/activate && python fetch_reference.py >> data/logs/fetch-reference.log 2>&1 && python fetch_gas_reference.py >> data/logs/fetch-gas-reference.log 2>&1'
```

Audit runs at 06:00 (after gas at 03:00 and retail at 04:00 have had time to start). Sumal cron line (different project) left untouched — outside this scope.

---

## Files

| Action | Path |
|---|---|
| Create | `check_runs.py` |
| Create | `audit_pipeline.py` |
| Create | `scripts/hc_run.sh` (chmod +x) |
| Create | `data/logs/` (mkdir) |
| Update | crontab (via `crontab -e`) |
| Update | `readme.md` — add "Monitoring" section under "Cron layout" |
| Update | `docs/activity-log.md` — entry for the changes |
| Optional | `/etc/logrotate.d/monitorulpreturilor` (sudo, separate step) |

Reusable functions (already exist in `generate_pipeline_report.py`):
- `load_store_freshness(conn, stale_days)` — line ~31
- `load_run_stats(conn, limit)` — line ~64
- `compute_outlier_summary(conn, z_threshold)` — line ~76
- `compute_price_velocity(conn)` — line ~152
- `load_promo_sanity_issues(conn)` — line ~177
- Constants: `STALE_DAYS=2`, `OUTLIER_Z=3.0`, `PROMO_DEPTH_PCT=20.0`

Reusable from `db.py`:
- Schema for `runs` table — line 142
- `abandon_stale_runs` already added today

---

## Verification

1. **Dry-run the new scripts** (no DB writes):
   ```bash
   source venv/bin/activate
   python check_runs.py --script fetch_prices --max-age-hours 25   # exits 0 or 1, prints summary
   python check_runs.py --script fetch_gas_prices --max-age-hours 25
   python audit_pipeline.py                                          # writes data/logs/audit-*.{txt,json}
   ```

2. **Test the wrapper locally** before installing the cron:
   ```bash
   # Success path (uses a test UUID or HC_RUN_DRYRUN=1 to skip curl):
   scripts/hc_run.sh test-uuid python -c 'print("ok")'
   # Failure path:
   scripts/hc_run.sh test-uuid python -c 'import sys; sys.exit(1)'
   ```

3. **End-to-end after install**:
   - Wait for the next cron tick (03:00 gas).
   - Confirm `data/logs/fetch-gas-prices.log` was created and ends with a fresh SUMMARY.
   - Confirm the healthchecks.io UI shows the matching UUID as green.
   - Force a failure: `mv data/prices.db data/prices.db.bak`, wait for retail cron, confirm `/fail` ping arrives and you receive the alert (email/webhook). Restore the DB.

4. **Logrotate** (if installed):
   ```bash
   sudo logrotate -d /etc/logrotate.d/monitorulpreturilor    # debug, no changes
   sudo logrotate -f /etc/logrotate.d/monitorulpreturilor    # force run
   ```

---

## Notes / out of scope

- **venv cleanup deferred** per user choice. CLAUDE.md still references `~/devbox/envs/240826/`, which is stale; addressed in a future round.
- **Existing log migration**: `fetch-prices.log` (121 MB) and friends stay where they are. New writes go to `data/logs/`. The old files can be archived/deleted manually later.
- **Sumal cron** (different project, `prometeu/sumal-inspectorul-padurii.py`): untouched here. Apply the same wrapper pattern there if desired in a separate change.
- **New healthcheck UUIDs**: the user creates `UUID_GAS` and `UUID_AUDIT` in the healthchecks.io UI before the cron is installed. The existing `48b9dd2d-…` becomes `UUID_RETAIL`; `09407697-…` becomes `UUID_REFERENCE`.
