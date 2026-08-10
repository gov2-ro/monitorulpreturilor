# Design Spec: `run_history` Auto-Clear for Non-Session Scripts

**Date:** 2026-08-10
**Status:** Approved

---

## Context

`audit_pipeline.py`'s `check_run_history` (line 59) flags the daily audit RED whenever an
`abandoned`/`error` row exists in the `runs` table within the last `ABANDONED_DAYS` (7) days
and hasn't been acknowledged. It already auto-suppresses one specific recovery shape: a
sibling row with the *exact same* `(script, started_at)` that reached `completed`. This
works for `fetch_prices`, which deliberately reuses one `started_at` as a session id across
resumed/interrupted cron ticks within a day.

`fetch_gas_prices` doesn't share that session model — every cron invocation gets a fresh
`started_at` via `start_run()`. So when a gas run hangs and gets reaped by the next day's
`abandon_stale_runs()`, the auto-suppress rule never matches, and the audit stays RED for
the full 7-day window even though the pipeline visibly recovered on its own.

**Concrete case investigated (2026-08-10 `/pipeline-check`):** `fetch_gas_prices` run `#1162`
started 2026-08-07T03:40:27, never reached a terminal status, and sat as `running` for ~24h.
The 2026-08-08T03:40 cron tick called `abandon_stale_runs()`, which marked `#1162` as
`abandoned` (`finished_at=2026-08-08T03:40:14.809`), then immediately started and completed
`#1166` (`started_at=2026-08-08T03:40:14.847`, `finished_at=2026-08-08T03:40:16.751`) — all
within the same cron invocation. Functionally self-healed in under two seconds, but held
`run_history` RED for two audit days until this investigation.

## Goals

- Extend `check_run_history`'s existing suppression logic so scripts without a shared
  session id (gas, and any future script) also get credit for near-immediate self-recovery.
- Preserve the check's purpose: a script that fails and only recovers a day+ later (a real
  operational gap) must still show RED.
- Don't blur the `acknowledged_at` column's meaning — it stays "a human reviewed this."
- Make auto-suppression visible/debuggable, not silent.

## Non-Goals

- Not fixing why `#1162` hung in the first place (no error, no notes). That's a separate
  diagnostic gap, filed to `docs/backlog.md` under Gas / Bugs.
- Not changing `ack_run.py` or the manual acknowledgment workflow.
- Not making the recovery window configurable per script — see Alternatives.

## Design

### Recovery rule

A bad run (`abandoned`/`error`) is excluded from the RED count if a `completed` run of the
same script exists that either:

1. shares the exact same `started_at` (existing rule, unchanged), **or**
2. has a `started_at` within `RUN_RECOVERY_WINDOW_MINUTES` (flat **60**, all scripts) after
   the bad run's `finished_at` (new rule).

60 minutes comfortably covers "reaped and replaced within the same cron tick" for both
gas's daily cadence and retail's 30-minute cadence, while a next-day recovery (~24h later)
falls outside the window and still counts as a real gap.

### SQL change (`audit_pipeline.py`)

```python
RUN_RECOVERY_WINDOW_MINUTES = 60  # a completed run of the same script starting within this
                                    # window after a bad run's finished_at counts as recovered

def check_run_history(conn):
    rows = conn.execute(f"""
        SELECT id, script, status, started_at, finished_at, notes, acknowledged_at
        FROM runs r
        WHERE status IN ('abandoned', 'error')
          AND (finished_at IS NULL OR finished_at >= datetime('now', '-{ABANDONED_DAYS} days'))
          AND acknowledged_at IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM runs r2
              WHERE r2.script = r.script
                AND r2.status = 'completed'
                AND (
                    r2.started_at = r.started_at
                    OR (
                        r.finished_at IS NOT NULL
                        AND datetime(r2.started_at) BETWEEN datetime(r.finished_at)
                                                          AND datetime(r.finished_at, '+{RUN_RECOVERY_WINDOW_MINUTES} minutes')
                    )
                )
          )
        ORDER BY id DESC
    """).fetchall()
    ...
```

No schema change. No write to `runs`. Purely a broader read-side exclusion, same spirit as
the rule it extends.

