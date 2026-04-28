#!/usr/bin/env python3
"""
Backfill prices_current table from existing prices table.
One-time migration: takes the most recent price per (product_id, store_id) and populates prices_current.
Safe to run multiple times (UPSERT handles duplicates).
"""

import sqlite3
import sys
from datetime import datetime, timezone

DB_PATH = "data/prices.db"

def backfill_prices_current(db_path=DB_PATH):
    """
    Backfill prices_current with the latest price per (product_id, store_id).
    Takes the row with the most recent price_date for each combination.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    print(f"[{datetime.now(timezone.utc).isoformat()}] Starting backfill...")
    print(f"  Database: {db_path}")

    # Count current rows before backfill
    prices_count = cur.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
    prices_current_count = cur.execute("SELECT COUNT(*) FROM prices_current").fetchone()[0]
    print(f"  Before: {prices_count:,} rows in prices, {prices_current_count:,} rows in prices_current")

    # Backfill: get the latest price per (product_id, store_id)
    # Use ROW_NUMBER() to pick the most recent price_date for each combination
    backfill_sql = """
    INSERT INTO prices_current
    (product_id, store_id, price, price_date, promo, brand, unit,
     retail_categ_id, retail_categ_name, first_seen_at, last_checked_at)
    WITH latest AS (
      SELECT *,
             ROW_NUMBER() OVER (PARTITION BY product_id, store_id
                               ORDER BY price_date DESC, fetched_at DESC, id DESC) as rn
      FROM prices
    )
    SELECT product_id, store_id, price, price_date, promo, brand, unit,
           retail_categ_id, retail_categ_name, fetched_at, last_checked_at
    FROM latest
    WHERE rn = 1
    ON CONFLICT(product_id, store_id) DO UPDATE SET
      price=excluded.price,
      price_date=excluded.price_date,
      promo=excluded.promo,
      brand=excluded.brand,
      unit=excluded.unit,
      retail_categ_id=excluded.retail_categ_id,
      retail_categ_name=excluded.retail_categ_name,
      last_checked_at=excluded.last_checked_at
    """

    print(f"  Inserting latest prices into prices_current...")
    cur.execute(backfill_sql)
    conn.commit()

    # Verify
    prices_current_new = cur.execute("SELECT COUNT(*) FROM prices_current").fetchone()[0]
    print(f"  After backfill: {prices_current_new:,} rows in prices_current")
    print(f"  (represents {prices_current_new:,} unique product-store combinations)")

    # Optional: report how many price changes per product
    multi_price = cur.execute("""
        SELECT COUNT(*) FROM (
            SELECT product_id, store_id, COUNT(DISTINCT price) as price_variants
            FROM prices
            GROUP BY product_id, store_id
            HAVING price_variants > 1
        )
    """).fetchone()[0]
    print(f"  {multi_price:,} product-store combos had price changes (will be kept in prices table)")

    conn.close()
    print(f"[{datetime.now(timezone.utc).isoformat()}] Backfill complete!")


if __name__ == "__main__":
    try:
        backfill_prices_current()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
