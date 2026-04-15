"""
Analyse retail price variability across and within networks.

Two analyses:
  intra  — same product, same network, different stores: variance should be
           near-zero for chains with centrally-set prices; outliers flag
           data-quality issues or genuine regional pricing.
  cross  — same product, different networks: spread and ratio reveal which
           networks are cheapest/most expensive per product.

Unit normalization is applied before comparison so kg/Kg/K/1kg all collapse
to the same bucket.  SELGROS is flagged (B2B wholesale) and excluded from
cross-network rankings by default (--include-selgros to override).

Output: CSV files in data/
  price_intra_network.csv   — per (product, network): min/max/avg/cv/stores
  price_cross_network.csv   — per product: cheapest/most-expensive network,
                              spread, ratio, price per network

Usage:
  python analyse_prices.py [--min-stores 2] [--include-selgros] [--debug]
"""

import argparse
import csv
import math
import re
from collections import defaultdict
from pathlib import Path

from db import init_db

# ---------------------------------------------------------------------------
# Unit normalisation
# ---------------------------------------------------------------------------

_UNIT_MAP = {
    # weight
    "kg": "kg", "Kg": "kg", "KG": "kg", "K": "kg", "k": "kg",
    "1kg": "kg",
    # pieces / units
    "BUC": "buc", "BUCATA": "buc", "BUCATI": "buc",
    "Buc": "buc", "Buc.": "buc", "buc": "buc", "bucata": "buc",
    "BU": "buc", "PC": "buc", "CU": "buc", "BO": "buc",
    # litres
    "Litru": "l", "L": "l", "l": "l",
}

def normalise_unit(raw):
    """Return a canonical unit string, or the stripped raw value if unknown."""
    s = (raw or "").strip()
    return _UNIT_MAP.get(s, s.lower() if s else "")


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------

