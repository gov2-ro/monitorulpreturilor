"""
Fetch current prices for all UAT × product batches.
Run daily. Writes to data/prices.db.
Requires reference data (run fetch_reference.py first).

Options:
  --limit-uats N      process only the first N UATs
  --limit-products N  use only the first N products per UAT
  --fresh             ignore any saved checkpoint and start a clean run
"""
import argparse
import json
import os
import time
from datetime import datetime, timezone

from tqdm import tqdm

from api import BASE, fetch_xml, parse_stores_and_prices
from db import init_db, insert_price, upsert_store

BATCH_SIZE = 30
SLEEP_BETWEEN = 0.5  # seconds between requests
# API silently returns 0 results for buffer > ~5000 m; 50 stores max per call
BUFFER_M = 5000
CHECKPOINT_PATH = "data/retail_checkpoint.json"


def _batches(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


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


def main(db_path="data/prices.db", limit_uats=None, limit_products=None, fresh=False):
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
    prod_ids = [row[0] for row in conn.execute("SELECT id FROM products")]

    if not uats:
        tqdm.write("No UATs found – run fetch_reference.py first.")
        return
    if not prod_ids:
        tqdm.write("No products found – run fetch_reference.py first.")
        return

    if limit_uats:
        uats = uats[:limit_uats]
    if limit_products:
        prod_ids = prod_ids[:limit_products]

    n_batches = (len(prod_ids) + BATCH_SIZE - 1) // BATCH_SIZE
    tqdm.write(
        f"Fetching prices: {len(uats)} UATs × {len(prod_ids)} products "
        f"({n_batches} batch{'es' if n_batches != 1 else ''}/UAT)  fetched_at={fetched_at}"
    )

    total_prices = 0
    batches_list = list(_batches(prod_ids, BATCH_SIZE))

    with tqdm(uats, desc="UATs", unit="uat") as uat_bar:
        for uat_id, uat_name, lat, lon in uat_bar:
            uat_bar.set_description(uat_name[:30])
            if lat is None or lon is None:
                tqdm.write(f"  {uat_name}: skipped (no centroid)")
                continue

            uat_prices = 0
            with tqdm(batches_list, desc="  batches", unit="batch", leave=False) as batch_bar:
                for i, batch in enumerate(batch_bar):
                    key = f"{uat_id}:{i}"
                    if key in done:
                        batch_bar.set_postfix(status="resumed")
                        continue

                    csv_ids = ",".join(str(p) for p in batch)
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
                    batch_bar.set_postfix(prices=uat_prices)

                    done.add(key)
                    _save_checkpoint(CHECKPOINT_PATH, fetched_at, done)

                    time.sleep(SLEEP_BETWEEN)

            tqdm.write(f"  {uat_name}: {uat_prices} price records")
            total_prices += uat_prices
            uat_bar.set_postfix(total_prices=total_prices)

    _clear_checkpoint(CHECKPOINT_PATH)
    tqdm.write(f"\nDone. {total_prices} price records inserted.")
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("db", nargs="?", default="data/prices.db",
                        help="path to SQLite DB (default: data/prices.db)")
    parser.add_argument("--limit-uats", type=int, default=None,
                        help="process only the first N UATs")
    parser.add_argument("--limit-products", type=int, default=None,
                        help="use only the first N products per UAT")
    parser.add_argument("--fresh", action="store_true",
                        help="ignore saved checkpoint and start a clean run")
    args = parser.parse_args()
    main(args.db, args.limit_uats, args.limit_products, args.fresh)
