"""Freshness detector must distinguish the failure modes that the old check missed.

Each case here is drawn from a state actually observed in data/prices.db on 2026-09-03.
"""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from generate_pipeline_report import (  # noqa: E402
    FROZEN_DATE_DAYS,
    STALE_DAYS_DAILY,
    STALE_DAYS_WEEKLY,
    freshness_breakdown,
    load_store_freshness,
)

AS_OF = "2026-09-03"


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE retail_networks (id TEXT PRIMARY KEY, name TEXT);
        CREATE TABLE stores (id INTEGER PRIMARY KEY, name TEXT, network_id TEXT,
                             is_active INTEGER DEFAULT 1, fetch_tier TEXT DEFAULT 'daily');
        CREATE TABLE prices_current (product_id INTEGER, store_id INTEGER, price REAL,
                                     price_date TEXT, last_checked_at TEXT,
                                     last_changed_at TEXT);
    """)
    c.execute("INSERT INTO retail_networks VALUES ('N1','TESTNET')")
    return c


def add_store(conn, sid, tier, price_date, last_checked, last_changed, active=1):
    conn.execute("INSERT INTO stores VALUES (?,?,?,?,?)",
                 (sid, f"store{sid}", "N1", active, tier))
    conn.execute("INSERT INTO prices_current VALUES (?,?,?,?,?,?)",
                 (1, sid, 9.99, price_date, last_checked, last_changed))


def status_of(conn, sid):
    rows = load_store_freshness(conn, as_of_date=AS_OF)
    return next(r for r in rows if r["store_id"] == sid)


def test_healthy_daily_store_is_ok(conn):
    add_store(conn, 1, "daily", "2026-09-02", "2026-09-03", "2026-09-03")
    assert status_of(conn, 1)["status"] == "ok"
    assert status_of(conn, 1)["stale"] is False


def test_polled_but_starved_is_not_ok(conn):
    """The exact bug: polled today, no new data for 70 days.

    The old check read last_checked_at and called this fresh.
    """
    add_store(conn, 2, "weekly", "2026-06-25", "2026-09-03", "2026-06-25")
    r = status_of(conn, 2)
    assert r["status"] == "starved"
    assert r["days_unpolled"] == 0
    assert r["days_stale"] == 70


def test_frozen_price_dates_detected_despite_fresh_writes(conn):
    """LIDL mode: writes land daily, but every price carries a 49-day-old Pricedate."""
    add_store(conn, 3, "daily", "2026-07-16", "2026-09-03", "2026-09-03")
    r = status_of(conn, 3)
    assert r["status"] == "frozen_dates"
    assert r["days_stale"] == 0          # invisible to a last_changed_at-only check
    assert r["days_price_date"] == 49


def test_unpolled_takes_precedence_over_starved(conn):
    add_store(conn, 4, "daily", "2026-06-01", "2026-06-01", "2026-06-01")
    assert status_of(conn, 4)["status"] == "unpolled"


def test_weekly_tier_gets_a_longer_budget(conn):
    """8 days idle is fine on the weekly tier and stale on the daily tier."""
    add_store(conn, 5, "weekly", "2026-08-26", "2026-09-03", "2026-08-26")
    add_store(conn, 6, "daily", "2026-08-26", "2026-09-03", "2026-08-26")
    assert 8 > STALE_DAYS_DAILY and 8 < STALE_DAYS_WEEKLY
    assert status_of(conn, 5)["status"] == "ok"
    assert status_of(conn, 6)["status"] == "starved"


def test_inactive_stores_excluded(conn):
    add_store(conn, 7, "daily", "2026-01-01", "2026-01-01", "2026-01-01", active=0)
    assert load_store_freshness(conn, as_of_date=AS_OF) == []


def test_missing_timestamps_are_stale_not_crash(conn):
    add_store(conn, 8, "daily", None, None, None)
    r = status_of(conn, 8)
    assert r["status"] == "unpolled"
    assert r["days_stale"] == 9999


def test_frozen_threshold_boundary(conn):
    add_store(conn, 9, "daily", "2026-08-21", "2026-09-03", "2026-09-03")   # 13d
    add_store(conn, 10, "daily", "2026-08-19", "2026-09-03", "2026-09-03")  # 15d
    assert FROZEN_DATE_DAYS == 14
    assert status_of(conn, 9)["status"] == "ok"
    assert status_of(conn, 10)["status"] == "frozen_dates"


def test_breakdown_counts_by_status_and_network(conn):
    add_store(conn, 11, "daily", "2026-09-02", "2026-09-03", "2026-09-03")  # ok
    add_store(conn, 12, "daily", "2026-07-16", "2026-09-03", "2026-09-03")  # frozen
    add_store(conn, 13, "daily", "2026-06-01", "2026-09-03", "2026-06-01")  # starved
    counts, worst = freshness_breakdown(load_store_freshness(conn, as_of_date=AS_OF))
    assert counts == {"ok": 1, "frozen_dates": 1, "starved": 1}
    assert worst[0][0] == "TESTNET"
    assert worst[0][1]["total"] == 2
