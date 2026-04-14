"""
Fetch current prices for all UAT × product batches.
Run daily. Writes to prices.db. Requires reference data to exist (run
fetch_reference.py first).
"""
import sys
import time
from datetime import datetime, timezone

from api import BASE, fetch_xml, parse_stores_and_prices
from db import init_db, insert_price, upsert_store

BATCH_SIZE = 30
SLEEP_BETWEEN = 0.5  # seconds between requests
# API silently returns 0 results for buffer > ~5000 m; 50 stores max per call
BUFFER_M = 5000


def _batches(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


def main(db_path="prices.db"):
    conn = init_db(db_path)
    fetched_at = datetime.now(timezone.utc).isoformat()

    uats = conn.execute(
        "SELECT id, name, center_lat, center_lon FROM uats"
    ).fetchall()
    prod_ids = [row[0] for row in conn.execute("SELECT id FROM products")]

    if not uats:
        print("No UATs found – run fetch_reference.py first.")
        return
    if not prod_ids:
        print("No products found – run fetch_reference.py first.")
        return

    n_batches = (len(prod_ids) + BATCH_SIZE - 1) // BATCH_SIZE
    print(
        f"Fetching prices: {len(uats)} UATs × {len(prod_ids)} products "
        f"({n_batches} batches/UAT)  fetched_at={fetched_at}"
    )

    total_prices = 0
    for uat_idx, (uat_id, uat_name, lat, lon) in enumerate(uats, 1):
        if lat is None or lon is None:
            print(f"  [{uat_idx}/{len(uats)}] {uat_name}: skipped (no centroid)")
            continue

        print(f"  [{uat_idx}/{len(uats)}] {uat_name}...", flush=True)
        uat_prices = 0

        for batch in _batches(prod_ids, BATCH_SIZE):
            csv_ids = ",".join(str(i) for i in batch)
            url = (
                f"{BASE}/GetStoresForProductsByLatLon"
                f"?lat={lat}&lon={lon}&buffer={BUFFER_M}"
                f"&csvprodids={csv_ids}&OrderBy=price"
            )
            root = fetch_xml(url)
            stores, prices = parse_stores_and_prices(root, fetched_at)

            for s in stores:
                upsert_store(
                    conn, s["id"], s["name"], s["addr"],
                    s["lat"], s["lon"], s["uat_id"],
                    s["network_id"], s["zipcode"],
                )
            for p in prices:
                insert_price(
                    conn,
                    p["product_id"], p["store_id"], p["price"],
                    p["price_date"], p["promo"], p["brand"], p["unit"],
                    p["retail_categ_id"], p["retail_categ_name"],
                    p["fetched_at"],
                )
            conn.commit()
            uat_prices += len(prices)
            time.sleep(SLEEP_BETWEEN)

        print(f"    {uat_prices} price records")
        total_prices += uat_prices

    print(f"\nDone. {total_prices} price records inserted.")
    conn.close()


if __name__ == "__main__":
    db_path = sys.argv[1] if len(sys.argv) > 1 else "prices.db"
    main(db_path)
