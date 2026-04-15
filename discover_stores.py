"""
Discover retail stores by probing the API from populated locality centroids.

Source: data/reference/populatie romania siruta coords.csv — official Romanian
administrative dataset, 3 180 localities all with lat/lon and population.
No UAT matching needed — the API endpoint accepts any lat/lon directly.

Algorithm:
  1. Load localities from CSV filtered by --min-pop
  2. Deduplicate points within 4 km (greedy haversine) to avoid redundant probes
  3. For each point: GET GetStoresForProductsByLatLon with a small product batch
  4. Upsert all returned stores into DB (prices are NOT written)
  5. Checkpoint after every probe; resume on restart

Prerequisite: run fetch_reference.py first to populate the products table.

Usage:
  python discover_stores.py [--min-pop 2500] [--limit N] [--debug] [--dry-run] [--fresh]
"""

import argparse
import csv
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from tqdm import tqdm

from api import BASE, fetch_xml, parse_stores_and_prices
from db import init_db, upsert_store

# --- config ------------------------------------------------------------------

BUFFER_M = 5000        # API buffer; returns up to 50 nearest stores
PROBE_BATCH = 30       # product IDs per discovery probe
DEDUP_RADIUS_KM = 4.0  # drop a point if a kept point is within this distance
SLEEP = 0.5            # seconds between API calls

POP_CSV = Path("data/reference/populatie romania siruta coords.csv")
CHECKPOINT_PATH = "data/discover_stores_checkpoint.json"


# --- geometry ----------------------------------------------------------------

def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


def deduplicate_points(points, radius_km, debug=False):
    """
    Greedy dedup: keep a point only if no already-kept point is within radius_km.
    Expects input sorted by population desc so the most-populated locality wins.
    O(n²) — fine for a few hundred points.
    """
    kept = []
    for name, lat, lon, pop in points:
        if not any(haversine_km(lat, lon, klat, klon) < radius_km
                   for _, klat, klon, _ in kept):
            kept.append((name, lat, lon, pop))
    if debug:
        tqdm.write(f"[debug] Dedup: {len(points)} → {len(kept)} points "
                   f"(radius={radius_km}km)")
    return kept


# --- data loading ------------------------------------------------------------

def load_localities(csv_path, min_pop, debug=False):
    """
    Return list of (name, lat, lon, population) from the official Romanian
    locality CSV (populatie romania siruta coords.csv), filtered by min_pop,
    sorted by population descending.

    CSV columns: localitate, judet, cod_judet, siruta, tip_localitate,
                 lat, long, populatie, namecheck
    """
    places = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            pop = int(r["populatie"]) if r["populatie"] else 0
            if pop < min_pop:
                continue
            try:
                lat, lon = float(r["lat"]), float(r["long"])
            except (ValueError, TypeError):
                continue
            places.append((r["localitate"], lat, lon, pop))
    places.sort(key=lambda x: -x[3])
    if debug:
        tqdm.write(f"[debug] {len(places)} localities loaded with pop >= {min_pop:,}")
    return places


# --- checkpoint helpers ------------------------------------------------------

def _load_checkpoint(path):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        data = json.load(f)
    data["done"] = set(data["done"])
    return data


def _save_checkpoint(path, started_at, done, stores_seen):
    with open(path, "w") as f:
        json.dump({
            "started_at": started_at,
            "status": "in_progress",
            "done": sorted(done),
            "stores_seen": stores_seen,
        }, f)


def _finish_checkpoint(path, started_at, done, stores_seen):
    with open(path, "w") as f:
        json.dump({
            "started_at": started_at,
            "status": "completed",
            "done": sorted(done),
            "stores_seen": stores_seen,
        }, f)


