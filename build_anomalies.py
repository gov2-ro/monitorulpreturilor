#!/usr/bin/env python3
"""Build today's price-anomaly feed.

For the latest `price_date` snapshot, find products whose cheapest network
price differs sharply from their priciest network price. The output drives
the Anomalii page: a scrollable feed of "save X lei by buying it at Y instead
of Z".

Process per product (with stores in ≥2 consumer networks today):
  1. Take the per-network MIN price across all stores (most-favourable shelf).
  2. Drop network prices outside [OUTLIER_LOW, OUTLIER_HIGH] × the cross-network
     median for that product — same filter as build_baskets.py, same data-quality
     issue (occasional API errors like 1L oil at 0.50 lei).
  3. Compute ratio = priciest / cheapest. Keep products with ratio ≥ MIN_RATIO.
  4. Rank by ratio desc; keep top KEEP_TOP entries.

Excludes B2B networks (SELGROS) via networks.is_b2b().

Outputs:
  docs/data/anomalies_today.json
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from networks import short, is_b2b  # noqa: E402

DEFAULT_DB = ROOT / "data" / "prices.db"
DEFAULT_OUT = ROOT / "docs" / "data" / "anomalies_today.json"

OUTLIER_LOW = 0.30
OUTLIER_HIGH = 3.0
MIN_RATIO = 1.5  # priciest is ≥1.5× cheapest
MIN_NETWORKS = 2
KEEP_TOP = 300


def _median(xs):
    s = sorted(xs)
    n = len(s)
    return None if n == 0 else (s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2]))


def fetch_per_network_min(conn, price_date):
    """Return {pid: {nid: min_price_across_stores}}, B2B excluded."""
    rows = conn.execute("""
        SELECT pr.product_id, s.network_id, MIN(pr.price)
        FROM prices pr JOIN stores s ON pr.store_id = s.id
        WHERE pr.price_date = ?
          AND s.network_id IS NOT NULL
          AND pr.price > 0
        GROUP BY pr.product_id, s.network_id
    """, (price_date,)).fetchall()
    out = {}
    dropped_b2b = 0
    for pid, nid, p in rows:
        if is_b2b(nid):
            dropped_b2b += 1
            continue
        out.setdefault(pid, {})[nid] = p
    print(f"  loaded {len(out)} products × networks (excluded {dropped_b2b} B2B rows)")
    return out


def filter_outliers(per_pid):
    """Drop network prices outside [OUTLIER_LOW, OUTLIER_HIGH] × per-product median."""
    kept_pid = {}
    dropped = 0
    for pid, by_nid in per_pid.items():
        if len(by_nid) < MIN_NETWORKS:
            continue
        m = _median(list(by_nid.values()))
        if m is None or m == 0:
            kept_pid[pid] = by_nid
            continue
        lo, hi = OUTLIER_LOW * m, OUTLIER_HIGH * m
        kept = {nid: p for nid, p in by_nid.items() if lo <= p <= hi}
        dropped += len(by_nid) - len(kept)
        if len(kept) >= MIN_NETWORKS:
            kept_pid[pid] = kept
    if dropped:
        print(f"  outlier filter: dropped {dropped} network rows")
    return kept_pid


def fetch_product_meta(conn, pids):
    """Return {pid: {name, category}}."""
    if not pids:
        return {}
    placeholders = ",".join("?" * len(pids))
    sql = f"""
        SELECT p.id, p.name, c.name
        FROM products p LEFT JOIN categories c ON p.categ_id = c.id
        WHERE p.id IN ({placeholders})
    """
    return {
        pid: {"name": name, "category": cat}
        for pid, name, cat in conn.execute(sql, list(pids))
    }


def fetch_units_brands(conn, pids, price_date):
    """Return {pid: {unit, brand}} from today's price rows (most common values)."""
    if not pids:
        return {}
    placeholders = ",".join("?" * len(pids))
    sql = f"""
        SELECT product_id, unit, brand
        FROM prices
        WHERE price_date = ? AND product_id IN ({placeholders})
    """
    by_pid = {}
    for pid, unit, brand in conn.execute(sql, (price_date, *pids)):
        d = by_pid.setdefault(pid, {"units": {}, "brands": {}})
        if unit:
            d["units"][unit] = d["units"].get(unit, 0) + 1
        if brand:
            d["brands"][brand] = d["brands"].get(brand, 0) + 1
    out = {}
    for pid, d in by_pid.items():
        unit = max(d["units"], key=d["units"].get) if d["units"] else None
        brand = max(d["brands"], key=d["brands"].get) if d["brands"] else None
        out[pid] = {"unit": unit, "brand": brand}
    return out


def build(db_path, out_path):
    conn = sqlite3.connect(db_path)
    price_date = conn.execute("SELECT MAX(price_date) FROM prices").fetchone()[0]
    print(f"Building anomalies as of {price_date} (db={db_path})")

    raw = fetch_per_network_min(conn, price_date)
    cleaned = filter_outliers(raw)

    rows = []
    for pid, by_nid in cleaned.items():
        items = [(nid, p) for nid, p in by_nid.items()]
        items.sort(key=lambda x: x[1])
        cheapest_nid, cheapest_p = items[0]
        priciest_nid, priciest_p = items[-1]
        if cheapest_p <= 0:
            continue
        ratio = priciest_p / cheapest_p
        if ratio < MIN_RATIO:
            continue
        rows.append({
            "pid": pid,
            "ratio": ratio,
            "cheapest_nid": cheapest_nid,
            "cheapest_price": cheapest_p,
            "priciest_nid": priciest_nid,
            "priciest_price": priciest_p,
            "by_network": items,
        })

    rows.sort(key=lambda r: r["ratio"], reverse=True)
    top = rows[:KEEP_TOP]
    print(f"  {len(rows)} products with ratio ≥ {MIN_RATIO}, keeping top {len(top)}")

    pids = [r["pid"] for r in top]
    meta = fetch_product_meta(conn, pids)
    extras = fetch_units_brands(conn, pids, price_date)

    out = []
    for r in top:
        m = meta.get(r["pid"], {})
        e = extras.get(r["pid"], {})
        out.append({
            "product_id": r["pid"],
            "product": m.get("name") or f"Product {r['pid']}",
            "category": m.get("category"),
            "brand": e.get("brand"),
            "unit": e.get("unit"),
            "cheapest": {"network": short(r["cheapest_nid"]), "price": round(r["cheapest_price"], 2)},
            "priciest": {"network": short(r["priciest_nid"]), "price": round(r["priciest_price"], 2)},
            "ratio": round(r["ratio"], 2),
            "save_lei": round(r["priciest_price"] - r["cheapest_price"], 2),
            "save_pct": round(100.0 * (1.0 - r["cheapest_price"] / r["priciest_price"])),
            "by_network": [[short(nid), round(p, 2)] for nid, p in r["by_network"]],
        })

    payload = {
        "as_of": price_date,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "params": {
            "min_ratio": MIN_RATIO,
            "min_networks": MIN_NETWORKS,
            "outlier_low": OUTLIER_LOW,
            "outlier_high": OUTLIER_HIGH,
            "keep_top": KEEP_TOP,
        },
        "count": len(out),
        "items": out,
    }

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    size_kb = out_path.stat().st_size / 1024
    print(f"  {out_path.name} — {size_kb:.0f} KB, {len(out)} anomalies")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build today's price-anomaly feed JSON")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="Path to prices.db")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="Output JSON path")
    args = ap.parse_args()
    build(args.db, args.out)