def _stats(prices):
    n = len(prices)
    if n == 0:
        return {}
    mn = min(prices)
    mx = max(prices)
    avg = sum(prices) / n
    variance = sum((p - avg) ** 2 for p in prices) / n
    std = math.sqrt(variance)
    cv = (std / avg * 100) if avg else 0          # coefficient of variation %
    median = sorted(prices)[n // 2]
    return {"n": n, "min": mn, "max": mx, "avg": avg,
            "std": std, "cv": cv, "median": median,
            "spread": mx - mn, "ratio": mx / mn if mn else None}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(db_path="data/prices.db", min_stores=2, include_selgros=False, debug=False):
    conn = init_db(db_path)

    print("Loading prices…")
    rows = conn.execute("""
        SELECT p.product_id, pr.name, p.store_id, s.network_id, n.name as network,
               p.price, p.unit, p.price_date
        FROM prices p
        JOIN stores   s  ON p.store_id   = s.id
        JOIN products pr ON p.product_id = pr.id
        LEFT JOIN retail_networks n ON s.network_id = n.id
        WHERE s.network_id IS NOT NULL
    """).fetchall()
    conn.close()

    print(f"  {len(rows):,} price rows loaded")

    # Build: (product_id, product_name, network, unit_norm) → {store_id: latest_price}
    # Use latest price per store (max price_date) to avoid counting historical duplicates
    latest: dict = {}   # key → {store_id: (price, price_date)}

    for prod_id, prod_name, store_id, net_id, network, price, unit, price_date in rows:
        if not include_selgros and (network or "").upper() == "SELGROS":
            continue
        unit_norm = normalise_unit(unit)
        key = (prod_id, prod_name, network, unit_norm)
        if key not in latest:
            latest[key] = {}
        existing = latest[key].get(store_id)
        if existing is None or price_date > existing[1]:
            latest[key][store_id] = (price, price_date)

    # -----------------------------------------------------------------------
    # Intra-network analysis
    # -----------------------------------------------------------------------
    print("Computing intra-network variability…")
    intra_rows = []
    for (prod_id, prod_name, network, unit_norm), store_prices in latest.items():
        prices = [v[0] for v in store_prices.values()]
        if len(prices) < min_stores:
            continue
        s = _stats(prices)
        intra_rows.append({
            "product_id": prod_id,
            "product": prod_name,
            "network": network,
            "unit": unit_norm,
            "stores": s["n"],
            "min": round(s["min"], 2),
            "max": round(s["max"], 2),
            "avg": round(s["avg"], 2),
            "std": round(s["std"], 3),
            "cv_pct": round(s["cv"], 2),
            "spread": round(s["spread"], 2),
            "ratio": round(s["ratio"], 3) if s["ratio"] else "",
        })

    intra_rows.sort(key=lambda r: -r["cv_pct"])

    intra_path = Path("data/price_intra_network.csv")
    _write_csv(intra_path, intra_rows, [
        "product_id", "product", "network", "unit", "stores",
        "min", "max", "avg", "std", "cv_pct", "spread", "ratio",
    ])
    print(f"  {len(intra_rows)} rows → {intra_path}")

    if debug:
        print("\n  Top 10 intra-network outliers (by CV%):")
        for r in intra_rows[:10]:
            print(f"    {r['network']:20s}  {r['product'][:40]:40s}  "
                  f"cv={r['cv_pct']:.1f}%  spread={r['spread']}  n={r['stores']}")

    # -----------------------------------------------------------------------
    # Cross-network analysis
    # -----------------------------------------------------------------------
    print("Computing cross-network variability…")

    # Aggregate to (product, unit) → {network: median_price}
    # Use median per network to reduce intra-network noise
    by_prod_unit: dict = defaultdict(lambda: defaultdict(list))
    for (prod_id, prod_name, network, unit_norm), store_prices in latest.items():
        prices = [v[0] for v in store_prices.values()]
        if not prices:
            continue
        median = sorted(prices)[len(prices) // 2]
        by_prod_unit[(prod_id, prod_name, unit_norm)][network].append(median)

    cross_rows = []
    for (prod_id, prod_name, unit_norm), net_prices in by_prod_unit.items():
        # Collapse multiple medians per network (shouldn't happen but be safe)
        net_median = {net: sorted(ps)[len(ps) // 2] for net, ps in net_prices.items()}
        if len(net_median) < 2:
            continue   # need at least 2 networks to compare

        prices_flat = list(net_median.values())
        s = _stats(prices_flat)
        cheapest = min(net_median, key=net_median.get)
        priciest = max(net_median, key=net_median.get)

        row = {
            "product_id": prod_id,
            "product": prod_name,
            "unit": unit_norm,
            "networks": len(net_median),
            "spread": round(s["spread"], 2),
            "ratio": round(s["ratio"], 3) if s["ratio"] else "",
            "cheapest_network": cheapest,
            "cheapest_price": round(net_median[cheapest], 2),
            "priciest_network": priciest,
            "priciest_price": round(net_median[priciest], 2),
        }
        # Add one column per network (sorted)
        for net in sorted(net_median):
            row[f"price_{net}"] = round(net_median[net], 2)
        cross_rows.append(row)

    cross_rows.sort(key=lambda r: -(r["ratio"] if r["ratio"] else 0))

    # Dynamic columns: fixed headers + per-network price columns
    fixed = ["product_id", "product", "unit", "networks", "spread", "ratio",
             "cheapest_network", "cheapest_price", "priciest_network", "priciest_price"]
    net_cols = sorted({k for r in cross_rows for k in r if k.startswith("price_")})
    cross_path = Path("data/price_cross_network.csv")
    _write_csv(cross_path, cross_rows, fixed + net_cols)
    print(f"  {len(cross_rows)} rows → {cross_path}")

    if debug:
        print("\n  Top 10 cross-network price gaps (by ratio):")
        for r in cross_rows[:10]:
            print(f"    {r['product'][:40]:40s}  "
                  f"{r['cheapest_network']}={r['cheapest_price']} → "
                  f"{r['priciest_network']}={r['priciest_price']}  "
                  f"ratio={r['ratio']}")

    print("\nDone.")


def _write_csv(path, rows, fieldnames):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("db", nargs="?", default="data/prices.db")
    parser.add_argument("--min-stores", type=int, default=2,
                        help="min stores per (product, network) for intra analysis (default: 2)")
    parser.add_argument("--include-selgros", action="store_true",
                        help="include SELGROS (B2B wholesale) in cross-network comparison")
    parser.add_argument("--debug", action="store_true",
                        help="print top outliers to stdout")
    args = parser.parse_args()
    main(args.db, args.min_stores, args.include_selgros, args.debug)
