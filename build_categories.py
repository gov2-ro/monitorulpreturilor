#!/usr/bin/env python3
"""Build per-category spread data for the Category Explorer page.

For the latest price_date, per category:
  - List products that have prices at ≥2 consumer networks (outlier-filtered).
  - Rank each product by cross-network price ratio (priciest/cheapest).
  - Output a summary index + per-category detail JSON.

Outlier filter and B2B exclusion match build_anomalies.py so comparisons
are consistent across pages.

Outputs:
  docs/data/categories/index.json      — category list + summary stats
  docs/data/categories/{id}.json       — per-category product list
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from networks import short, is_b2b  # noqa: E402

DEFAULT_DB = ROOT / "data" / "prices.db"
DEFAULT_OUT = ROOT / "docs" / "data" / "categories"

OUTLIER_LOW = 0.30
OUTLIER_HIGH = 3.0
MIN_NETWORKS = 2
# Max products to emit per category (sorted by ratio desc)
MAX_PER_CAT = 200


def _median(xs):
    s = sorted(xs)
    n = len(s)
    return None if n == 0 else (s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2]))


def fetch_categories(conn):
    """Return {id: {name, parent_id, product_count}} for all categories."""
    rows = conn.execute("SELECT id, name, parent_id FROM categories").fetchall()
    cats = {r[0]: {"name": r[1], "parent_id": r[2]} for r in rows}
    for cid in cats:
        n = conn.execute("SELECT COUNT(*) FROM products WHERE categ_id=?", (cid,)).fetchone()[0]
        cats[cid]["product_count"] = n
    return cats


def fetch_products_for_date(conn, price_date):
    """Return {pid: {name, categ_id}} for products with any price today."""
    rows = conn.execute("""
        SELECT DISTINCT pr.product_id, p.name, p.categ_id
        FROM prices pr JOIN products p ON pr.product_id = p.id
        WHERE pr.price_date = ?
    """, (price_date,)).fetchall()
    return {r[0]: {"name": r[1], "categ_id": r[2]} for r in rows}


def fetch_per_network_min(conn, price_date, product_ids):
    """Return {pid: {nid: min_price}}, B2B excluded."""
    if not product_ids:
        return {}
    placeholders = ",".join("?" * len(product_ids))
    rows = conn.execute(f"""
        SELECT pr.product_id, s.network_id, MIN(pr.price)
        FROM prices pr JOIN stores s ON pr.store_id = s.id
        WHERE pr.price_date = ?
          AND pr.product_id IN ({placeholders})
          AND s.network_id IS NOT NULL
          AND pr.price > 0
        GROUP BY pr.product_id, s.network_id
    """, (price_date, *product_ids)).fetchall()
    out = {}
    for pid, nid, p in rows:
        if is_b2b(nid):
            continue
        out.setdefault(pid, {})[nid] = p
    return out


def filter_outliers(per_pid):
    kept = {}
    for pid, by_nid in per_pid.items():
        m = _median(list(by_nid.values()))
        if m is None or m == 0:
            kept[pid] = by_nid
            continue
        lo, hi = OUTLIER_LOW * m, OUTLIER_HIGH * m
        filt = {nid: p for nid, p in by_nid.items() if lo <= p <= hi}
        if len(filt) >= MIN_NETWORKS:
            kept[pid] = filt
    return kept


def score_products(products, prices, pids_in_cat):
    """Score products in this category. Returns list sorted by ratio desc."""
    out = []
    for pid in pids_in_cat:
        by_nid = prices.get(pid)
        if not by_nid or len(by_nid) < MIN_NETWORKS:
            continue
        ranked = sorted(by_nid.items(), key=lambda x: x[1])
        cheapest_nid, cheapest_p = ranked[0]
        priciest_nid, priciest_p = ranked[-1]
        if cheapest_p <= 0:
            continue
        ratio = priciest_p / cheapest_p
        out.append({
            "product_id": pid,
            "product": products[pid]["name"],
            "ratio": round(ratio, 2),
            "cheapest": {"network": short(cheapest_nid), "price": round(cheapest_p, 2)},
            "priciest": {"network": short(priciest_nid), "price": round(priciest_p, 2)},
            "save_lei": round(priciest_p - cheapest_p, 2),
            "save_pct": round(100.0 * (1.0 - cheapest_p / priciest_p)),
            "n_networks": len(by_nid),
            "by_network": [[short(nid), round(p, 2)] for nid, p in ranked],
        })
    out.sort(key=lambda x: x["ratio"], reverse=True)
    return out[:MAX_PER_CAT]


def network_leaderboard(scored):
    """Count how many times each network is cheapest across scored products."""
    tally = {}
    for item in scored:
        n = item["cheapest"]["network"]
        tally[n] = tally.get(n, 0) + 1
    return sorted(tally.items(), key=lambda x: -x[1])


def main():
    ap = argparse.ArgumentParser(description="Build per-category price spread data")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(args.db)
    price_date = conn.execute("SELECT MAX(price_date) FROM prices").fetchone()[0]
    print(f"Building categories as of {price_date}")

    cats = fetch_categories(conn)
    products = fetch_products_for_date(conn, price_date)
    print(f"  {len(products)} products with prices today, {len(cats)} categories")

    all_pids = list(products.keys())
    raw_prices = fetch_per_network_min(conn, price_date, all_pids)
    prices = filter_outliers(raw_prices)
    print(f"  {len(prices)} products after outlier filter (≥{MIN_NETWORKS} networks)")

    # Group products by category
    by_cat = {}
    for pid, meta in products.items():
        cid = meta.get("categ_id")
        if cid:
            by_cat.setdefault(cid, []).append(pid)

    index_entries = []
    written = 0

    for cid, pids in sorted(by_cat.items(), key=lambda x: -len(x[1])):
        cat_meta = cats.get(cid, {"name": f"Cat {cid}", "parent_id": None, "product_count": len(pids)})

        scored = score_products(products, prices, pids)
        if not scored:
            continue

        top_ratio = scored[0]["ratio"] if scored else None
        total_save = sum(s["save_lei"] for s in scored)
        leaderboard = network_leaderboard(scored)

        payload = {
            "id": cid,
            "name": cat_meta["name"],
            "as_of": price_date,
            "products_total": len(pids),
            "products_with_spread": len(scored),
            "top_ratio": top_ratio,
            "leaderboard": [{"network": n, "cheapest_count": c} for n, c in leaderboard],
            "items": scored,
        }
        out_path = out_dir / f"{cid}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        size_kb = out_path.stat().st_size / 1024

        index_entries.append({
            "id": cid,
            "name": cat_meta["name"],
            "products_total": len(pids),
            "products_with_spread": len(scored),
            "top_ratio": round(top_ratio, 2) if top_ratio else None,
            "total_save_lei": round(total_save, 2),
            "leaderboard": leaderboard[:3],
        })
        print(f"  {cid}.json  {cat_meta['name'][:40]:40s}  {len(scored):4d} products  {size_kb:.0f} KB")
        written += 1

    index_entries.sort(key=lambda x: -x["products_with_spread"])
    index = {
        "as_of": price_date,
        "count": len(index_entries),
        "categories": index_entries,
    }
    with open(out_dir / "index.json", "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, separators=(",", ":"))
    print(f"  index.json — {len(index_entries)} categories")


if __name__ == "__main__":
    main()
