#!/usr/bin/env python3
"""Build CPI prototype data — basket cost trend over available price dates.

Tracks the national cheapest-network basket cost for each curated basket
across all available retail price_dates. With only days of history this is
a skeleton; it becomes meaningful over weeks/months as data accumulates.

Also tracks per-product price changes vs the earliest available date.

Outputs:
  docs/data/cpi.json  — basket cost time series + product change table
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from networks import is_b2b  # noqa: E402

DEFAULT_DB = ROOT / "data" / "prices.db"
DEFAULT_OUT = ROOT / "docs" / "data" / "cpi.json"
BASKETS_CFG = ROOT / "config" / "baskets.json"

OUTLIER_LOW, OUTLIER_HIGH = 0.30, 3.0
WEEKS_PER_MONTH = 52 / 12


def _median(xs):
    s = sorted(xs)
    n = len(s)
    return None if n == 0 else (s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2]))


def fetch_dates(conn):
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT price_date FROM prices ORDER BY price_date"
    )]


def fetch_national_min(conn, price_date, product_ids):
    """Return {pid: {nid: min_price}} for the given date, outlier-filtered."""
    if not product_ids:
        return {}
    placeholders = ",".join("?" * len(product_ids))
    rows = conn.execute(f"""
        SELECT pr.product_id, s.network_id, MIN(pr.price)
        FROM prices pr JOIN stores s ON pr.store_id = s.id
        WHERE pr.price_date = ? AND pr.product_id IN ({placeholders})
          AND s.network_id IS NOT NULL AND pr.price > 0
        GROUP BY pr.product_id, s.network_id
    """, (price_date, *product_ids)).fetchall()

    by_pid = {}
    for pid, nid, p in rows:
        if is_b2b(nid):
            continue
        by_pid.setdefault(pid, {})[nid] = p

    # Outlier filter
    kept = {}
    for pid, by_nid in by_pid.items():
        m = _median(list(by_nid.values()))
        if not m:
            kept[pid] = by_nid
            continue
        kept[pid] = {nid: p for nid, p in by_nid.items()
                     if OUTLIER_LOW * m <= p <= OUTLIER_HIGH * m}
    return kept


def score_basket_cheapest(basket, prices):
    """Return (cost_week, items_found) using cheapest network per item."""
    cost, found = 0.0, 0
    for it in basket["items"]:
        best = None
        for pid in it["product_ids"]:
            for nid, p in prices.get(pid, {}).items():
                if best is None or p < best:
                    best = p
        if best is not None:
            cost += it["qty_per_week"] * best
            found += 1
    return round(cost, 2), found


def build():
    ap = argparse.ArgumentParser(description="Build CPI prototype data")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    baskets = json.load(open(BASKETS_CFG, encoding="utf-8"))["baskets"]

    dates = fetch_dates(conn)
    if not dates:
        print("No price dates found.")
        return
    print(f"Building CPI over {len(dates)} dates: {dates[0]} → {dates[-1]}")

    # Collect all product IDs across all baskets
    all_pids = sorted({pid for b in baskets for it in b["items"] for pid in it["product_ids"]})

    # Build time series per basket
    series = {b["id"]: [] for b in baskets}
    # Track per-product prices on first vs last date
    first_prices = {}  # {pid: {nid: price}} on first date
    last_prices = {}   # same on last date

    for i, date in enumerate(dates):
        prices = fetch_national_min(conn, date, all_pids)
        if i == 0:
            first_prices = {pid: dict(by_nid) for pid, by_nid in prices.items()}
        if i == len(dates) - 1:
            last_prices = prices

        for basket in baskets:
            cost_week, items_found = score_basket_cheapest(basket, prices)
            n_items = len(basket["items"])
            series[basket["id"]].append({
                "date": date,
                "cost_week": cost_week,
                "cost_month": round(cost_week * WEEKS_PER_MONTH, 2),
                "items_found": items_found,
                "items_total": n_items,
                "comparable": items_found >= n_items * 0.5,
            })

    # Per-product change table (first vs last date)
    product_changes = []
    for basket in baskets:
        for it in basket["items"]:
            pids = it["product_ids"]
            # Best price first date
            p_first = min(
                (v for pid in pids for v in first_prices.get(pid, {}).values()),
                default=None
            )
            p_last = min(
                (v for pid in pids for v in last_prices.get(pid, {}).values()),
                default=None
            )
            if p_first and p_last and p_first > 0:
                change_pct = round(100.0 * (p_last - p_first) / p_first, 1)
            else:
                change_pct = None
            product_changes.append({
                "label": it["label"],
                "basket_id": basket["id"],
                "price_first": round(p_first, 2) if p_first else None,
                "price_last": round(p_last, 2) if p_last else None,
                "change_pct": change_pct,
            })

    # Deduplicate (same item appears in multiple baskets)
    seen = set()
    unique_changes = []
    for c in product_changes:
        k = c["label"]
        if k not in seen:
            seen.add(k)
            unique_changes.append(c)
    unique_changes.sort(key=lambda x: abs(x["change_pct"] or 0), reverse=True)

    # Base index: cost on first date for camara basket = 100
    camara_first = next((p["cost_month"] for p in series["camara"] if p["comparable"]), None)

    payload = {
        "dates": dates,
        "n_dates": len(dates),
        "first_date": dates[0],
        "last_date": dates[-1],
        "base_cost_month": round(camara_first, 2) if camara_first else None,
        "baskets": [
            {
                "id": b["id"],
                "name_ro": b["name_ro"],
                "series": series[b["id"]],
            }
            for b in baskets
        ],
        "product_changes": unique_changes,
        "caveat": (
            f"Date insuficiente pentru un indice robust — {len(dates)} zile disponibile. "
            "Valorile vor deveni semnificative după acumularea a cel puțin 4 săptămâni de date."
        ),
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    size_kb = out.stat().st_size / 1024

    for b in baskets:
        pts = series[b["id"]]
        if pts:
            first_c = pts[0]["cost_month"]
            last_c = pts[-1]["cost_month"]
            print(f"  {b['id']:12s}  {first_c:>6.2f} → {last_c:>6.2f} lei/lună")
    print(f"  cpi.json — {size_kb:.0f} KB, {len(dates)} dates, {len(unique_changes)} products tracked")


if __name__ == "__main__":
    build()
