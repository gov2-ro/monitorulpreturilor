#!/usr/bin/env python3
"""Persist price quality flags to the price_flags table.

Run after each daily fetch to populate outlier_price, price_spike,
and promo_too_deep flags. Safe to re-run — uses INSERT OR IGNORE.

Usage:
    python build_price_flags.py
    python build_price_flags.py --db data/prices.db
"""

import argparse
import json
import math
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "prices.db"

Z_THRESHOLD = 3.0
SPIKE_PCT = 0.50       # > 50% change
PROMO_DEPTH = 0.20     # promo < 20% of regular avg


def flag_outlier_prices(conn, z_threshold=Z_THRESHOLD):
    """Insert outlier_price flags using median + MAD (robust to masking effect).

    Standard z-score fails when a single outlier skews mean/stdev so the
    outlier's own z barely exceeds the threshold. Modified z-score uses
    median and MAD, making it resilient to extreme single-value outliers.

    Returns count of new flags inserted.
    """
    products = conn.execute(
        "SELECT DISTINCT product_id FROM prices_current WHERE price > 0"
    ).fetchall()

    inserted = 0

    for (pid,) in products:
        rows = conn.execute(
            "SELECT store_id, price, price_date FROM prices_current WHERE product_id=? AND price>0",
            (pid,)
        ).fetchall()

        if len(rows) < 3:
            continue

        prices = [r[1] for r in rows]
        median = sorted(prices)[len(prices) // 2]
        mad = sorted([abs(p - median) for p in prices])[len(prices) // 2]

        if mad < 1e-9:
            continue  # all prices identical — no outliers possible

        for store_id, price, price_date in rows:
            # Modified z-score: 0.6745 * |x - median| / MAD
            mz = 0.6745 * abs(price - median) / mad
            if mz > z_threshold:
                details = json.dumps({"price": price, "median": round(median, 4),
                                      "mad": round(mad, 4), "z_score": round(mz, 2)})
                cur = conn.execute(
                    """INSERT OR IGNORE INTO price_flags
                       (product_id, store_id, price_date, flag_type, details)
                       VALUES (?,?,?,'outlier_price',?)""",
                    (pid, store_id, price_date, details)
                )
                inserted += cur.rowcount

    print(f"  outlier_price: {inserted} flags inserted")
    return inserted


def flag_price_spikes(conn, spike_pct=SPIKE_PCT):
    """Insert price_spike flags for > spike_pct change vs previous price.

    Compares the two most recent distinct price_dates in the prices table.
    Returns count of new flags inserted.
    """
    dates = conn.execute(
        "SELECT DISTINCT price_date FROM prices WHERE price_date IS NOT NULL ORDER BY price_date DESC LIMIT 2"
    ).fetchall()
    if len(dates) < 2:
        print("  price_spike: insufficient history (need 2+ dates)")
        return 0

    curr_date, prev_date = dates[0][0], dates[1][0]

    spikes = conn.execute("""
        SELECT a.product_id, a.store_id, a.price AS curr_price, b.price AS prev_price
        FROM prices a
        JOIN prices b ON a.product_id=b.product_id AND a.store_id=b.store_id
        WHERE a.price_date=? AND b.price_date=?
          AND b.price > 0
          AND ABS(a.price - b.price) / b.price > ?
    """, (curr_date, prev_date, spike_pct)).fetchall()

    inserted = 0
    for pid, sid, curr, prev in spikes:
        pct = round((curr - prev) / prev * 100, 1)
        details = json.dumps({"curr_price": curr, "prev_price": prev,
                               "pct_change": pct, "curr_date": curr_date,
                               "prev_date": prev_date})
        cur = conn.execute(
            """INSERT OR IGNORE INTO price_flags
               (product_id, store_id, price_date, flag_type, details)
               VALUES (?,?,?,'price_spike',?)""",
            (pid, sid, curr_date, details)
        )
        inserted += cur.rowcount

    print(f"  price_spike: {inserted} flags inserted ({curr_date} vs {prev_date})")
    return inserted


def flag_promo_too_deep(conn, depth=PROMO_DEPTH):
    """Insert promo_too_deep flags for promo prices < depth × product regular avg.

    Returns count of new flags inserted.
    """
    rows = conn.execute("""
        WITH reg AS (
          SELECT product_id, AVG(price) AS reg_avg
          FROM prices_current
          WHERE promo IS NULL AND price > 0
          GROUP BY product_id
        )
        SELECT pc.product_id, pc.store_id, pc.price, pc.price_date, r.reg_avg
        FROM prices_current pc
        JOIN reg r ON pc.product_id = r.product_id
        WHERE pc.promo IS NOT NULL
          AND pc.price > 0
          AND r.reg_avg > 0
          AND pc.price < r.reg_avg * ?
    """, (depth,)).fetchall()

    inserted = 0
    for pid, sid, price, price_date, reg_avg in rows:
        pct = round(price / reg_avg * 100, 1)
        details = json.dumps({"promo_price": price, "regular_avg": round(reg_avg, 4),
                               "pct_of_regular": pct})
        cur = conn.execute(
            """INSERT OR IGNORE INTO price_flags
               (product_id, store_id, price_date, flag_type, details)
               VALUES (?,?,?,'promo_too_deep',?)""",
            (pid, sid, price_date, details)
        )
        inserted += cur.rowcount

    print(f"  promo_too_deep: {inserted} flags inserted")
    return inserted


def build(db_path=DEFAULT_DB):
    from db import init_db
    print(f"Building price flags from {db_path}")
    conn = init_db(str(db_path))  # ensures price_flags table exists (idempotent)
    total = 0
    total += flag_outlier_prices(conn)
    total += flag_price_spikes(conn)
    total += flag_promo_too_deep(conn)
    conn.commit()
    existing = conn.execute("SELECT COUNT(*) FROM price_flags").fetchone()[0]
    conn.close()
    print(f"  Total new flags: {total} | Total in DB: {existing}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()
    build(args.db)
