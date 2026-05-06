#!/usr/bin/env python3
"""
Analyse price variability to determine if full store coverage is necessary.

Computes:
1. Intra-network variance (same product, UAT, network) — price spread across stores in same network
2. Inter-network variance (same product, UAT) — price difference across networks
3. Network-wide variance (same product, network) — price spread across all stores in a network
"""

import sqlite3
from statistics import stdev, mean, median
from collections import defaultdict
import sys

def get_connection(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def analyse_intra_network_variance(conn):
    """
    For each (product, UAT, network), compute variance across stores.
    Shows: do prices vary between different Kaufland stores in the same city?
    """
    print("\n" + "="*80)
    print("INTRA-NETWORK VARIANCE (same product, UAT, network → different stores)")
    print("="*80)

    query = """
    SELECT
        p.product_id,
        s.uat_id,
        s.network_id,
        COUNT(DISTINCT p.store_id) as store_count,
        COUNT(*) as price_records,
        CAST(AVG(p.price) AS REAL) as avg_price,
        MIN(p.price) as min_price,
        MAX(p.price) as max_price,
        CAST(MAX(p.price) - MIN(p.price) AS REAL) as price_spread,
        CAST((MAX(p.price) - MIN(p.price)) / AVG(p.price) * 100 AS REAL) as spread_pct
    FROM prices p
    JOIN stores s ON p.store_id = s.id
    WHERE p.price > 0 AND s.network_id IS NOT NULL
    GROUP BY p.product_id, s.uat_id, s.network_id
    HAVING COUNT(DISTINCT p.store_id) >= 2  -- only groups with 2+ stores
    """

    cursor = conn.execute(query)
    results = cursor.fetchall()

    print(f"\nTotal (product, UAT, network) groups with 2+ stores: {len(results)}")

    if results:
        spreads = [r['spread_pct'] for r in results]
        print(f"\nPrice spread % statistics (across stores in same network/UAT):")
        print(f"  Median spread: {median(spreads):.2f}%")
        print(f"  Mean spread:   {mean(spreads):.2f}%")
        print(f"  Min spread:    {min(spreads):.2f}%")
        print(f"  Max spread:    {max(spreads):.2f}%")

        # Distribution
        zero_var = len([s for s in spreads if s < 0.1])
        low_var = len([s for s in spreads if 0.1 <= s < 1])
        mid_var = len([s for s in spreads if 1 <= s < 5])
        high_var = len([s for s in spreads if s >= 5])

        print(f"\nVariance distribution:")
        print(f"  0–0.1% (essentially no variance):        {zero_var:6d} ({zero_var/len(spreads)*100:5.1f}%)")
        print(f"  0.1–1% (minimal, noise-level):           {low_var:6d} ({low_var/len(spreads)*100:5.1f}%)")
        print(f"  1–5% (small but noticeable):             {mid_var:6d} ({mid_var/len(spreads)*100:5.1f}%)")
        print(f"  5%+ (substantial pricing differences):   {high_var:6d} ({high_var/len(spreads)*100:5.1f}%)")

        # Show some examples of high variance
        high_variance_examples = sorted(results, key=lambda x: x['spread_pct'], reverse=True)[:5]
        if high_var > 0:
            print(f"\nTop 5 high-variance examples (spread_pct):")
            for ex in high_variance_examples:
                print(f"  Product {ex['product_id']:5d}, UAT {ex['uat_id']:3d}, Network {ex['network_id']:3s}: "
                      f"spread {ex['spread_pct']:6.2f}% ({ex['min_price']:.2f}–{ex['max_price']:.2f}, "
                      f"{ex['store_count']} stores)")

def analyse_inter_network_variance(conn):
    """
    For each (product, UAT), compare prices across different networks.
    Shows: do different networks price the same product differently in the same city?
    """
    print("\n" + "="*80)
    print("INTER-NETWORK VARIANCE (same product, UAT → different networks)")
    print("="*80)

    query = """
    SELECT
        p.product_id,
        s.uat_id,
        COUNT(DISTINCT s.network_id) as network_count,
        COUNT(DISTINCT p.store_id) as store_count,
        CAST(AVG(p.price) AS REAL) as avg_price,
        MIN(p.price) as min_price,
        MAX(p.price) as max_price,
        CAST(MAX(p.price) - MIN(p.price) AS REAL) as price_spread,
        CAST((MAX(p.price) - MIN(p.price)) / AVG(p.price) * 100 AS REAL) as spread_pct
    FROM prices p
    JOIN stores s ON p.store_id = s.id
    WHERE p.price > 0 AND s.network_id IS NOT NULL
    GROUP BY p.product_id, s.uat_id
    HAVING COUNT(DISTINCT s.network_id) >= 2  -- only compare if 2+ networks have the product
    """

    cursor = conn.execute(query)
    results = cursor.fetchall()

    print(f"\nTotal (product, UAT) groups with 2+ networks: {len(results)}")

    if results:
        spreads = [r['spread_pct'] for r in results]
        print(f"\nPrice spread % statistics (across networks in same UAT):")
        print(f"  Median spread: {median(spreads):.2f}%")
        print(f"  Mean spread:   {mean(spreads):.2f}%")
        print(f"  Min spread:    {min(spreads):.2f}%")
        print(f"  Max spread:    {max(spreads):.2f}%")

        # Distribution
        zero_var = len([s for s in spreads if s < 1])
        low_var = len([s for s in spreads if 1 <= s < 5])
        mid_var = len([s for s in spreads if 5 <= s < 10])
        high_var = len([s for s in spreads if s >= 10])

        print(f"\nVariance distribution:")
        print(f"  0–1% (essentially no variance):           {zero_var:6d} ({zero_var/len(spreads)*100:5.1f}%)")
        print(f"  1–5% (small differences):                 {low_var:6d} ({low_var/len(spreads)*100:5.1f}%)")
        print(f"  5–10% (moderate differences):             {mid_var:6d} ({mid_var/len(spreads)*100:5.1f}%)")
        print(f"  10%+ (substantial network differentiation):{high_var:6d} ({high_var/len(spreads)*100:5.1f}%)")

        # Show examples of high inter-network variance
        high_variance_examples = sorted(results, key=lambda x: x['spread_pct'], reverse=True)[:5]
        if high_var > 0:
            print(f"\nTop 5 products with network price differences (spread_pct):")
            for ex in high_variance_examples:
                print(f"  Product {ex['product_id']:5d}, UAT {ex['uat_id']:3d}: "
                      f"spread {ex['spread_pct']:6.2f}% ({ex['min_price']:.2f}–{ex['max_price']:.2f}, "
                      f"{ex['network_count']} networks)")

def analyse_network_wide_variance(conn):
    """
    For each (product, network), compute price variance across all stores.
    Shows: is Kaufland's price for milk consistent across the entire country?
    """
    print("\n" + "="*80)
    print("NETWORK-WIDE VARIANCE (same product, network → all UATs)")
    print("="*80)

    query = """
    SELECT
        p.product_id,
        s.network_id,
        COUNT(DISTINCT s.uat_id) as uat_count,
        COUNT(DISTINCT p.store_id) as store_count,
        CAST(AVG(p.price) AS REAL) as avg_price,
        MIN(p.price) as min_price,
        MAX(p.price) as max_price,
        CAST(MAX(p.price) - MIN(p.price) AS REAL) as price_spread,
        CAST((MAX(p.price) - MIN(p.price)) / AVG(p.price) * 100 AS REAL) as spread_pct
    FROM prices p
    JOIN stores s ON p.store_id = s.id
    WHERE p.price > 0 AND s.network_id IS NOT NULL
    GROUP BY p.product_id, s.network_id
    HAVING COUNT(DISTINCT s.uat_id) >= 3  -- at least 3 UATs
    """

    cursor = conn.execute(query)
    results = cursor.fetchall()

    print(f"\nTotal (product, network) groups with 3+ UATs: {len(results)}")

    if results:
        spreads = [r['spread_pct'] for r in results]
        print(f"\nPrice spread % statistics (across all UATs in same network):")
        print(f"  Median spread: {median(spreads):.2f}%")
        print(f"  Mean spread:   {mean(spreads):.2f}%")
        print(f"  Min spread:    {min(spreads):.2f}%")
        print(f"  Max spread:    {max(spreads):.2f}%")

        # Distribution
        zero_var = len([s for s in spreads if s < 2])
        low_var = len([s for s in spreads if 2 <= s < 5])
        mid_var = len([s for s in spreads if 5 <= s < 10])
        high_var = len([s for s in spreads if s >= 10])

        print(f"\nVariance distribution:")
        print(f"  0–2% (national price parity):             {zero_var:6d} ({zero_var/len(spreads)*100:5.1f}%)")
        print(f"  2–5% (minor regional variance):           {low_var:6d} ({low_var/len(spreads)*100:5.1f}%)")
        print(f"  5–10% (moderate regional variance):       {mid_var:6d} ({mid_var/len(spreads)*100:5.1f}%)")
        print(f"  10%+ (substantial regional differences):  {high_var:6d} ({high_var/len(spreads)*100:5.1f}%)")

def analyse_store_coverage_impact(conn):
    """
    Estimate impact of scraping only 1 store per network per UAT.
    """
    print("\n" + "="*80)
    print("STORE COVERAGE IMPACT ANALYSIS")
    print("="*80)

    # Current coverage
    query_current = """
    SELECT COUNT(DISTINCT p.store_id) as stores_with_prices
    FROM prices p
    WHERE p.price > 0
    """
    current_stores = conn.execute(query_current).fetchone()[0]

    # Theoretical 1-per-network-per-UAT
    query_optimal = """
    SELECT COUNT(*) as count
    FROM (
        SELECT DISTINCT s.uat_id, s.network_id
        FROM stores s
        JOIN prices p ON s.id = p.store_id
        WHERE p.price > 0 AND s.network_id IS NOT NULL
    )
    """
    optimal_stores = conn.execute(query_optimal).fetchone()[0]

    # Total stores in DB
    query_total = "SELECT COUNT(*) FROM stores WHERE network_id IS NOT NULL"
    total_stores = conn.execute(query_total).fetchone()[0]

    print(f"\nCurrent store coverage: {current_stores:,} stores with prices")
    print(f"Total stores in DB:    {total_stores:,}")
    print(f"Ideal 1-per-network-per-UAT: ~{optimal_stores:,} stores")
    print(f"\nReduction potential: {((current_stores - optimal_stores) / current_stores * 100):.1f}%")
    print(f"  Current requests: ~{current_stores // 50:,} (assuming 50 stores per request)")
    print(f"  Optimized requests: ~{optimal_stores // 50:,} (assuming 1 store per network/UAT)")

def main():
    db_path = "/Users/pax/devbox/gov2/monitorulpreturilor/data/prices.db"
    conn = get_connection(db_path)

    try:
        analyse_intra_network_variance(conn)
        analyse_inter_network_variance(conn)
        analyse_network_wide_variance(conn)
        analyse_store_coverage_impact(conn)

        print("\n" + "="*80)
        print("SUMMARY & RECOMMENDATIONS")
        print("="*80)
        print("""
If intra-network variance is near-zero (<0.5%), then:
  → Different Kaufland stores in the same city price identically
  → We only need 1 store per network per UAT, not 50
  → Request volume could drop 95%+ (from ~24k to ~200/day)

If inter-network variance is large (>5%), then:
  → Different networks price differently → cross-network comparison is valuable
  → Continue with current approach

If network-wide variance is small (<2%), then:
  → Prices are nationally standardized
  → Can delegate price monitoring to a subset of national rep stores
""")

    finally:
        conn.close()

if __name__ == "__main__":
    main()
