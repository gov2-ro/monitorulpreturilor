"""
Discover retail stores by sampling populated UAT centroids.

Algorithm:
  1. Load UATs from DB + join with population spreadsheet
  2. Filter by --pop-threshold (default 10000)
  3. For large UATs (bbox diagonal > 10km): tile with 8km grid
  4. Globally deduplicate sampling points within 4km
  5. For each point: fetch stores via GetStoresForProductsByLatLon
  6. Upsert stores into DB (prices are NOT written)

Prerequisite: run fetch_reference.py first to populate the uats + products tables.

Usage:
  python discover_stores.py [--pop-threshold 10000] [--debug] [--dry-run]
"""

import argparse
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import openpyxl
from tqdm import tqdm

from api import BASE, fetch_xml, parse_stores_and_prices
from db import init_db, upsert_store

BUFFER = 5000          # metres — API silently returns empty above ~5000
GRID_SPACING_KM = 8.0  # adjacent circles overlap ~1km at 5km radius
DEDUP_RADIUS_KM = 4.0  # drop a point if a kept point is this close
SLEEP = 0.5            # seconds between API calls


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km (fast approximation)."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


def bbox_from_wkt(wkt):
    """POLYGON((lon lat, …)) → (min_lat, min_lon, max_lat, max_lon)."""
    start = wkt.index("((") + 2
    end = wkt.rindex("))")
    pairs = [p.strip().split() for p in wkt[start:end].split(",") if p.strip()]
    lons = [float(p[0]) for p in pairs]
    lats = [float(p[1]) for p in pairs]
    return min(lats), min(lons), max(lats), max(lons)


def tile_bbox(min_lat, min_lon, max_lat, max_lon, spacing_km):
    """Grid of (lat, lon) points covering the bbox at spacing_km intervals."""
    lat_step = spacing_km / 111.0
    center_lat = (min_lat + max_lat) / 2
    lon_step = spacing_km / (111.0 * math.cos(math.radians(center_lat)))
    points = []
    lat = min_lat
    while lat <= max_lat + lat_step / 2:
        lon = min_lon
        while lon <= max_lon + lon_step / 2:
            points.append((lat, lon))
            lon += lon_step
        lat += lat_step
    return points


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def load_population(xlsx_path):
    """Return {siruta_id: population} from the reference spreadsheet."""
    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    ws = wb.active
    pop = {}
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue  # skip header
        siruta, population = row[1], row[4]
        if siruta is not None and population is not None:
            pop[int(siruta)] = int(population)
    wb.close()
    return pop


def generate_sampling_points(uats, pop_map, pop_threshold, debug=False):
    """
    Returns list of (lat, lon, uat_name).
    UATs below threshold are skipped.
    Large UATs (bbox diagonal > 10km) get a grid; small ones use centroid.
    """
    points = []
    skipped_pop = skipped_no_coord = 0

    for u in uats:
        uat_pop = pop_map.get(u["id"])
        if uat_pop is None or uat_pop < pop_threshold:
            skipped_pop += 1
            continue

        wkt = u.get("wkt")
        lat, lon = u["center_lat"], u["center_lon"]

        if not wkt or lat is None or lon is None:
            skipped_no_coord += 1
            continue

        min_lat, min_lon, max_lat, max_lon = bbox_from_wkt(wkt)
        diag = haversine_km(min_lat, min_lon, max_lat, max_lon)

        if diag <= 10.0:
            points.append((lat, lon, u["name"]))
            if debug:
                print(f"  {u['name']} (pop {uat_pop:,}) → centroid  diag={diag:.1f}km")
        else:
            grid = tile_bbox(min_lat, min_lon, max_lat, max_lon, GRID_SPACING_KM)
            for glat, glon in grid:
                points.append((glat, glon, u["name"]))
            if debug:
                print(f"  {u['name']} (pop {uat_pop:,}) → {len(grid)} grid pts  diag={diag:.1f}km")

    if debug or True:  # always print summary
        print(f"  UATs included: {len({p[2] for p in points})}")
        print(f"  Skipped (below threshold or no pop data): {skipped_pop}")
        print(f"  Skipped (no coords/WKT): {skipped_no_coord}")
    return points


