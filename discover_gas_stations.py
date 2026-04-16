"""
Discover gas stations by probing GetGasItemsByLatLon from populated locality centroids.

Uses the same population-based probe strategy as discover_stores.py. One fuel type
is requested per probe (Motorină standard, ID=21) — enough to find all stations in
the 5 km buffer. Discovered stations and their UAT IDs are upserted into the DB;
fetch_gas_prices.py then covers all newly added UATs on the next daily run.

Source: data/reference/populatie romania siruta coords.csv

Usage:
  python discover_gas_stations.py [--min-pop 2500] [--limit N] [--debug] [--dry-run] [--fresh]
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

from api import GAS_BASE, fetch_xml, parse_gas_items
from db import init_db, upsert_gas_station, ensure_uat

# --- config ------------------------------------------------------------------

BUFFER_M       = 5000   # API buffer radius in metres
DISCOVERY_FUEL = 21     # Motorină standard — most widespread; used for station discovery
DEDUP_RADIUS_KM = 4.0  # drop a probe point if a kept point is within this distance
SLEEP          = 0.3    # seconds between API calls

POP_CSV         = Path("data/reference/populatie romania siruta coords.csv")
CHECKPOINT_PATH = "data/discover_gas_checkpoint.json"


# --- geometry ----------------------------------------------------------------

def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


def deduplicate_points(points, radius_km, debug=False):
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


def _save_checkpoint(path, started_at, done, stations_seen):
    with open(path, "w") as f:
        json.dump({
            "started_at": started_at,
            "status": "in_progress",
            "done": sorted(done),
            "stations_seen": stations_seen,
        }, f)


def _finish_checkpoint(path, started_at, done, stations_seen):
    with open(path, "w") as f:
        json.dump({
            "started_at": started_at,
            "status": "completed",
            "done": sorted(done),
            "stations_seen": stations_seen,
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
        stations_seen = cp.get("stations_seen", 0)
        tqdm.write(f"Resuming: {len(done)}/{len(points)} probes already done")
    else:
        started_at = datetime.now(timezone.utc).isoformat()
        done = set()
        stations_seen = 0

    stations_before = conn.execute("SELECT COUNT(*) FROM gas_stations").fetchone()[0]
    uats_before = conn.execute("SELECT COUNT(*) FROM uats").fetchone()[0]
    probes = errors = 0

    try:
        with tqdm(points, unit="pt") as bar:
            for name, lat, lon, pop in bar:
                key = f"{lat:.5f},{lon:.5f}"
                bar.set_description(name[:28])
                bar.set_postfix(pop=f"{pop:,}", seen=stations_seen, err=errors)

                if key in done:
                    continue

                url = (
                    f"{GAS_BASE}/GetGasItemsByLatLon"
                    f"?lat={lat}&lon={lon}&buffer={BUFFER_M}"
                    f"&CSVGasCatalogProductIds={DISCOVERY_FUEL}&OrderBy=dist"
                )
                if args.debug:
                    tqdm.write(f"[debug] GET {url}")

                try:
                    fetched_at = datetime.now(timezone.utc).isoformat()
                    root = fetch_xml(url)
                    stations, _ = parse_gas_items(root, fetched_at)
                    for s in stations:
                        if s.get("uat_id"):
                            ensure_uat(conn, s["uat_id"])
                        upsert_gas_station(conn, s["id"], s["name"], s["addr"],
                                           s["lat"], s["lon"], s["uat_id"],
                                           s["network_id"], s["zipcode"],
                                           s["update_date"])
                    if stations:
                        conn.commit()
                    stations_seen += len(stations)
                    probes += 1
                    if args.debug and stations:
                        tqdm.write(f"[debug] {name}: {len(stations)} station(s)")
                except Exception as exc:
                    tqdm.write(f"  ERROR ({name}): {exc}")
                    errors += 1

                done.add(key)
                _save_checkpoint(CHECKPOINT_PATH, started_at, done, stations_seen)
                time.sleep(SLEEP)

        _finish_checkpoint(CHECKPOINT_PATH, started_at, done, stations_seen)

    except KeyboardInterrupt:
        tqdm.write(f"\nInterrupted after {probes} probes — checkpoint saved.")
        conn.close()
        raise
    finally:
        stations_after = conn.execute("SELECT COUNT(*) FROM gas_stations").fetchone()[0]
        uats_after = conn.execute("SELECT COUNT(*) FROM uats").fetchone()[0]
        tqdm.write(
            f"\nDone. {probes} probes, {errors} errors.\n"
            f"Gas stations: {stations_before} → {stations_after} "
            f"(+{stations_after - stations_before} new)\n"
            f"UATs: {uats_before} → {uats_after} "
            f"(+{uats_after - uats_before} new)"
        )
        conn.close()


if __name__ == "__main__":
    main()
