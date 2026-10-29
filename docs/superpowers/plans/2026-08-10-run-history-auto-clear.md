# Run History Auto-Clear Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `audit_pipeline.py`'s `run_history` check so a bad run (`abandoned`/`error`)
is auto-excluded from the audit's RED verdict when the same script has a `completed` run
starting within 60 minutes after the bad run's `finished_at` — not just an exact
`started_at` match (today's only rule, which only covers `fetch_prices`'s session-reuse
pattern).

**Architecture:** Single-function change in `audit_pipeline.py`. `check_run_history` first
fetches all bad-run candidates, then for each one runs a small parameterized lookup query to
find a recovering `completed` sibling (exact `started_at` match, or `completed.started_at`
within 60 minutes after the bad run's `finished_at`). Unrecovered candidates make up `red`/
`bad_run_count`/`samples` (unchanged shape); recovered ones go into a new `suppressed` list
for visibility. No schema change, no write to `runs`.

**Tech Stack:** Python 3.12, `sqlite3` (stdlib), `pytest` (not currently in
`requirements.txt` — install manually with `pip install pytest`; this gap is filed to
`docs/backlog.md` separately and is NOT part of this plan).

## Global Constraints

- `RUN_RECOVERY_WINDOW_MINUTES = 60`, flat constant, applies to all scripts — from
  `docs/superpowers/specs/2026-08-10-run-history-auto-clear-design.md`.
- No writes to `runs.acknowledged_at` — the exclusion is query-only so `ack_run.py`'s
  human-review audit trail keeps meaning "a human reviewed this."
- No schema changes.
- The existing exact-`started_at` match rule (fetch_prices session dedup) must keep working
  unchanged — regression-tested, not just assumed.
- Any timestamp comparison against SQLite's `datetime()` output MUST wrap both sides in
  `datetime(...)`. Verified empirically: the stored columns are ISO8601 with `'T'` and a
  UTC offset (e.g. `'2026-08-07T04:41:00.847081+00:00'`), while `datetime()`'s output is
  space-separated with no offset (e.g. `'2026-08-07 04:41:00'`). Comparing one normalized
  and one raw silently fails same-day comparisons because `'T'` (0x54) sorts after `' '`
  (0x20) — this is not a hypothetical, it made the very case this fix targets (`#1162`/
  `#1166`, same calendar day) never match.

---

## File Map

| File | Action |
|------|--------|
| `audit_pipeline.py` | Modify — add `RUN_RECOVERY_WINDOW_MINUTES` constant (near `ABANDONED_DAYS`, line ~37) and rewrite `check_run_history` (lines 59-88) |
| `tests/test_audit_pipeline.py` | Create |

---

### Task 1: Extend `check_run_history` with time-window recovery + `suppressed` list

**Files:**
- Modify: `audit_pipeline.py:36-39` (thresholds block), `audit_pipeline.py:59-88` (`check_run_history`)
- Test: `tests/test_audit_pipeline.py` (create)

**Interfaces:**
- Consumes: `db.init_db(path)` (from `db.py`) → `sqlite3.Connection` with a `runs` table
  having columns `(id, script, started_at, finished_at, status, uats_processed,
  records_written, notes, acknowledged_at)`. `id` is `INTEGER PRIMARY KEY AUTOINCREMENT`,
  everything else is `TEXT`/`INTEGER` and nullable.
- Produces: `check_run_history(conn) -> dict` with keys:
  - `name: str` (always `"run_history"`)
  - `red: bool`
  - `summary: str`
  - `bad_run_count: int`
  - `samples: list[dict]` — up to 5, each `{"id": int, "script": str, "status": str, "notes": str|None, "acknowledged": bool}` (unchanged shape from before this task)
  - `suppressed: list[dict]` — **new**, each `{"id": int, "script": str, "status": str, "recovered_by": int}`
  - `window_days: int`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_audit_pipeline.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from db import init_db
from audit_pipeline import check_run_history


@pytest.fixture
def db():
    return init_db(":memory:")


def _insert_run(conn, id, script, started_at, finished_at, status, notes=None):
    conn.execute(
        "INSERT INTO runs (id, script, started_at, finished_at, status, notes) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (id, script, started_at, finished_at, status, notes),
    )
    conn.commit()


def test_gas_self_heal_within_window_not_red(db):
    # fetch_gas_prices #1162: hung ~24h, reaped and replaced within the same
    # cron tick the next day. started_at differs (no session-id reuse like
    # fetch_prices has), but #1166 completed well within 60 min of #1162's
    # finished_at.
    _insert_run(db, 1162, "fetch_gas_prices",
                "2026-08-07T03:40:27.715025+00:00",
                "2026-08-08T03:40:14.809751+00:00", "abandoned")
    _insert_run(db, 1166, "fetch_gas_prices",
                "2026-08-08T03:40:14.847081+00:00",
                "2026-08-08T03:40:16.751905+00:00", "completed")

    result = check_run_history(db)

    assert result["red"] is False
    assert result["bad_run_count"] == 0
    assert result["samples"] == []
    assert result["suppressed"] == [
        {"id": 1162, "script": "fetch_gas_prices", "status": "abandoned", "recovered_by": 1166}
    ]


def test_genuinely_stuck_run_stays_red(db):
    # Abandoned run whose next completed run is a full calendar day later —
    # well outside the 60-minute recovery window. Must still flag RED.
    _insert_run(db, 200, "fetch_gas_prices",
                "2026-08-07T03:40:00+00:00",
                "2026-08-07T05:40:00+00:00", "abandoned")
    _insert_run(db, 210, "fetch_gas_prices",
                "2026-08-08T03:40:00+00:00",
                "2026-08-08T03:59:00+00:00", "completed")

    result = check_run_history(db)

    assert result["red"] is True
    assert result["bad_run_count"] == 1
    assert result["samples"][0]["id"] == 200
    assert result["suppressed"] == []


def test_fetch_prices_session_match_unchanged(db):
    # Existing rule (exact same started_at as a completed sibling) must keep
    # working — fetch_prices reuses started_at as a session id across
    # resumed cron ticks. An earlier attempt in the session hit a transient
    # DB-lock error; a later attempt in the same session (same started_at,
    # hours later) completed.
    session = "2026-08-05T04:00:00+00:00"
    _insert_run(db, 300, "fetch_prices", session,
                "2026-08-05T04:05:00+00:00", "error", notes="database is locked")
    _insert_run(db, 301, "fetch_prices", session,
                "2026-08-05T06:10:00+00:00", "completed")

    result = check_run_history(db)

    assert result["red"] is False
    assert result["bad_run_count"] == 0
    assert result["suppressed"] == [
        {"id": 300, "script": "fetch_prices", "status": "error", "recovered_by": 301}
    ]


def test_recovery_window_boundary(db):
    # Exactly at the 60-minute edge: still counts (BETWEEN is inclusive).
    _insert_run(db, 400, "fetch_gas_prices",
                "2026-08-07T03:40:00+00:00",
                "2026-08-07T03:41:00+00:00", "error", notes="timeout")
    _insert_run(db, 401, "fetch_gas_prices",
                "2026-08-07T04:41:00+00:00",  # exactly +60min from finished_at
                "2026-08-07T04:45:00+00:00", "completed")

    result = check_run_history(db)
    assert result["red"] is False
    assert result["suppressed"] == [
        {"id": 400, "script": "fetch_gas_prices", "status": "error", "recovered_by": 401}
    ]


def test_recovery_window_just_past_boundary_stays_red(db):
    # One minute past the edge: no longer counts, stays red.
    _insert_run(db, 402, "fetch_gas_prices",
                "2026-08-07T03:40:00+00:00",
                "2026-08-07T03:41:00+00:00", "error", notes="timeout")
    _insert_run(db, 403, "fetch_gas_prices",
                "2026-08-07T04:42:00+00:00",  # +61min from finished_at
                "2026-08-07T04:45:00+00:00", "completed")

    result = check_run_history(db)
    assert result["red"] is True
    assert result["bad_run_count"] == 1
    assert result["suppressed"] == []


def test_acknowledged_run_excluded_regardless_of_recovery(db):
    # A human-acknowledged bad run must stay excluded even with no recovery
    # sibling at all — acknowledged_at is a separate, unrelated exclusion
    # that this task must not disturb.
    db.execute(
        "INSERT INTO runs (id, script, started_at, finished_at, status, acknowledged_at) "
        "VALUES (500, 'fetch_gas_prices', '2026-08-01T03:40:00+00:00', "
        "'2026-08-01T04:00:00+00:00', 'abandoned', datetime('now'))"
    )
    db.commit()

    result = check_run_history(db)
    assert result["red"] is False
    assert result["bad_run_count"] == 0
    assert result["suppressed"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pip install pytest` (once, if not already installed), then:
```bash
source venv/bin/activate
python -m pytest tests/test_audit_pipeline.py -v
```
Expected: `ModuleNotFoundError: No module named 'audit_pipeline'` is NOT expected (the module
exists today) — instead expect `ImportError`/`AttributeError` on `suppressed` key lookups,
or `KeyError: 'suppressed'` from the assertions, since `check_run_history` doesn't return
that key yet. `test_genuinely_stuck_run_stays_red` and `test_acknowledged_run_excluded_regardless_of_recovery`
may already pass (existing behavior) — that's fine, only the `suppressed`-asserting tests
must fail.

- [ ] **Step 3: Implement the change in `audit_pipeline.py`**

In the thresholds block (near line 37), add the new constant next to `ABANDONED_DAYS`:

```python
ABANDONED_DAYS = 7       # any abandoned/error run in last N days → red
RUN_RECOVERY_WINDOW_MINUTES = 60  # a completed run of the same script starting within this
                                    # window after a bad run's finished_at counts as recovered
```

Replace the full body of `check_run_history` (lines 59-88) with:

```python
def check_run_history(conn):
    # A bad run (abandoned/error) is excluded from the count if the same script has a
    # completed run that either (a) shares its exact started_at — fetch_prices reuses
    # started_at as a session id across resumed cron ticks, so a completed sibling means
    # the abandoned/error rows are normal mid-session cleanup — or (b) started within
    # RUN_RECOVERY_WINDOW_MINUTES after the bad run's finished_at, which covers scripts
    # like fetch_gas_prices that don't share a session id: a hung run gets reaped by
    # abandon_stale_runs() and immediately replaced by a fresh completed run.
    candidates = conn.execute(f"""
        SELECT id, script, status, started_at, finished_at, notes, acknowledged_at
        FROM runs
        WHERE status IN ('abandoned', 'error')
          AND (finished_at IS NULL OR finished_at >= datetime('now', '-{ABANDONED_DAYS} days'))
          AND acknowledged_at IS NULL
        ORDER BY id DESC
    """).fetchall()

    bad, suppressed = [], []
    for r in candidates:
        run_id, script, status, started_at, finished_at, notes, acknowledged_at = r
        recovered = conn.execute("""
            SELECT id FROM runs
            WHERE script = ?
              AND status = 'completed'
              AND (
                  started_at = ?
                  OR (
                      ? IS NOT NULL
                      AND datetime(started_at) BETWEEN datetime(?) AND datetime(?, ?)
                  )
              )
            ORDER BY id ASC
            LIMIT 1
        """, (script, started_at,
              finished_at, finished_at, finished_at,
              f'+{RUN_RECOVERY_WINDOW_MINUTES} minutes')).fetchone()
        if recovered:
            suppressed.append({"id": run_id, "script": script, "status": status,
                                "recovered_by": recovered[0]})
        else:
            bad.append(r)

    red = len(bad) > 0
    samples = [{"id": r[0], "script": r[1], "status": r[2], "notes": r[5],
                "acknowledged": r[6] is not None} for r in bad[:5]]
    return {
        "name": "run_history",
        "red": red,
        "summary": f"{len(bad)} unrecovered abandoned/error run(s) in last {ABANDONED_DAYS}d",
        "bad_run_count": len(bad),
        "samples": samples,
        "suppressed": suppressed,
        "window_days": ABANDONED_DAYS,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
source venv/bin/activate
python -m pytest tests/test_audit_pipeline.py -v
```
Expected: all 6 tests PASS.

- [ ] **Step 5: Run the full existing test suite to check for regressions**

```bash
source venv/bin/activate
python -m pytest tests/ -q
```
Expected: same pass/fail counts as the pre-existing baseline (9 passed, 7 errors in
`tests/test_pipeline_report.py` — a pre-existing, unrelated schema-drift issue filed to
`docs/backlog.md`; `tests/test_db.py` and `tests/test_price_flags.py` unaffected). Your new
`tests/test_audit_pipeline.py::*` (6 tests) should all show as passed, on top of that
baseline. If the pre-existing baseline numbers differ from this, stop and investigate before
proceeding — that means this change broke something outside its intended scope.

- [ ] **Step 6: Sanity-check against the real database (read-only, no writes)**

```bash
source venv/bin/activate
python3 -c "
from db import init_db
from audit_pipeline import check_run_history
conn = init_db('data/prices.db')
result = check_run_history(conn)
print('red:', result['red'])
print('bad_run_count:', result['bad_run_count'])
print('suppressed:', result['suppressed'])
conn.close()
"
```
Expected: `#1162` (or whatever its current acknowledged/aged-out state is by the time this
runs) appears in `suppressed` with `recovered_by: 1166` if still within the 7-day window,
or the check is simply green if it has since aged out or been manually acknowledged. Either
outcome is fine — this step is a smoke test, not an assertion.

- [ ] **Step 7: Commit**

```bash
git add audit_pipeline.py tests/test_audit_pipeline.py
git commit -m "$(cat <<'EOF'
feat(audit): auto-clear run_history for near-immediate self-recovery

Extends check_run_history's existing session-based auto-suppress (exact
started_at match, which only covers fetch_prices's session-reuse
pattern) with a 60-minute time-window rule: a completed run of the same
script starting within 60 min of a bad run's finished_at also counts as
recovered. Covers fetch_gas_prices, which gets a fresh started_at per
cron tick and so never matched the exact-match rule even when a hung
run was reaped and immediately replaced by a successful one.

Query-only — no acknowledged_at writes, so ack_run.py's human-review
trail stays unambiguous. Adds a suppressed list to the check's return
for visibility into what got auto-excluded and why.

EOF
)"
```

---

## Self-Review Notes (for whoever executes this plan)

- **Spec coverage:** the 60-min flat window, query-only exclusion (no `acknowledged_at`
  write), and `suppressed` observability list from
  `docs/superpowers/specs/2026-08-10-run-history-auto-clear-design.md` are all covered by
  Task 1. The spec's "Out of Scope" item (`abandon_stale_runs()` not recording *why* a run
  hung) is intentionally NOT part of this plan — already filed to `docs/backlog.md`.
- **No placeholders:** every step above has literal code, not a description of code.
- **Type consistency:** `check_run_history(conn) -> dict` signature and the `suppressed`
  item shape (`{"id", "script", "status", "recovered_by"}`) are used identically in the test
  file and the implementation — verified by re-reading both blocks side by side.
