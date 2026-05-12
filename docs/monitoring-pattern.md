# Cron monitoring pattern

Reusable approach for unattended cron-driven pipelines. Built and validated in this project (`hc_run.sh`, `check_runs.py`, `audit_pipeline.py`) but designed to be portable to other data-fetch / batch-job projects.

The goal: turn "cron exited 0" — which is nearly meaningless — into three meaningful signals: **did the wrapper survive?**, **did the job actually do work?**, **is the data healthy?**

## The problem

A typical cron line looks like:

```cron
0 4 * * * /path/script.sh >> /var/log/script.log 2>&1; curl https://hc-ping.com/UUID
```

This has three failure modes that look identical from the outside:

1. **Silent failure** — `;` between command and ping means the ping fires even if the command crashed. Healthchecks.io stays green, you stay unaware.
2. **Empty success** — the command exits 0 but did nothing (network error swallowed, empty result set, partial write). Cron is happy. Data is broken.
3. **Slow data rot** — coverage drops, freshness slips, a single subsystem stops being touched. Each individual run "succeeds" but the dataset degrades over weeks.

Each needs a different layer.

## The pattern

Three layers, each cheap, each composable:

```
[ cron line ]
     │
     ├─► hc_run.sh <uuid> <cmd>      # Layer 1 — wrapper, propagates exit code to /fail
     │       │
     │       └─► <cmd> && check_runs.py    # Layer 2 — post-job verifier, fails on empty success
     │
[ separate cron line ]
     │
     └─► hc_run.sh <uuid> audit_pipeline.py     # Layer 3 — daily data-quality audit
```

### Layer 1 — wrapper script (`scripts/hc_run.sh`)

Trivial bash, ~25 lines. Pings `/start`, runs the command, pings success or `/fail` based on `$?`. Healthchecks.io then handles alerting (email/Slack/Telegram/webhook) — no new infra.

Key contracts:
- Wrapper exit code = wrapped command exit code (so chained cron commands still work).
- All curl calls are best-effort (`|| true`) — a flaky healthchecks.io must not crash the job.
- `HC_RUN_DRYRUN=1` skips curl, for local testing.

Why a wrapper and not inline bash: cron commands grow ugly fast. One reusable wrapper keeps every cron line readable and consistent.

### Layer 2 — post-job verifier (`check_runs.py`)

A persistent `runs` table is the single source of truth. Each cron-run script writes one row:

```sql
runs (id, script, started_at, finished_at, status, records_written, notes)
-- status ∈ {running, completed, interrupted, abandoned, error}
```

`check_runs.py --script X` reads the most recent `status='completed'` row for X and fails on:
- missing row,
- stale `finished_at` (older than `--max-age-hours`),
- `records_written <= 0`.

It runs **inside** the cron wrapper, after the job: `hc_run.sh UUID bash -c 'job && check_runs.py --script X'`. So a job that exits 0 but wrote zero records still trips `/fail`.

Two subtleties that matter:

1. **Pick `finished_at`, not `started_at`.** If the job supports resumes (multi-day checkpoints), `started_at` is anchored to the original day and is the wrong staleness signal.
2. **Honour the lock file.** If the job is still legitimately running (lock file held by a live PID), exit 0 with a note — don't fail it. Verify the PID is alive (`os.kill(pid, 0)`) before trusting the lock.

### Layer 3 — daily data-quality audit (`audit_pipeline.py`)

A separate cron line at a quiet hour. Runs read-only queries against the DB and exits non-zero on RED thresholds. Wrap it with `hc_run.sh` on its own UUID — failures alert just like the fetch jobs.

The exact checks are domain-specific. The structural pattern is the same:

```python
def check_X(conn) -> {"name": str, "red": bool, "summary": str, ...payload}

def run_audit(db_path) -> {"overall": "GREEN"|"RED", "checks": [...]}
```

Each check returns a dict; the runner aggregates them; the overall verdict drives the exit code. Both a human-readable text trail and a JSON summary are persisted to `data/logs/audit-YYYY-MM-DD.{txt,json}` so you can grep history later.

