import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db import init_db, insert_price, propagate_network_prices

SRC, TGT = 1, 2


def _price(conn, store, price, date, promo=None, at="2026-11-20T00:00:00"):
    insert_price(conn, 100, store, price, date, promo, "b", "kg", "1", "c", at)


def _history(conn, store):
    return conn.execute(
        "SELECT price, price_date FROM prices WHERE store_id=? ORDER BY id", (store,)
    ).fetchall()


def test_first_propagation_writes_history():
    conn = init_db(":memory:")
    _price(conn, SRC, 5.0, "2026-04-17 04:00")
    propagate_network_prices(conn, SRC, [TGT], "2026-11-20T01:00:00")
    assert _history(conn, TGT) == [(5.0, "2026-04-17 04:00")]


def test_unchanged_price_is_not_reinserted_after_retention_delete():
    conn = init_db(":memory:")
    _price(conn, SRC, 5.0, "2026-04-17 04:00")
    propagate_network_prices(conn, SRC, [TGT], "2026-11-20T01:00:00")
    conn.execute("DELETE FROM prices WHERE price_date < '2026-10-06'")   # retention
    propagate_network_prices(conn, SRC, [TGT], "2026-11-21T01:00:00")
    assert _history(conn, TGT) == []
    cur = conn.execute("SELECT price, last_checked_at FROM prices_current WHERE store_id=?",
                       (TGT,)).fetchone()
    assert cur == (5.0, "2026-11-21T01:00:00")


def test_changed_price_or_promo_writes_history():
    conn = init_db(":memory:")
    _price(conn, SRC, 5.0, "2026-11-01 04:00")
    propagate_network_prices(conn, SRC, [TGT], "2026-11-20T01:00:00")
    _price(conn, SRC, 6.0, "2026-11-21 04:00")
    propagate_network_prices(conn, SRC, [TGT], "2026-11-21T01:00:00")
    _price(conn, SRC, 6.0, "2026-11-22 04:00", promo="1")
    propagate_network_prices(conn, SRC, [TGT], "2026-11-22T01:00:00")
    assert [r[0] for r in _history(conn, TGT)] == [5.0, 6.0, 6.0]