# --- main --------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--db", default="data/prices.db",
                        help="SQLite DB path (default: data/prices.db)")
    parser.add_argument("--min-pop", type=int, default=2_500,
                        help="minimum locality population to probe (default: 2500)")
    parser.add_argument("--limit", type=int, default=None,
                        help="probe only first N localities — for testing")
    parser.add_argument("--fresh", action="store_true",
                        help="ignore saved checkpoint and start fresh")
    parser.add_argument("--debug", action="store_true",
                        help="verbose logging")
    parser.add_argument("--dry-run", action="store_true",
                        help="print probe points without making API calls")
    args = parser.parse_args()

    conn = init_db(args.db)

    # Product IDs used as the discovery probe (just enough to trigger store results)
    prod_ids = [r[0] for r in conn.execute(
        "SELECT id FROM products LIMIT ?", (PROBE_BATCH,)
    ).fetchall()]
    if not prod_ids:
        print("ERROR: No products in DB — run fetch_reference.py first.")
        conn.close()
        return
    csv_prods = ",".join(str(p) for p in prod_ids)
    if args.debug:
        tqdm.write(f"[debug] Probing with {len(prod_ids)} product IDs")

    # Load and deduplicate probe points
    places = load_localities(POP_CSV, args.min_pop, debug=args.debug)
    points = deduplicate_points(places, DEDUP_RADIUS_KM, debug=args.debug)

    tqdm.write(
        f"Probe points: {len(points)} "
        f"(pop >= {args.min_pop:,}, dedup radius={DEDUP_RADIUS_KM}km)"
    )

    if args.limit:
        points = points[: args.limit]
        tqdm.write(f"  Limited to first {args.limit} for testing")

    if args.dry_run:
        print("\n[dry-run] Probe points (name, lat, lon, pop):")
        for name, lat, lon, pop in points:
            print(f"  {lat:.5f}, {lon:.5f}  pop={pop:,}  — {name}")
        conn.close()
        return

    # Checkpoint
    cp = None if args.fresh else _load_checkpoint(CHECKPOINT_PATH)
    if cp and cp.get("status") == "completed":
        tqdm.write(
            f"Previous run already completed ({cp['started_at']}). "
            "Use --fresh to re-run."
        )
        conn.close()
        return

    if cp:
        started_at = cp["started_at"]
        done = cp["done"]
        stores_seen = cp.get("stores_seen", 0)
        tqdm.write(f"Resuming: {len(done)}/{len(points)} probes already done")
    else:
        started_at = datetime.now(timezone.utc).isoformat()
        done = set()
        stores_seen = 0

    stores_before = conn.execute("SELECT COUNT(*) FROM stores").fetchone()[0]
    probes = errors = 0

    try:
        with tqdm(points, unit="pt") as bar:
            for name, lat, lon, pop in bar:
                key = f"{lat:.5f},{lon:.5f}"
                bar.set_description(name[:28])
                bar.set_postfix(pop=f"{pop:,}", seen=stores_seen, err=errors)

                if key in done:
                    continue

                url = (
                    f"{BASE}/GetStoresForProductsByLatLon"
                    f"?lat={lat}&lon={lon}&buffer={BUFFER_M}"
                    f"&csvprodids={csv_prods}&OrderBy=price"
                )
                if args.debug:
                    tqdm.write(f"[debug] GET {url}")

                try:
                    fetched_at = datetime.now(timezone.utc).isoformat()
                    root = fetch_xml(url)
                    stores, _ = parse_stores_and_prices(root, fetched_at)
                    for s in stores:
                        upsert_store(conn, s["id"], s["name"], s["addr"],
                                     s["lat"], s["lon"], s["uat_id"],
                                     s["network_id"], s["zipcode"])
                    if stores:
                        conn.commit()
                    stores_seen += len(stores)
                    probes += 1
                    if args.debug and stores:
                        tqdm.write(f"[debug] {name}: {len(stores)} store(s)")
                except Exception as exc:
                    tqdm.write(f"  ERROR ({name}): {exc}")
                    errors += 1

                done.add(key)
                _save_checkpoint(CHECKPOINT_PATH, started_at, done, stores_seen)
                time.sleep(SLEEP)

        _finish_checkpoint(CHECKPOINT_PATH, started_at, done, stores_seen)

    except KeyboardInterrupt:
        tqdm.write(f"\nInterrupted after {probes} probes — checkpoint saved.")
        conn.close()
        raise
    finally:
        stores_after = conn.execute("SELECT COUNT(*) FROM stores").fetchone()[0]
        tqdm.write(
            f"\nDone. {probes} probes, {errors} errors.\n"
            f"Stores: {stores_before} → {stores_after} (+{stores_after - stores_before} new)"
        )
        conn.close()


if __name__ == "__main__":
    main()
