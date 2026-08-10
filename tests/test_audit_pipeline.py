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
