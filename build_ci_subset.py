"""
Build store and product ID subsets for the GitHub Actions CI run.

Writes:
  data/ci_stores.txt   — one store_id per line
  data/ci_products.txt — one product_id per line

Store selection (union, deduplicated):
  1. Top --top-per-net stores per retail network by surrounding_population
  2. Middle-population batch (percentile 35–65 by pop), geographically spread
     using Z-order (snake grid ~50 km cells) — up to --mid-batch stores

Product selection (union, deduplicated):
  1. Top --top-overall products by blended rank (store coverage + record count)
  2. Top --top-per-cat products per category by the same blended rank

Requires at least one price fetch to have been run. If the prices table is
empty, only the reference-based store list is written and products are skipped
with a warning.

Usage:
  python build_ci_subset.py [db_path]
    --top-per-net N   top N stores per network  (default 10)
    --mid-batch N     middle-pop geographic batch size (default 50)
    --top-overall N   top N products overall     (default 50)
    --top-per-cat N   top N products per category (default 20)
    --debug           print selection details
"""

import argparse
import os

from db import init_db

# Romania bounding box (matches fetch_prices.py)
_RO_LAT_MIN = 43.6
_RO_LON_MIN = 20.3
_GRID_DEG   = 0.45   # ~50 km


def _z_order(lat, lon):
    row = int((lat - _RO_LAT_MIN) / _GRID_DEG)
    col = int((lon - _RO_LON_MIN) / _GRID_DEG)
    col_key = col if row % 2 == 0 else 1000 - col
    return (row, col_key)


def select_stores(conn, top_per_net, mid_batch, debug):
    """Return (selected_ids, summary_str)."""

    # --- Tier 1: top N per network by surrounding_population ---
    tier1 = conn.execute("""
        WITH ranked AS (
            SELECT s.id,
                   s.name,
                   n.name AS network,
                   s.surrounding_population,
                   ROW_NUMBER() OVER (
                       PARTITION BY s.network_id
                       ORDER BY s.surrounding_population DESC
                   ) AS rn
            FROM stores s
            JOIN retail_networks n ON s.network_id = n.id
            WHERE s.lat IS NOT NULL
              AND s.surrounding_population IS NOT NULL
        )
        SELECT id, name, network, surrounding_population
        FROM ranked
        WHERE rn <= ?
        ORDER BY network, surrounding_population DESC
    """, (top_per_net,)).fetchall()

    tier1_ids = {r[0] for r in tier1}

    if debug:
        networks = {}
        for sid, name, net, pop in tier1:
            networks.setdefault(net, []).append(name)
        for net, names in sorted(networks.items()):
            print(f"  [{net}] {len(names)} stores: {', '.join(names[:3])}{'…' if len(names)>3 else ''}")

    # --- Tier 2: middle-population batch, geographically spread ---
    all_stores = conn.execute("""
        SELECT id, name, surrounding_population, lat, lon
        FROM stores
        WHERE lat IS NOT NULL
          AND surrounding_population IS NOT NULL
        ORDER BY surrounding_population
    """).fetchall()

    total = len(all_stores)
    lo = int(total * 0.35)
    hi = int(total * 0.65)
    middle = all_stores[lo:hi]

    # Sort by Z-order for geographic spread, then pick up to mid_batch
    middle_sorted = sorted(middle, key=lambda r: _z_order(r[3], r[4]))
    tier2 = middle_sorted[:mid_batch]
    tier2_ids = {r[0] for r in tier2}

    combined = tier1_ids | tier2_ids
    deduped = len(tier1_ids) + len(tier2_ids) - len(combined)

    summary = (
        f"Stores: {len(combined)} total  "
        f"({len(tier1_ids)} top-per-network + {len(tier2_ids)} middle-geo"
        + (f", {deduped} overlap" if deduped else "")
        + ")"
    )
    return sorted(combined), summary