Connection guidance:
- **Read-only URI**: `sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)`. The audit must never block the live fetcher's write lock.
- **Slow checks opt-in**: any check that scans wide tables (per-product loops, full statistical sweeps) goes behind an `--include-X` flag. The daily cron path stays fast.
- **Empty tables are not errors**: if `price_flags` doesn't exist yet or has no rows, the audit returns "ok / skipped", not red.

## Cron layout

```cron
# Fetch — wrapper around (job && verify)
0 4 * * * cd /proj && scripts/hc_run.sh UUID_FETCH bash -c '<job> >> data/logs/fetch.log 2>&1 && python check_runs.py --script <name> --max-age-hours 25 >> data/logs/check.log 2>&1'

# Audit — its own line, its own UUID
0 6 * * * cd /proj && scripts/hc_run.sh UUID_AUDIT bash -c 'python audit_pipeline.py >> data/logs/audit.log 2>&1'
```

One UUID per logical job (combine `job + verify` under one UUID — failure of either trips `/fail`). A separate UUID for the audit keeps fetch-failure noise out of the audit signal.

## Adapting to a new project — checklist

Minimum viable port, in order:

1. **Add a `runs` table** to your DB if you don't have one. Track `script, started_at, finished_at, status, records_written, notes`. Wrap your job's main with `start_run` / `finish_run` and a `try/finally`.
2. **Add `abandon_stale_runs(conn, script)`** that marks `status='running'` rows as `'abandoned'` at every startup. SIGKILL / OOM / unattended-upgrades leave orphans otherwise.
3. **Copy `scripts/hc_run.sh`** verbatim. Make it executable. Test with `HC_RUN_DRYRUN=1`.
4. **Copy `check_runs.py`**. The only thing that should change is the `LOCK_FILES` dict (which scripts have a lock, where).
5. **Stub `audit_pipeline.py`** with one trivial check (e.g. "any row in the last 24h"). Wire it up. Add real domain checks one at a time — each is ~20 lines.
6. **Write the cron template** at `scripts/crontab.template` (in-repo, gitignored or not). Don't install it directly; the user (or deploy step) edits in real UUIDs.
7. **Create healthchecks.io endpoints.** One per logical job. Configure the schedule on each so a *missing* ping also alerts (catches "cron daemon stopped" scenarios that no in-script check can detect).

## Gotchas seen in this project

- **`;` vs `&&`** in cron is the entire bug. `;` runs the next command unconditionally; `&&` only on success. The wrapper exists so you don't have to think about this per-line.
- **Unattended-upgrades on Debian/Ubuntu** can kill long-running cron jobs mid-flight. The `runs` table will show `status='running'` forever without `abandon_stale_runs`. Worth configuring the upgrade window to avoid the cron window if jobs are long.
- **Healthchecks.io ping endpoint paths**: base URL = success; `/start` = job started; `/fail` = job failed. Use all three.
- **Long resumes break naive freshness checks.** A job that resumes a 7-day checkpoint will have `started_at` from a week ago. Always compare against `MAX(finished_at) WHERE status='completed'`, not `started_at`.
- **Daily audit must use a read-only connection** when running against a live SQLite DB. WAL mode + read-only URI lets the audit succeed even while the fetcher holds the write lock.

## What's deliberately NOT in this pattern

- **No log aggregation / no Loki / no ELK.** Plain text + JSON in `data/logs/` is enough for a single-host pipeline. Add structure when you need cross-host correlation, not before.
- **No retry logic in the wrapper.** Retries belong in the job itself (where you can be smart about which failures are retryable). The wrapper just observes.
- **No alerting code.** Healthchecks.io's free tier covers email + 3 webhook integrations. Don't build SMTP/Telegram/etc. unless you've outgrown that.
- **No metrics export.** If you later want time-series (Prometheus, Grafana), the `runs` and `price_flags` tables are already the source of truth — write a small exporter then. Don't preempt it.
