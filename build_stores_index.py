#!/usr/bin/env python3
"""Build docs/data/stores_index.json for the Aproape de tine geolocation page.

Emits a compact list of all stores with coordinates + basket cost at each
store's UAT (national basket cost used when UAT-specific data is missing).

Output format (one entry per store):
  [id, name, addr, lat, lon, network_short, uat_id, uat_name, basket_min_month]

Sorted by network short name then store name.
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
BASKETS_DIR = ROOT / "docs" / "data" / "baskets"
DEFAULT_OUT = ROOT / "docs" / "data" / "stores_index.json"


def load_basket_uat_costs(baskets_dir):
    """Return {uat_id: min_month_cost} from camara basket per_uat data."""
    path = Path(baskets_dir) / "camara.json"
    if not path.exists():
        return {}, None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    # National fallback: cheapest comparable network
    nat = data.get("national", {})
    comparable = {nid: d for nid, d in nat.items() if d.get("comparable")}
    nat_min = min((d["cost_month"] for d in comparable.values()), default=None)

    per_uat = {}
    for uat_id_str, by_nid in data.get("per_uat", {}).items():
        comp = {nid: d for nid, d in by_nid.items() if d.get("comparable")}
        if not comp:
            continue
        min_cost = min(d["cost_month"] for d in comp.values())
        per_uat[int(uat_id_str)] = round(min_cost, 2)

    return per_uat, round(nat_min, 2) if nat_min else None


def main():
    ap = argparse.ArgumentParser(description="Build stores index for geolocation page")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--baskets", default=str(BASKETS_DIR))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)

    uat_basket, nat_basket = load_basket_uat_costs(args.baskets)
    print(f"  Basket data: {len(uat_basket)} UATs, national fallback={nat_basket}")

    rows = conn.execute("""
        SELECT s.id, s.name, s.addr, s.lat, s.lon,
               s.network_id, s.uat_id,
               COALESCE(u.name, '') AS uat_name
        FROM stores s
        LEFT JOIN uats u ON s.uat_id = u.id
        WHERE s.lat IS NOT NULL AND s.lon IS NOT NULL
          AND s.lat != 0 AND s.lon != 0
          AND s.network_id IS NOT NULL
        ORDER BY s.network_id, s.name
    """).fetchall()

    # Compact array format: [id, name, addr, lat, lon, network, uat_id, uat_name, basket_min_month]
    # Saves ~40% vs object format
    stores = []
    for sid, name, addr, lat, lon, nid, uat_id, uat_name in rows:
        if is_b2b(nid):
            continue
        basket_cost = uat_basket.get(uat_id, nat_basket)
        stores.append([
            sid,
            name,
            addr or "",
            round(lat, 6),
            round(lon, 6),
            short(nid),
            uat_id,
            uat_name or f"UAT {uat_id}",
            basket_cost,
        ])

    payload = {
        "fields": ["id", "name", "addr", "lat", "lon", "network", "uat_id", "uat_name", "basket_min_month"],
        "nat_basket_min_month": round(nat_basket, 2) if nat_basket else None,
        "stores": stores,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    size_kb = out.stat().st_size / 1024
    print(f"  stores_index.json — {len(stores)} stores, {size_kb:.0f} KB")


if __name__ == "__main__":
    main()
