#!/usr/bin/env python3
"""Build per-basket cost data for the Coșul de Cămară page.

For each curated basket (config/baskets.json), score it at every network
nationally and per UAT:

  For each basket item:
    pick the cheapest available substitute SKU at that network in that UAT
    (any store in the UAT, latest price_date).

  weekly_cost  = Σ qty_per_week_i × cheapest_price_i
  monthly_cost = weekly_cost × 52/12

`comparable` is True only when ≥50% of basket items were found at that
network — protects the UI from ranking a network that's missing the basket.

Excludes B2B networks (SELGROS) via networks.is_b2b().

Outputs:
  docs/data/baskets/index.json   — metadata + UAT/network directory
  docs/data/baskets/{id}.json    — full data per basket (national + per-UAT)
"""

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from networks import short, is_b2b  # noqa: E402

DEFAULT_DB = ROOT / "data" / "prices.db"
BASKETS_CFG = ROOT / "config" / "baskets.json"
DEFAULT_OUT_DIR = ROOT / "docs" / "data" / "baskets"

WEEKS_PER_MONTH = 52 / 12  # 4.3333…
COMPARABLE_COVERAGE = 0.5  # need ≥50% items found to rank a network
# Outlier filter: drop a (product, network) price if it's <30% or >300% of the
# cross-network median for that product. Source API has occasional data errors
# (e.g. 1l of oil priced at 0.50 lei) that would bias the cheapest-pick logic.
OUTLIER_LOW = 0.30
OUTLIER_HIGH = 3.0


def load_baskets():
    with open(BASKETS_CFG, encoding="utf-8") as f:
        return json.load(f)["baskets"]


def fetch_uats(conn):
    rows = conn.execute("""
        SELECT u.id, u.name, u.center_lat, u.center_lon
        FROM uats u
        WHERE EXISTS (SELECT 1 FROM stores s WHERE s.uat_id = u.id)
    """).fetchall()
    return {
        r[0]: {"name": r[1] or f"UAT {r[0]}", "lat": r[2], "lon": r[3]}
        for r in rows
    }


def fetch_networks(conn):
    rows = conn.execute("SELECT id FROM retail_networks").fetchall()
    consumer = [(nid, short(nid)) for (nid,) in rows if not is_b2b(nid)]
    return sorted(consumer, key=lambda x: x[1])


def fetch_prices_per_uat(conn, product_ids, price_date):
    """Return {(pid, uat_id, network_id): min_price} after outlier filtering."""
    placeholders = ",".join("?" * len(product_ids))
    sql = f"""
        SELECT pr.product_id, s.uat_id, s.network_id, MIN(pr.price)
        FROM prices pr JOIN stores s ON pr.store_id = s.id
        WHERE pr.product_id IN ({placeholders})
          AND pr.price_date = ?
          AND s.network_id IS NOT NULL
          AND s.uat_id IS NOT NULL
          AND pr.price > 0
        GROUP BY pr.product_id, s.uat_id, s.network_id
    """
    raw = {}
    for pid, uat, nid, p in conn.execute(sql, (*product_ids, price_date)):
        raw[(pid, uat, nid)] = p
    return _filter_outliers(raw, key_pid_index=0)


def fetch_prices_national(conn, product_ids, price_date):
    """Return {(pid, network_id): min_price across all UATs} after outlier filter."""
    placeholders = ",".join("?" * len(product_ids))
    sql = f"""
        SELECT pr.product_id, s.network_id, MIN(pr.price)
        FROM prices pr JOIN stores s ON pr.store_id = s.id
        WHERE pr.product_id IN ({placeholders})
          AND pr.price_date = ?
          AND s.network_id IS NOT NULL
          AND pr.price > 0
        GROUP BY pr.product_id, s.network_id
    """
    raw = {}
    for pid, nid, p in conn.execute(sql, (*product_ids, price_date)):
        raw[(pid, nid)] = p
    return _filter_outliers(raw, key_pid_index=0)


def _median(xs):
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return None
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def _filter_outliers(price_dict, key_pid_index=0):
    """Drop (key, price) pairs whose price falls outside [OUTLIER_LOW, OUTLIER_HIGH]
    of the per-product median. The median is computed across all values sharing
    the same product_id (regardless of UAT/network)."""
    by_pid = {}
    for key, price in price_dict.items():
        pid = key[key_pid_index]
        by_pid.setdefault(pid, []).append(price)
    medians = {pid: _median(ps) for pid, ps in by_pid.items()}
    kept = {}
    dropped = 0
    for key, price in price_dict.items():
        pid = key[key_pid_index]
        m = medians.get(pid)
        if m is None or m == 0:
            kept[key] = price
            continue
        if OUTLIER_LOW * m <= price <= OUTLIER_HIGH * m:
            kept[key] = price
        else:
            dropped += 1
    if dropped:
        print(f"    outlier filter: dropped {dropped}/{len(price_dict)} entries")
    return kept


