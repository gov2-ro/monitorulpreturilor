#!/usr/bin/env python3
"""
Analyze price uniformity: for each (product, network, date), count distinct prices.
Reports: % of groups with uniform pricing, price variance distribution, top variance products.
"""

import sqlite3
import csv
from collections import defaultdict
from pathlib import Path

DB_PATH = Path("data/prices.db")

def analyze_price_uniformity(db_path=DB_PATH):
    """
    Analyze price variance within each (product_id, network_id, price_date) group.
    A group is "uniform" if all stores for that product on that date in that network
    charge the same price.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Query: per (product, network, date), get price distribution
    query = """
    SELECT
        p.product_id,
        s.network_id,
        p.price_date,
        COUNT(DISTINCT p.store_id)  AS store_count,
        COUNT(DISTINCT p.price)     AS distinct_price_count,
        MIN(p.price)                AS min_price,
        MAX(p.price)                AS max_price
    FROM prices p
    JOIN stores s ON p.store_id = s.id
    GROUP BY p.product_id, s.network_id, p.price_date
    HAVING store_count >= 3
    ORDER BY s.network_id, p.product_id, p.price_date
    """

    print("Querying price distributions (this may take 1-2 min)...")
    rows = list(cur.execute(query))
    total_groups = len(rows)

    if not total_groups:
        print("No groups found with 3+ stores. Check DB.")
        conn.close()
        return

    # Aggregate statistics
    uniform_groups = sum(1 for r in rows if r["distinct_price_count"] == 1)
    variance_groups = total_groups - uniform_groups

    spreads = []
    for r in rows:
        if r["min_price"] > 0:
            spread = (r["max_price"] - r["min_price"]) / r["min_price"] * 100
            spreads.append((spread, r["product_id"], r["network_id"], r["price_date"]))

    spreads.sort(reverse=True)

    # Summary stats
    print("\n" + "="*70)
    print(f"PRICE UNIFORMITY ANALYSIS")
    print("="*70)
    print(f"\nTotal groups (product × network × date, ≥3 stores): {total_groups:,}")
    print(f"Uniform pricing (all stores same price):              {uniform_groups:,} ({100*uniform_groups/total_groups:.1f}%)")
    print(f"Varying pricing (stores differ):                      {variance_groups:,} ({100*variance_groups/total_groups:.1f}%)")

    if spreads:
        p95_spread = spreads[int(0.05 * len(spreads))][0]
        p50_spread = spreads[int(0.50 * len(spreads))][0]
        max_spread = spreads[0][0]
        print(f"\nPrice spread (max%-min%) distribution among varying groups:")
        print(f"  Median:   {p50_spread:.2f}%")
        print(f"  p95:      {p95_spread:.2f}%")
        print(f"  Max:      {max_spread:.2f}%")

    # Per-network summary
    network_stats = defaultdict(lambda: {"total": 0, "uniform": 0})
    for r in rows:
        nid = r["network_id"]
        network_stats[nid]["total"] += 1
        if r["distinct_price_count"] == 1:
            network_stats[nid]["uniform"] += 1

    print(f"\nUniformity by network:")
    print(f"  Network         | Uniform % | Groups")
    print(f"  " + "-" * 50)
    for nid in sorted(network_stats.keys()):
        stats = network_stats[nid]
        uniform_pct = 100 * stats["uniform"] / stats["total"]
        print(f"  {str(nid):15s} | {uniform_pct:8.1f}% | {stats['total']:,}")

    # Top 20 products by variance
    print(f"\nTop 20 most variable products (highest max spread):")
    print(f"  Product ID | Network | Spread %")
    print(f"  " + "-" * 40)
    seen = set()
    for i, (spread, prod_id, net_id, date) in enumerate(spreads[:100]):
        key = (prod_id, net_id)
        if key not in seen and i < 20:
            print(f"  {prod_id:10d} | {str(net_id):7s} | {spread:6.2f}%")
            seen.add(key)

    # Export to CSV
    csv_path = Path("docs/price_uniformity.csv")
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "product_id", "network_id", "price_date", "store_count",
            "distinct_prices", "min_price", "max_price", "spread_pct"
        ])
        for r in rows:
            spread = (r["max_price"] - r["min_price"]) / r["min_price"] * 100 if r["min_price"] > 0 else 0
            writer.writerow([
                r["product_id"],
                r["network_id"],
                r["price_date"],
                r["store_count"],
                r["distinct_price_count"],
                f"{r['min_price']:.2f}",
                f"{r['max_price']:.2f}",
                f"{spread:.2f}"
            ])

    print(f"\nResults exported to {csv_path}")
    print("="*70)

    conn.close()


if __name__ == "__main__":
    analyze_price_uniformity()
