"""
Fetch current fuel prices for all UATs.
Run daily. Writes to data/prices.db.
Requires UAT data — run fetch_reference.py (or fetch_gas_reference.py) first.

Options:
  --limit-uats N   process only the first N UATs (for testing)
"""
import argparse
import time
from datetime import datetime, timezone

import requests
from tqdm import tqdm

from api import GAS_BASE, fetch_xml, parse_gas_items
from db import init_db, insert_gas_price, upsert_gas_station

SLEEP_BETWEEN = 0.3  # seconds between requests
# API only accepts one product ID per request (CSV returns 500)
FUEL_IDS = [11, 12, 21, 22, 31, 41]


def main(db_path="data/prices.db", limit_uats=None):
    conn = init_db(db_path)
    fetched_at = datetime.now(timezone.utc).isoformat()

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
                    continue

                stations, prices = parse_gas_items(root, fetched_at)
                for s in stations:
                    all_stations[s["id"]] = s
                uat_prices.extend(prices)
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

    tqdm.write(f"\nDone. {total_prices} gas price records inserted.")
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("db", nargs="?", default="data/prices.db",
                        help="path to SQLite DB (default: data/prices.db)")
    parser.add_argument("--limit-uats", type=int, default=None,
                        help="process only the first N UATs")
    args = parser.parse_args()
    main(args.db, args.limit_uats)
