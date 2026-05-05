"""
Export key analytical views to CSV files in docs/data/.

Run after fetch_prices.py (and optionally generate_site.py) to produce
static CSV snapshots alongside the site.

Usage:
  python export_analytics.py [db_path] [--out docs/data]
"""

import argparse
import csv
import os
import sys

from db import init_db

EXPORTS = [
    {
        "file": "price_variability.csv",
        "sql": "SELECT * FROM v_price_variability",
        "desc": "Intra-network price spread per product (outlier-filtered, latest date)",
    },
    {
        "file": "cross_network_spread.csv",
        "sql": "SELECT * FROM v_cross_network_spread",
        "desc": "Cross-network price ratio per product (excl. SELGROS)",
    },
    {
        "file": "popular_products.csv",
        "sql": "SELECT * FROM v_product_popularity LIMIT 200",
        "desc": "Top 200 products by blended store-coverage + record-count rank",
    },
    {
        "file": "private_labels.csv",
        "sql": "SELECT * FROM v_private_label_candidates LIMIT 100",
        "desc": "Products appearing in only one network (private-label candidates)",
    },
    {
        "file": "stores_per_network.csv",
        "sql": "SELECT * FROM v_stores_per_network",
        "desc": "Store count per retail network",
    },
    {
        "file": "price_freshness.csv",
        "sql": "SELECT * FROM v_price_freshness LIMIT 30",
        "desc": "Price record counts per fetch date (last 30 dates)",
    },
    {
        "file": "products_no_prices.csv",
        "sql": "SELECT * FROM v_products_no_prices",
        "desc": "Products with no price records at all",
    },
    {
        "file": "run_history.csv",
        "sql": """
            SELECT script, started_at, finished_at, status,
                   uats_processed AS items, records_written, notes
            FROM runs ORDER BY started_at DESC LIMIT 30
        """,
        "desc": "Last 30 pipeline run records",
    },
    {
        "file": "price_flags_summary.csv",
        "sql": """
            SELECT flag_type, COUNT(*) AS total,
                   COUNT(DISTINCT product_id) AS products,
                   COUNT(DISTINCT store_id) AS stores
            FROM price_flags
            GROUP BY flag_type
        """,
        "desc": "Summary count of price flags by type",
    },
]


def export_all(db_path, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    conn = init_db(db_path)

    total_rows = 0
    for exp in EXPORTS:
        path = os.path.join(out_dir, exp["file"])
        try:
            cur = conn.execute(exp["sql"])
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(cols)
                writer.writerows(rows)
            print(f"  {exp['file']}: {len(rows)} rows")
            total_rows += len(rows)
        except Exception as exc:
            print(f"  {exp['file']}: ERROR — {exc}", file=sys.stderr)

    conn.close()
    print(f"\nDone. {len(EXPORTS)} files, {total_rows} total rows → {out_dir}/")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("db", nargs="?", default="data/prices.db")
    parser.add_argument("--out", default="docs/data",
                        help="output directory (default: docs/data)")
    args = parser.parse_args()
    export_all(args.db, args.out)