**Verified empirically:** raw `r2.started_at BETWEEN r.finished_at AND datetime(r.finished_at,
'+60 minutes')` silently fails whenever both timestamps fall on the same calendar day — SQLite's
`datetime()` output uses a space separator (`'2026-08-07 04:41:00'`) while the stored ISO8601
columns use `'T'` and a UTC offset (`'2026-08-07T04:41:00.847081+00:00'`); `'T'` (0x54) sorts
after `' '` (0x20), so the same-day upper-bound comparison is always false. Both sides of the
comparison must go through `datetime(...)` to normalize format before comparing — confirmed
against the real `#1162`/`#1166` timestamps in `sqlite3` before writing the implementation plan.

### Observability: `suppressed` list

Add a `suppressed` key to `check_run_history`'s return dict: a list of bad runs that were
excluded by either rule, each with `id`, `script`, `status`, and `recovered_by` (the id of
the completed sibling that matched). This is informational only — it does not affect `red`,
`summary`, or `bad_run_count`. It flows into `audit-YYYY-MM-DD.json` for free (the report
dict is serialized as-is) and gives `/pipeline-check`'s drill-down step something concrete
to show instead of the check silently going green with no explanation.

Query for the suppressed list reuses the same matching logic as the `NOT EXISTS` clause,
via a small Python join over the already-fetched bad-run candidates (no second complex SQL
statement needed — see plan for exact shape).

### Data flow

1. Audit runs (`audit_pipeline.py`, daily cron + manual `/pipeline-check`).
2. `check_run_history` evaluates the extended query. `#1162` now matches rule 2 against
   `#1166` (`#1166.started_at` = `#1162.finished_at` + 38ms, well inside 60 min) → excluded
   from `red`/`bad_run_count`, included in `suppressed`.
3. JSON/text output unchanged in shape, `suppressed` is additive.
4. `/pipeline-check`'s existing drill-down step (which already reads `run_history`'s
   `samples`) can optionally surface `suppressed` too — not required for this spec, the data
   just needs to be present in the JSON.

### Error handling

No new failure modes — same read-only query pattern as the existing check. The `BETWEEN`
comparison against `r.finished_at IS NOT NULL` guards the only edge case (a bad row with no
`finished_at`, which shouldn't occur in practice since `abandon_stale_runs` and `finish_run`
always set it together with the status, but the existing code already guards this in the
outer `WHERE` clause).

### Testing

New `tests/test_audit_pipeline.py` (pytest + in-memory sqlite via `init_db(":memory:")`,
matching `tests/test_pipeline_report.py`'s convention):

1. **Gas self-heal case** — reproduce `#1162`/`#1166` exactly (abandoned, then completed
   ~1s later under a different `started_at`). Assert `red is False`, `bad_run_count == 0`,
   and the bad run appears in `suppressed` with the correct `recovered_by`.
2. **Genuinely stuck run** — abandoned run with no completed sibling within 60 min (either
   none at all, or only a next-day completed run >60 min later). Assert `red is True` and
   the run appears in `samples`, not `suppressed`.
3. **Existing fetch_prices session case (regression guard)** — same `started_at` shared
   across an `interrupted` and a `completed` row. Assert unchanged behavior (excluded from
   red, as today).
4. **Boundary** — completed run exactly at the 60-minute edge, and one minute past it, to
   pin the `BETWEEN` semantics.

## Alternatives Considered

- **Any later success clears it** — auto-clear as soon as any future run of the script
  completes, regardless of elapsed time. Rejected: for a daily cron, this would clear almost
  every bad run within ~24h regardless of severity, defeating the check's purpose.
- **Consecutive-success streak** — require N consecutive completions before fully clearing.
  Rejected for now: more state to track (a streak counter) for a failure shape that, per the
  investigated case, resolves in seconds — the time-window rule already captures it without
  extra state. Can revisit if flapping becomes a real pattern.
- **Per-script or crontab-derived window** — more precise, but couples the audit to cron
  parsing / a new config surface for marginal benefit, since both current cadences (30 min,
  daily) are already far outside a 60-minute window from a genuine next-day recovery.
- **Auto-write `acknowledged_at`** — would make suppression visible via `ack_run.py --list`,
  but blurs "a human reviewed this" with "the system detected recovery." Rejected in favor of
  keeping the exclusion query-only and adding the `suppressed` list for visibility instead.

## Out of Scope (filed to backlog)

`abandon_stale_runs()` doesn't record *why* a run hung (no `notes`, unlike `error` rows
which capture the exception). We know `#1162` hung, not why. Filed to `docs/backlog.md`
under Gas / Bugs — separate from whether a self-healed run should hold the audit RED.