def score_basket(basket, prices_by_pid_nid, networks, with_drill=True):
    """Return {network_id: {network, cost_week, cost_month, items_found,
    items_total, comparable, items?}}."""
    out = {}
    n_items = len(basket["items"])
    for nid, short_n in networks:
        items_drill = []
        items_found = 0
        cost = 0.0
        for it in basket["items"]:
            best_pid, best_p = None, None
            for pid in it["product_ids"]:
                p = prices_by_pid_nid.get((pid, nid))
                if p is not None and (best_p is None or p < best_p):
                    best_pid, best_p = pid, p
            if best_p is not None:
                items_found += 1
                cost += it["qty_per_week"] * best_p
                if with_drill:
                    items_drill.append({
                        "label": it["label"],
                        "pid": best_pid,
                        "price": round(best_p, 2),
                        "qty": it["qty_per_week"],
                    })
            elif with_drill:
                items_drill.append({
                    "label": it["label"],
                    "pid": None,
                    "price": None,
                    "qty": it["qty_per_week"],
                })
        entry = {
            "network": short_n,
            "cost_week": round(cost, 2),
            "cost_month": round(cost * WEEKS_PER_MONTH, 2),
            "items_found": items_found,
            "items_total": n_items,
            "comparable": items_found >= n_items * COMPARABLE_COVERAGE,
        }
        if with_drill:
            entry["items"] = items_drill
        out[nid] = entry
    return out


def main():
    ap = argparse.ArgumentParser(description="Build per-basket cost JSON for the Coșul page")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="Path to prices.db")
    ap.add_argument("--out", default=str(DEFAULT_OUT_DIR), help="Output directory")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(args.db)
    price_date = conn.execute("SELECT MAX(price_date) FROM prices").fetchone()[0]
    print(f"Building baskets as of {price_date} (db={args.db})")

    baskets = load_baskets()
    uats = fetch_uats(conn)
    networks = fetch_networks(conn)
    print(f"  {len(networks)} consumer networks: {[s for _, s in networks]}")
    print(f"  {len(uats)} UATs with stores")

    index = {
        "as_of": price_date,
        "weeks_per_month": round(WEEKS_PER_MONTH, 4),
        "baskets": [],
        "networks": [{"id": nid, "short": s} for nid, s in networks],
        "uats": [
            {"id": uid, "name": m["name"], "lat": m["lat"], "lon": m["lon"]}
            for uid, m in sorted(uats.items(), key=lambda x: (x[1]["name"] or "").lower())
        ],
    }

    for basket in baskets:
        all_pids = sorted({pid for it in basket["items"] for pid in it["product_ids"]})

        nat_prices = fetch_prices_national(conn, all_pids, price_date)
        national = score_basket(basket, nat_prices, networks, with_drill=True)

        uat_prices = fetch_prices_per_uat(conn, all_pids, price_date)
        per_uat = {}
        for uat_id in uats:
            sub = {(pid, nid): p for (pid, u, nid), p in uat_prices.items() if u == uat_id}
            if not sub:
                continue
            scored = score_basket(basket, sub, networks, with_drill=False)
            scored = {nid: d for nid, d in scored.items() if d["items_found"] > 0}
            if scored:
                per_uat[str(uat_id)] = scored

        payload = {
            "id": basket["id"],
            "name_ro": basket["name_ro"],
            "name_en": basket["name_en"],
            "description_ro": basket["description_ro"],
            "items": basket["items"],
            "as_of": price_date,
            "national": national,
            "per_uat": per_uat,
        }
        out_path = out_dir / f"{basket['id']}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        size_kb = out_path.stat().st_size / 1024

        comparable_costs = [
            d["cost_month"] for d in national.values() if d["comparable"]
        ]
        index["baskets"].append({
            "id": basket["id"],
            "name_ro": basket["name_ro"],
            "name_en": basket["name_en"],
            "description_ro": basket["description_ro"],
            "items_total": len(basket["items"]),
            "national_cheapest_month": min(comparable_costs) if comparable_costs else None,
            "national_priciest_month": max(comparable_costs) if comparable_costs else None,
            "national_cheapest_network": min(
                national.items(), key=lambda kv: kv[1]["cost_month"] if kv[1]["comparable"] else float("inf"),
                default=(None, None)
            )[1]["network"] if comparable_costs else None,
            "uats_covered": len(per_uat),
        })
        print(f"  {basket['id']}.json — {size_kb:.0f} KB, {len(per_uat)} UATs")

    with open(out_dir / "index.json", "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, separators=(",", ":"))
    print(f"  index.json — {len(index['baskets'])} baskets, {len(index['uats'])} UATs")


if __name__ == "__main__":
    main()