def deduplicate_points(points, radius_km, debug=False):
    """
    Greedy dedup: keep a point only if no already-kept point is within radius_km.
    O(n²) — fine for the expected hundreds of points in V1.
    """
    kept = []
    for lat, lon, label in points:
        if not any(haversine_km(lat, lon, klat, klon) < radius_km
                   for klat, klon, _ in kept):
            kept.append((lat, lon, label))
    if debug:
        print(f"  Dedup: {len(points)} → {len(kept)} points (radius {radius_km}km)")
    return kept


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default="data/prices.db",
                        help="SQLite DB path (default: data/prices.db)")
    parser.add_argument("--pop-threshold", type=int, default=10_000,
                        help="Minimum UAT population to sample (default: 10000)")
    parser.add_argument("--debug", action="store_true",
                        help="Verbose per-UAT output")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print sampling points, skip API calls")
    args = parser.parse_args()

    conn = init_db(args.db)

    # ---- population reference ----
    xlsx = Path("data/reference/populatie romania siruta coords.xlsx")
    print(f"Loading population data from {xlsx}...")
    pop_map = load_population(xlsx)
    print(f"  {len(pop_map):,} entries loaded")

    # ---- UATs from DB ----
    rows = conn.execute(
        "SELECT id, name, wkt, center_lat, center_lon FROM uats"
    ).fetchall()
    uats = [
        {"id": r[0], "name": r[1], "wkt": r[2], "center_lat": r[3], "center_lon": r[4]}
        for r in rows
    ]
    print(f"  {len(uats)} UATs in DB")
    if len(uats) < 100:
        print("  WARNING: UAT count is low — run fetch_reference.py first")

    # ---- product IDs for discovery queries ----
    prod_ids = [r[0] for r in conn.execute("SELECT id FROM products LIMIT 30").fetchall()]
    if not prod_ids:
        print("ERROR: No products in DB — run fetch_reference.py first")
        conn.close()
        return
    csv_prods = ",".join(str(p) for p in prod_ids)
    if args.debug:
        print(f"  Using {len(prod_ids)} product IDs for discovery")

    # ---- sampling points ----
    print(f"\nGenerating sampling points (pop >= {args.pop_threshold:,})...")
    raw_points = generate_sampling_points(uats, pop_map, args.pop_threshold, debug=args.debug)
    print(f"  {len(raw_points)} raw points")
    deduped = deduplicate_points(raw_points, DEDUP_RADIUS_KM, debug=True)

    if args.dry_run:
        print("\n[dry-run] Sampling points:")
        for lat, lon, label in deduped:
            print(f"  {lat:.5f}, {lon:.5f}  — {label}")
        conn.close()
        return

    # ---- fetch stores ----
    before_count = conn.execute("SELECT COUNT(*) FROM stores").fetchone()[0]
    print(f"\nFetching stores for {len(deduped)} sampling points "
          f"(currently {before_count} stores in DB)...\n")

    errors = 0
    with tqdm(deduped, unit="point") as pbar:
        for lat, lon, label in pbar:
            pbar.set_postfix(label=label[:25], errors=errors)
            url = (
                f"{BASE}/GetStoresForProductsByLatLon"
                f"?lat={lat}&lon={lon}&buffer={BUFFER}"
                f"&csvprodids={csv_prods}&OrderBy=price"
            )
            try:
                fetched_at = datetime.now(timezone.utc).isoformat()
                root = fetch_xml(url)
                stores, _ = parse_stores_and_prices(root, fetched_at)
                for s in stores:
                    upsert_store(conn, s["id"], s["name"], s["addr"],
                                 s["lat"], s["lon"], s["uat_id"],
                                 s["network_id"], s["zipcode"])
                conn.commit()
            except Exception as exc:
                tqdm.write(f"  ERROR at ({lat:.4f}, {lon:.4f}) {label}: {exc}")
                errors += 1
            time.sleep(SLEEP)

    after_count = conn.execute("SELECT COUNT(*) FROM stores").fetchone()[0]
    print(f"\nDone.")
    print(f"  Sampling points queried : {len(deduped)}")
    print(f"  Errors                  : {errors}")
    print(f"  Stores before           : {before_count}")
    print(f"  Stores after            : {after_count}  (+{after_count - before_count} new)")
    conn.close()


if __name__ == "__main__":
    main()
