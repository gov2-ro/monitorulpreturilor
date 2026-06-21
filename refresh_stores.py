#!/usr/bin/env python3
"""refresh_stores.py — one-off: cheaply re-surface every store to (re)populate
network_id, logo_url and type_* from the store-level <Retailnetwork>/<Logo>/<Type>
elements.

Why: those fields now come from the *store element* in the API response
(see api.parse_stores_and_prices), which is present whenever a store appears —
independent of which product is queried or whether its price is 0. So we don't
need a full price sweep to fix the ~996 NULL-network stores: one short pass that
queries a small basket of near-universal products at every spatial anchor makes
~all stores appear and get re-upserted.

Unlike fetch_prices.py this writes STORES ONLY (no prices) and uses one batch
per anchor, so a full pass is ~minutes, not days. upsert_store COALESCEs, so a
known network_id/logo/type is never clobbered by a NULL.

Usage:
  python refresh_stores.py                      # full run, dynamic top-10 basket
  python refresh_stores.py --limit-anchors 20   # quick smoke test
  python refresh_stores.py --dry-run            # cluster + plan only, no HTTP
  python refresh_stores.py --basket 1012440,1013048   # explicit product ids
  python refresh_stores.py --basket-size 20 --debug
"""
import argparse
import logging
import time
from datetime import datetime, timezone

import requests
from tqdm import tqdm

from api import BASE, fetch_xml, parse_stores_and_prices
from db import (init_db, upsert_store, backfill_store_network_from_logo,
                check_store_network_conflicts)
from fetch_prices import _cluster_anchors, BUFFER_M, SLEEP_BETWEEN

# Fallback basket: top store-coverage products from the 2026-06-21 audit
# (Merci, Jacobs, Ketchup, Mars, Twix, ...). Used only if prices_current is empty.
FALLBACK_BASKET = [1012440, 1013048, 1013565, 1016517, 1013007,
                   1018109, 1015719, 1032882, 1066742, 1013940]


def _default_basket(conn, n):
    """Top-N products by distinct-store coverage (self-adjusting, no stale ids)."""
    rows = conn.execute(
        "SELECT product_id FROM prices_current "
        "GROUP BY product_id ORDER BY COUNT(DISTINCT store_id) DESC LIMIT ?",
        (n,),
    ).fetchall()
    return [r[0] for r in rows] or FALLBACK_BASKET[:n]


def _null_network_count(conn):
    return conn.execute(
        "SELECT COUNT(*) FROM stores WHERE network_id IS NULL"
    ).fetchone()[0]


def main():
    ap = argparse.ArgumentParser(
        description="Re-surface stores to refresh network/logo/type (no prices).")
    ap.add_argument("--db", default="data/prices.db")
    ap.add_argument("--basket", help="comma-separated product ids (overrides --basket-size)")
    ap.add_argument("--basket-size", type=int, default=10,
                    help="number of top-coverage products to query (capped at 200)")
    ap.add_argument("--limit-anchors", type=int, help="process only the first N anchors")
    ap.add_argument("--dry-run", action="store_true", help="cluster and plan only; no HTTP/writes")
    ap.add_argument("--debug", action="store_true", help="verbose logging")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    conn = init_db(args.db)  # ensures the logo_url/type_* migration is applied

    stores_raw = conn.execute(
        "SELECT id, name, lat, lon, surrounding_population FROM stores "
        "WHERE lat IS NOT NULL AND lon IS NOT NULL "
        "AND (is_active IS NULL OR is_active = 1)"
    ).fetchall()
    if not stores_raw:
        tqdm.write("No stores found — run discover_stores.py first.")
        return

    if args.basket:
        basket = [int(x) for x in args.basket.split(",") if x.strip()]
    else:
        basket = _default_basket(conn, min(args.basket_size, 200))
    csv_ids = ",".join(str(p) for p in basket)

    anchors, anchor_covers, anchor_radius = _cluster_anchors(stores_raw)
    if args.limit_anchors:
        anchors = anchors[:args.limit_anchors]

    null_before = _null_network_count(conn)
    tqdm.write(f"Stores: {len(stores_raw)} active | anchors: {len(anchors)} | "
               f"basket: {len(basket)} products | NULL-network now: {null_before}")
    if args.dry_run:
        tqdm.write(f"Dry run — basket={basket}. No HTTP performed.")
        return

    fetched_at = datetime.now(timezone.utc).isoformat()
    seen_stores = set()
    errors = cap_hits = 0

    for n, a in enumerate(tqdm(anchors, desc="anchors", unit="anchor")):
        store_id, lat, lon = a[0], a[2], a[3]
        buf = anchor_radius.get(store_id, BUFFER_M)
        url = (f"{BASE}/GetStoresForProductsByLatLon"
               f"?lat={lat}&lon={lon}&buffer={buf}&csvprodids={csv_ids}&OrderBy=price")
        try:
            root = fetch_xml(url)
        except requests.exceptions.RequestException as exc:
            tqdm.write(f"  WARN: anchor {store_id} failed after retries: {exc}")
            errors += 1
            time.sleep(SLEEP_BETWEEN)
            continue
        result_stores, _prices = parse_stores_and_prices(root, fetched_at)  # prices discarded
        if len(result_stores) >= 50 and len(anchor_covers.get(store_id, [])) > 50:
            cap_hits += 1
        for s in result_stores:
            upsert_store(conn, s["id"], s["name"], s["addr"], s["lat"], s["lon"],
                         s["uat_id"], s["network_id"], s["zipcode"],
                         logo_url=s.get("logo_url"), type_id=s.get("type_id"),
                         type_name=s.get("type_name"))
            seen_stores.add(s["id"])
        if n % 25 == 0:
            conn.commit()
        time.sleep(SLEEP_BETWEEN)

    conn.commit()

    # Logo fallback for any store the store-element parse still left NULL.
    logo_tagged = backfill_store_network_from_logo(conn)
    conflicts = check_store_network_conflicts(conn)
    null_after = _null_network_count(conn)

    tqdm.write(f"\nDone. anchors={len(anchors)} stores_seen={len(seen_stores)} "
               f"errors={errors} cap_hits={cap_hits}")
    tqdm.write(f"NULL-network: {null_before} -> {null_after} "
               f"(logo backfill tagged {logo_tagged}); conflicts={len(conflicts)}")
    for c in conflicts[:10]:
        tqdm.write(f"  conflict: store {c['store_id']} ({c['store_name']}): "
                   f"db={c['db_network_id']} vs logo->{c['logo_network_id']} "
                   f"({c['logo_network_name']})")
    conn.close()


if __name__ == "__main__":
    main()
