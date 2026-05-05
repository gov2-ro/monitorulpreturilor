import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db import init_db, upsert_price_flag
import json


def test_price_flags_table_created():
    conn = init_db(":memory:")
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='price_flags'"
    ).fetchone()
    assert exists is not None


def test_upsert_price_flag_inserts():
    conn = init_db(":memory:")
    upsert_price_flag(conn, 1, 1, "2026-04-30", "outlier_price",
                      {"price": 999.0, "mean": 10.0, "z_score": 8.5})
    conn.commit()
    row = conn.execute("SELECT flag_type, details FROM price_flags").fetchone()
    assert row[0] == "outlier_price"
    d = json.loads(row[1])
    assert d["z_score"] == 8.5


def test_upsert_price_flag_idempotent():
    conn = init_db(":memory:")
    upsert_price_flag(conn, 1, 1, "2026-04-30", "outlier_price", {"price": 999.0})
    upsert_price_flag(conn, 1, 1, "2026-04-30", "outlier_price", {"price": 999.0})
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM price_flags").fetchone()[0]
    assert count == 1


def test_upsert_price_flag_none_details():
    conn = init_db(":memory:")
    upsert_price_flag(conn, 1, 1, "2026-04-30", "stale_store")
    conn.commit()
    row = conn.execute("SELECT details FROM price_flags").fetchone()
    assert row[0] is None
