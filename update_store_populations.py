"""
Compute and store a surrounding_population estimate for every store.

For each store, sum the populations of all Romanian localities whose centroid
falls within --radius km (default 10 km). This captures the city + immediate
suburbs a store serves, and is used to prioritise stores in fetch_prices.py.

Source: data/reference/populatie romania siruta coords.csv
        (3 180 localities, all with lat/lon and population)

Run once after discover_stores.py, or re-run whenever the stores table grows.

Usage:
  python update_store_populations.py [--radius 10] [--debug]
"""

import argparse
import csv
import math
from pathlib import Path

from tqdm import tqdm

from db import init_db

POP_CSV = Path("data/reference/populatie romania siruta coords.csv")


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


def load_localities(csv_path):
    places = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                lat, lon = float(r["lat"]), float(r["long"])
                pop = int(r["populatie"]) if r["populatie"] else 0
            except (ValueError, TypeError):
                continue
            if pop > 0:
                places.append((lat, lon, pop))
    return places


def main(db_path="data/prices.db", radius_km=10.0, debug=False):
    conn = init_db(db_path)

    stores = conn.execute(
        "SELECT id, name, lat, lon FROM stores WHERE lat IS NOT NULL AND lon IS NOT NULL"
    ).fetchall()

    localities = load_localities(POP_CSV)
    tqdm.write(f"Loaded {len(localities)} localities, {len(stores)} stores — radius={radius_km}km")

    updated = 0
    with tqdm(stores, desc="computing", unit="store") as bar:
        for store_id, name, slat, slon in bar:
            pop_sum = sum(
                pop for (llat, llon, pop) in localities
                if haversine_km(slat, slon, llat, llon) <= radius_km
            )
            conn.execute(
                "UPDATE stores SET surrounding_population = ? WHERE id = ?",
                (pop_sum, store_id),
            )
            updated += 1
            if debug:
                tqdm.write(f"[debug] {name}: {pop_sum:,}")

    conn.commit()
    conn.close()
    tqdm.write(f"Done. {updated} stores updated.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("db", nargs="?", default="data/prices.db")
    parser.add_argument("--radius", type=float, default=10.0,
                        help="radius in km for population sum (default: 10)")
    parser.add_argument("--debug", action="store_true",
                        help="print per-store population values")
    args = parser.parse_args()
    main(args.db, args.radius, args.debug)
