"""
Fetch current fuel prices for all UATs.
Run daily. Writes to data/prices.db.
Requires UAT data — run fetch_reference.py (or fetch_gas_reference.py) first.

Options:
  --limit-uats N   process only the first N UATs (for testing)
  --fresh          ignore any saved checkpoint and start a clean run
"""
import argparse
import json
import os
import time
from datetime import datetime, timezone

import requests
from tqdm import tqdm

from api import GAS_BASE, fetch_xml, parse_gas_items
from db import init_db, insert_gas_price, upsert_gas_station

SLEEP_BETWEEN = 0.3  # seconds between requests
# API only accepts one product ID per request (CSV returns 500)
FUEL_IDS = [11, 12, 21, 22, 31, 41]
CHECKPOINT_PATH = "data/gas_checkpoint.json"


def _load_checkpoint(path):
    if os.path.exists(path):
        with open(path) as f:
            data = json.load(f)
        data["done"] = set(data["done"])
        return data
    return None


def _save_checkpoint(path, fetched_at, done):
    with open(path, "w") as f:
        json.dump({"fetched_at": fetched_at, "done": sorted(done)}, f)


def _clear_checkpoint(path):
    if os.path.exists(path):
        os.remove(path)


def main(db_path="data/prices.db", limit_uats=None, fresh=False):
    conn = init_db(db_path)

    cp = None if fresh else _load_checkpoint(CHECKPOINT_PATH)
    if cp:
        fetched_at = cp["fetched_at"]
        done = cp["done"]
        tqdm.write(f"Resuming from checkpoint ({len(done)} work units already done)  fetched_at={fetched_at}")
    else:
        fetched_at = datetime.now(timezone.utc).isoformat()
        done = set()

    uats = conn.execute(
        "SELECT id, name, center_lat, center_lon FROM uats"
    ).fetchall()

    if not uats:
        tqdm.write("No UATs found — run fetch_reference.py first.")
        return

    if limit_uats:
        uats = uats[:limit_uats]

    tqdm.write(
        f"Fetching gas prices: {len(uats)} UATs  fetched_at={fetched_at}"
    )

    total_prices = 0
    with tqdm(uats, desc="UATs", unit="uat") as uat_bar:
        for uat_id, uat_name, lat, lon in uat_bar:
            uat_bar.set_description(uat_name[:30])
            all_stations = {}
            uat_prices = []

            for fuel_id in FUEL_IDS:
                key = f"{uat_id}:{fuel_id}"
                if key in done:
                    continue

                url = (
                    f"{GAS_BASE}/GetGasItemsByUat"
                    f"?UatId={uat_id}&CSVGasCatalogProductIds={fuel_id}&OrderBy=dist"
                )
                try:
                    root = fetch_xml(url)
                except requests.HTTPError as exc:
                    # API returns 500 when no stations carry this fuel in this UAT
                    tqdm.write(f"  {uat_name} fuel={fuel_id}: skipped ({exc.response.status_code})")
                    time.sleep(SLEEP_BETWEEN)
                    done.add(key)
                    _save_checkpoint(CHECKPOINT_PATH, fetched_at, done)
                    continue

                stations, prices = parse_gas_items(root, fetched_at)
                for s in stations:
                    all_stations[s["id"]] = s
                uat_prices.extend(prices)

                done.add(key)
                _save_checkpoint(CHECKPOINT_PATH, fetched_at, done)

                time.sleep(SLEEP_BETWEEN)

            for s in all_stations.values():
                upsert_gas_station(
                    conn, s["id"], s["name"], s["addr"],
                    s["lat"], s["lon"], s["uat_id"],
                    s["network_id"], s["zipcode"], s["update_date"],
                )
            for p in uat_prices:
                insert_gas_price(
                    conn,
                    p["product_id"], p["station_id"], p["price"],
                    p["price_date"], p["fetched_at"],
                )
            conn.commit()
            total_prices += len(uat_prices)
            uat_bar.set_postfix(stations=len(all_stations), total=total_prices)
            tqdm.write(
                f"  {uat_name}: {len(all_stations)} stations, {len(uat_prices)} prices"
            )

    _clear_checkpoint(CHECKPOINT_PATH)
    tqdm.write(f"\nDone. {total_prices} gas price records inserted.")
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("db", nargs="?", default="data/prices.db",
                        help="path to SQLite DB (default: data/prices.db)")
    parser.add_argument("--limit-uats", type=int, default=None,
                        help="process only the first N UATs")
    parser.add_argument("--fresh", action="store_true",
                        help="ignore saved checkpoint and start a clean run")
    args = parser.parse_args()
    main(args.db, args.limit_uats, args.fresh)
