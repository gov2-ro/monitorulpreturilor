# Pipeline cadence — how to read `runs` honestly

Reference for what the retail `fetch_prices` cron actually does, what the `runs` table really means, and why the daily-looking job is in fact a multi-day batch. Investigation notes from 2026-05-12.

Related: [`docs/activity-log.md`](activity-log.md) (history of fixes), [`docs/backlog.md`](backlog.md) (open work).

---

## TL;DR

- **Retail is not a daily job.** One full price sweep takes 3–7 daily cron firings to complete. Each firing is a 23 h slice that resumes from `data/prices_checkpoint.json`.
- **`runs.started_at` is misnamed** — it stores the session's `fetched_at`, *reused* across firings. Every row of a multi-firing session shares it. Subtracting `finished_at - started_at` does **not** give process duration.
- **The work itself only takes ~15 h** (29 760 batches × 1.85 s/call, measured). The 3–7 day calendar cost is almost entirely **external kills + waiting for the next 04:00 cron**, not slow code.
- **`abandoned` ≠ "failed"**, **`interrupted` ≠ "failed"** — see the status legend below. The audit's `run_history` check was treating both as failures; fixed 2026-05-12.

---

## The cron is a one-slice-per-day drip

```
0 4 * * *  …  fetch_prices.py --max-runtime 82800  # 82800 s = 23 h
```

Each 04:00 firing:

1. Reclaims any leftover `running` rows from a previous kill → marks them `abandoned` (`db.py:abandon_stale_runs`, `fetch_prices.py:291`).
2. Loads the checkpoint at `data/prices_checkpoint.json`. If it's in-progress, **reuses the existing `fetched_at`** as the new run's `started_at` (`fetch_prices.py:316`, `:404`).
3. Iterates remaining anchors × batches until either:
   - finishes the session → `status='completed'` (`fetch_prices.py:488`)
   - hits the 23 h cap → `status='interrupted'` (`fetch_prices.py:416`)
   - is killed externally → row stays `running` until next firing flips it to `abandoned`

So one logical session emits N rows in `runs`, all sharing `started_at`. Only the last one (`completed`) tells you the session is done. The siblings are bookkeeping.

## `started_at` is really `fetched_at`

This is the trap. `started_at` in the schema sounds like a process start time, but the code passes `fetched_at` into it (`db.py:start_run` is dumb; the caller decides). Consequences:

- `finished_at - started_at` measures the **session span** (data-stamp to finish), not how long the process ran.
- `status.py` shows "5d 17h duration" for the May-7 session because it's doing that subtraction blindly.
- Anything that wants real process time would need to record a separate `process_started_at`. See `docs/backlog.md` → "Multiple runs rows per logical run" for the architectural fix (skip `start_run` on resume).

## Status legend

| Status | What it means | Action signal? |
|---|---|---|
| `running` | Process is (claimed to be) alive right now | Not a finished row; ignore in summaries |
| `completed` | Session reached end-of-anchors cleanly | Green |
| `interrupted` | Graceful `--max-runtime` exit between anchors | Normal mid-session checkpoint; **not** a failure |
| `abandoned` | Next firing found a stale `running` row and reclaimed it. Means: previous process was killed externally without calling `finish_run` | Real signal of an external kill — but routine if the same session also has a later `completed` |
| `error` | An exception escaped `_main_body` | Real failure |

**Audit rule (post-2026-05-12 fix):** `abandoned`/`error` rows are only counted when no row with the same `(script, started_at)` later reaches `completed`. If the session ultimately succeeded, mid-flight cleanup rows are ignored. See `audit_pipeline.py:check_run_history`.

## Why a sweep takes 3–7 days

Measured cost of one full sweep (from `data/prices_checkpoint.json` after the 2026-05-07 session):

| Quantity | Value |
|---|---|
| Stores in DB | 4 094 |
| Anchors after 5 km clustering | 683 |
| Active products (post ghost-filter) | 87 327 |
| `BATCH_SIZE` | 200 |
| Total API calls per sweep | **29 760** |
| Measured avg request time | **1.70 s** (live test, 3 stores × 3 batches) |
| `SLEEP_BETWEEN` per call | 0.15 s |
| Pure-work estimate | **~15.3 h** |

15.3 h fits inside the 23 h `--max-runtime` budget. **So the cron *could* finish in one firing.** It doesn't, because:

1. **External kills truncate firings early.** Confirmed mechanisms on this host:
   - System reboots (e.g. `last reboot` shows 2026-05-12 08:04 boot, ending the previous 23-day uptime mid-session).
   - OOM killer activity (`journalctl --user` shows `oom-kill` events on `app.slice` around 2026-05-12 11:30).
   - `unattended-upgrades` historically (already in backlog).
2. **Dead time between firings is huge.** A kill at 08:00 means the next firing isn't until 04:00 the following day — 20 h of idle wall-clock per cycle.
3. The May-7 session needed 7 firings (5 abandoned, 1 interrupted, 1 completed). Most of the 6-day span was waiting, not working.

**The cadence is bound by kills, not by code.** Tuning batch size or sleep won't move the needle much — surviving a single 16-h window will.

## Recommended fixes (ranked by impact)

1. ~~**Run under a supervisor that restarts on crash.**~~ **Implemented 2026-05-12** as a re-entrant `*/30` cron with `--max-runtime 1700`; see `scripts/crontab.template` and the activity-log entry. The script's existing lock + checkpoint logic already supported this; no code changes. Kill recovery dropped from 24 h to ~30 min. Reboots still cost up to one cron tick (~30 min) plus boot time. A proper systemd unit (Option B in the original analysis) is the next step if reboot recovery needs to be faster.
2. **Investigate the OOM.** 87 k product IDs + cluster maps + 50 stores × ~200 prices per response → not obviously huge, but worth a `tracemalloc` pass. Backlog already tracks the 12× product spike that drove the work up.
3. **Reduce work, per existing backlog:** larger `BATCH_SIZE`, recency-filter products, validate the product catalog growth.
4. **Tighten `runs` schema** (backlog: "Multiple runs rows per logical run"). Either:
   - Don't call `start_run` on resume — update the existing row's `finished_at`/`status` and add a separate `process_started_at` column for the slice clock.
   - Or rename `started_at` → `session_id`/`fetched_at` and add an explicit `process_started_at`.

## Reading the pipeline today

A practical decoder:

- **`status.py`** is honest about counts but its `duration` column is the misleading session span. Don't trust it for retail.
- **`AUDIT run_history`** is now correct after the 2026-05-12 fix — only flags real unrecovered failures.
- **"Last completed `fetch_prices`"** is the only reliable cadence signal. If the most recent `completed` is >7 days old, the pipeline is truly stuck. Within 7 days, the noise of intermediate rows means nothing about health — only the next `completed` does.