def select_products(conn, top_overall, top_per_cat, debug):
    """Return (selected_ids, summary_str). Returns ([], warning) if no price data."""

    price_count = conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
    if price_count == 0:
        return [], "Products: skipped — prices table is empty (run fetch_prices.py first)"

    # Blended rank: average of store-coverage rank and record-count rank
    overall = conn.execute("""
        WITH coverage AS (
            SELECT product_id,
                   COUNT(DISTINCT store_id) AS store_count,
                   COUNT(*)                 AS record_count
            FROM prices
            GROUP BY product_id
        ),
        ranked AS (
            SELECT product_id, store_count, record_count,
                   RANK() OVER (ORDER BY store_count  DESC) AS cov_rank,
                   RANK() OVER (ORDER BY record_count DESC) AS rec_rank
            FROM coverage
        )
        SELECT product_id,
               (cov_rank + rec_rank) / 2.0 AS blended_rank
        FROM ranked
        ORDER BY blended_rank
        LIMIT ?
    """, (top_overall,)).fetchall()

    overall_ids = {r[0] for r in overall}

    per_cat = conn.execute("""
        WITH coverage AS (
            SELECT p.product_id,
                   pr.categ_id,
                   COUNT(DISTINCT p.store_id) AS store_count,
                   COUNT(*)                   AS record_count
            FROM prices p
            JOIN products pr ON p.product_id = pr.id
            GROUP BY p.product_id, pr.categ_id
        ),
        ranked AS (
            SELECT *,
                   RANK() OVER (
                       PARTITION BY categ_id
                       ORDER BY store_count DESC, record_count DESC
                   ) AS rn
            FROM coverage
        )
        SELECT product_id FROM ranked WHERE rn <= ?
    """, (top_per_cat,)).fetchall()

    per_cat_ids = {r[0] for r in per_cat}

    combined = overall_ids | per_cat_ids
    deduped = len(overall_ids) + len(per_cat_ids) - len(combined)

    if debug:
        print(f"  Products overall: {len(overall_ids)}  per-category: {len(per_cat_ids)}  overlap: {deduped}")

    summary = (
        f"Products: {len(combined)} total  "
        f"({len(overall_ids)} top-overall + {len(per_cat_ids)} per-category"
        + (f", {deduped} overlap" if deduped else "")
        + ")"
    )
    return sorted(combined), summary


def write_ids(path, ids):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(str(i) for i in ids) + "\n")


def main(db_path="data/prices.db", top_per_net=10, mid_batch=50,
         top_overall=50, top_per_cat=20, debug=False):

    conn = init_db(db_path)

    if debug:
        print("=== Store selection ===")
    store_ids, store_summary = select_stores(conn, top_per_net, mid_batch, debug)

    if debug:
        print("\n=== Product selection ===")
    product_ids, product_summary = select_products(conn, top_overall, top_per_cat, debug)

    conn.close()

    stores_path   = "data/ci_stores.txt"
    products_path = "data/ci_products.txt"

    write_ids(stores_path, store_ids)
    print(store_summary)
    print(f"  → {stores_path}")

    if product_ids:
        write_ids(products_path, product_ids)
        print(product_summary)
        print(f"  → {products_path}")
    else:
        print(product_summary)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("db", nargs="?", default="data/prices.db")
    parser.add_argument("--top-per-net", type=int, default=10,
                        help="top N stores per network by surrounding_population (default 10)")
    parser.add_argument("--mid-batch", type=int, default=50,
                        help="middle-population geographic batch size (default 50)")
    parser.add_argument("--top-overall", type=int, default=50,
                        help="top N products overall by blended rank (default 50)")
    parser.add_argument("--top-per-cat", type=int, default=20,
                        help="top N products per category by blended rank (default 20)")
    parser.add_argument("--debug", action="store_true",
                        help="print detailed selection breakdown")
    args = parser.parse_args()
    main(args.db, args.top_per_net, args.mid_batch,
         args.top_overall, args.top_per_cat, args.debug)
