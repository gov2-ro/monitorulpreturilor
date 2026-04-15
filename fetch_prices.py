"""
Fetch current prices for all stores × product batches.
Run daily. Writes to data/prices.db.
Requires reference data (run fetch_reference.py) and store discovery
(run discover_stores.py + update_store_populations.py) first.

Each store is queried from its own lat/lon, guaranteeing it always appears in
the API response (unlike the legacy UAT-centroid approach).

Ordering modes (--order):
  population  [default] — stores sorted by surrounding_population DESC; fetches
                          the most commercially dense areas first.
  geographic            — stores spread across Romania in grid Z-order so every
                          region gets covered before any area is revisited.

Options:
  --order population|geographic
  --limit-stores N    process only the first N stores
  --limit-products N  use only the first N products per store
  --fresh             ignore saved checkpoint and start a clean run
"""

import argparse
import json
import math
import os
import time
from datetime import datetime, timezone

from tqdm import tqdm

from api import BASE, fetch_xml, parse_stores_and_prices
from db import init_db, insert_price, upsert_store, start_run, finish_run

BATCH_SIZE = 30
SLEEP_BETWEEN = 0.5
BUFFER_M = 5000
CHECKPOINT_PATH = "data/prices_checkpoint.json"

# Romania bounding box for geographic ordering
_RO_LAT_MIN, _RO_LAT_MAX = 43.6, 48.3
_RO_LON_MIN, _RO_LON_MAX = 20.3, 29.7
_GRID_DEG = 0.45   # ~50 km per cell


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _batches(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i: i + n]


def _geo_sort_key(lat, lon):
    """Z-order (row-major snake) key for geographic spread ordering."""
    row = int((lat - _RO_LAT_MIN) / _GRID_DEG)
    col = int((lon - _RO_LON_MIN) / _GRID_DEG)
    # Snake: reverse column order on odd rows
    col_key = col if row % 2 == 0 else 1000 - col
    return (row, col_key)


def _order_stores(stores, mode):
    """
    stores: list of (id, name, lat, lon, surrounding_population)
    Returns reordered list.
    """
    if mode == "population":
        return sorted(stores, key=lambda s: (-(s[4] or 0), s[0]))
    elif mode == "geographic":
        return sorted(
            stores,
            key=lambda s: (*_geo_sort_key(s[2], s[3]), -(s[4] or 0))
        )
    else:
        raise ValueError(f"Unknown order mode: {mode!r}")


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------

def _load_checkpoint(path):
    if os.path.exists(path):
        with open(path) as f:
            data = json.load(f)
        data["done"] = set(data["done"])
        return data
    return None


def _save_checkpoint(path, fetched_at, done):
    with open(path, "w") as f:
        json.dump({"fetched_at": fetched_at, "status": "in_progress",
                   "done": sorted(done)}, f)


def _finish_checkpoint(path, fetched_at, done):
    with open(path, "w") as f:
        json.dump({"fetched_at": fetched_at, "status": "completed",
                   "done": sorted(done)}, f)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(db_path="data/prices.db", order="population", limit_stores=None,
         limit_products=None, fresh=False):
    conn = init_db(db_path)

    cp = None if fresh else _load_checkpoint(CHECKPOINT_PATH)
    if cp:
        today = datetime.now(timezone.utc).date()
        cp_date = datetime.fromisoformat(cp["fetched_at"]).date()
        status = cp.get("status", "in_progress")
        if status == "completed" and cp_date == today:
            tqdm.write(f"Already completed today ({cp['fetched_at']}). Nothing to do.")
            conn.close()
            return
        elif status == "completed" and cp_date != today:
            tqdm.write(f"Previous run completed on {cp_date}, starting fresh for today.")
            cp = None

    if cp:
        fetched_at = cp["fetched_at"]
        done = cp["done"]
        tqdm.write(f"Resuming checkpoint ({len(done)} work units done)  fetched_at={fetched_at}")
    else:
        fetched_at = datetime.now(timezone.utc).isoformat()
        done = set()

    stores_raw = conn.execute(
        "SELECT id, name, lat, lon, surrounding_population FROM stores "
        "WHERE lat IS NOT NULL AND lon IS NOT NULL"
    ).fetchall()
    prod_ids = [r[0] for r in conn.execute("SELECT id FROM products")]

    if not stores_raw:
        tqdm.write("No stores found — run discover_stores.py first.")
        conn.close()
        return
    if not prod_ids:
        tqdm.write("No products found — run fetch_reference.py first.")
        conn.close()
        return

    stores = _order_stores(stores_raw, order)

    if limit_stores:
        stores = stores[:limit_stores]
    if limit_products:
        prod_ids = prod_ids[:limit_products]

    batches_list = list(_batches(prod_ids, BATCH_SIZE))
    n_batches = len(batches_list)

    tqdm.write(
        f"Fetching prices: {len(stores)} stores × {len(prod_ids)} products "
        f"({n_batches} batch{'es' if n_batches != 1 else ''}/store)  "
        f"order={order}  fetched_at={fetched_at}"
    )

    run_id = start_run(conn, "fetch_prices", fetched_at)
    total_prices = 0
    stores_done = 0

    try:
        with tqdm(stores, desc="stores", unit="store") as store_bar:
            for store_id, name, lat, lon, pop in store_bar:
                store_bar.set_description(name[:30])

                store_prices = 0
                with tqdm(batches_list, desc="  batches", unit="batch", leave=False) as batch_bar:
                    for i, batch in enumerate(batch_bar):
                        key = f"{store_id}:{i}"
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
                        result_stores, prices = parse_stores_and_prices(root, fetched_at)

                        for s in result_stores:
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
                        store_prices += len(prices)
                        batch_bar.set_postfix(prices=store_prices)

                        done.add(key)
                        _save_checkpoint(CHECKPOINT_PATH, fetched_at, done)
                        time.sleep(SLEEP_BETWEEN)

                tqdm.write(f"  {name}: {store_prices} price records")
                total_prices += store_prices
                stores_done += 1
                store_bar.set_postfix(total_prices=total_prices)

        _finish_checkpoint(CHECKPOINT_PATH, fetched_at, done)
        finish_run(conn, run_id, "completed", stores_done, total_prices)
        tqdm.write(f"\nDone. {total_prices} price records inserted.")

    except KeyboardInterrupt:
        finish_run(conn, run_id, "interrupted", stores_done, total_prices)
        tqdm.write(f"\nInterrupted. {total_prices} price records written so far.")
        raise
    except Exception as exc:
        finish_run(conn, run_id, "error", stores_done, total_prices, notes=str(exc))
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("db", nargs="?", default="data/prices.db")
    parser.add_argument("--order", choices=["population", "geographic"],
                        default="population",
                        help="store ordering mode (default: population)")
    parser.add_argument("--limit-stores", type=int, default=None,
                        help="process only the first N stores")
    parser.add_argument("--limit-products", type=int, default=None,
                        help="use only the first N products per store")
    parser.add_argument("--fresh", action="store_true",
                        help="ignore saved checkpoint and start a clean run")
    args = parser.parse_args()
    main(args.db, args.order, args.limit_stores, args.limit_products, args.fresh)
