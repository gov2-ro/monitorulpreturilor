"""
Fetch current prices for all stores × product batches.
Run daily. Writes to data/prices.db.
Requires reference data (run fetch_reference.py) and store discovery
(run discover_stores.py + update_store_populations.py) first.

Spatial clustering (default): stores within 5 km are grouped and only one
anchor per cluster is queried, since the API returns prices for all stores
in its 5 km buffer.  This reduces anchors from ~3800 to ~680 (~82%).
Combined with 200-product batches, total requests drop ~95%.

Ordering modes (--order):
  population  [default] — anchors sorted by surrounding_population DESC
  geographic            — anchors spread across Romania in grid Z-order

Options:
  --order population|geographic
  --limit-stores N    process only the first N stores (before clustering)
  --limit-products N  use only the first N products per store
  --store-ids-file PATH   newline-separated store IDs; overrides --order/--limit-stores
  --product-ids-file PATH newline-separated product IDs; overrides --limit-products
  --no-cluster        disable spatial clustering (query every store individually)
  --fresh             ignore saved checkpoint and start a clean run
  --resume            continue a completed run (e.g. after adding new stores);
                      already-processed store×batch keys are skipped
"""

import argparse
import json
import math
import os
import time
from datetime import datetime, timezone

import requests
from tqdm import tqdm

from api import BASE, fetch_xml, parse_stores_and_prices
from db import init_db, insert_price, upsert_store, start_run, finish_run

# BATCH_SIZE = 30
BATCH_SIZE = 200
# SLEEP_BETWEEN = 0.5
SLEEP_BETWEEN = 0.15
BUFFER_M = 5000

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


def _haversine_m(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres between two points."""
    R = 6_371_000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def _cluster_anchors(stores, radius_m=5000):
    """Greedy set-cover: pick anchors so every store is within radius_m of some anchor.

    Returns the subset of stores chosen as anchors (same tuple format).
    """
    n = len(stores)
    # Pre-compute neighbor lists (indices within radius)
    neighbors = [[] for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            # Quick lat/lon pre-filter (~0.05° ≈ 5.5 km)
            if (abs(stores[i][2] - stores[j][2]) > 0.05
                    or abs(stores[i][3] - stores[j][3]) > 0.07):
                continue
            d = _haversine_m(stores[i][2], stores[i][3],
                             stores[j][2], stores[j][3])
            if d <= radius_m:
                neighbors[i].append(j)
                neighbors[j].append(i)

    uncovered = set(range(n))
    anchors = []
    while uncovered:
        # Pick store covering the most uncovered neighbors
        best = max(uncovered,
                   key=lambda i: sum(1 for j in neighbors[i] if j in uncovered))
        anchors.append(stores[best])
        uncovered.discard(best)
        for j in neighbors[best]:
            uncovered.discard(j)
    return anchors


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

def _load_ids_file(path):
    """Return a list of integer IDs from a newline-separated file."""
    with open(path) as f:
        return [int(line.strip()) for line in f if line.strip()]


def main(db_path="data/prices.db", order="population", limit_stores=None,
         limit_products=None, store_ids_file=None, product_ids_file=None,
         fresh=False, resume=False, max_runtime=0, no_cluster=False):
    if store_ids_file and (limit_stores is not None):
        raise ValueError("--store-ids-file and --limit-stores are mutually exclusive")
    if product_ids_file and (limit_products is not None):
        raise ValueError("--product-ids-file and --limit-products are mutually exclusive")

    checkpoint_path = db_path.replace(".db", "_checkpoint.json")
    conn = init_db(db_path)

    cp = None if fresh else _load_checkpoint(checkpoint_path)
    if cp:
        today = datetime.now(timezone.utc).date()
        cp_date = datetime.fromisoformat(cp["fetched_at"]).date()
        status = cp.get("status", "in_progress")
        if status == "completed" and cp_date == today:
            if not resume:
                tqdm.write(f"Already completed today ({cp['fetched_at']}). Nothing to do.")
                tqdm.write("  Use --resume to process any newly added stores.")
                conn.close()
                return
            tqdm.write(
                f"Resuming completed run ({cp['fetched_at']}, {len(cp['done'])} work units done). "
                f"New stores will be fetched; existing keys skipped."
            )
            cp["status"] = "in_progress"
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

    if store_ids_file:
        allowed = set(_load_ids_file(store_ids_file))
        stores = [s for s in stores_raw if s[0] in allowed]
        tqdm.write(f"Store filter: {len(stores)} stores from {store_ids_file}")
    else:
        stores = _order_stores(stores_raw, order)
        if limit_stores:
            stores = stores[:limit_stores]

    # Spatial clustering: reduce anchors so every store is within 5 km of one
    if not no_cluster and not store_ids_file:
        n_before = len(stores)
        tqdm.write(f"Clustering {n_before} stores (radius={BUFFER_M}m)...")
        stores = _cluster_anchors(stores, radius_m=BUFFER_M)
        stores = _order_stores(stores, order)
        tqdm.write(
            f"Clustered {n_before} stores → {len(stores)} anchors "
            f"({100 * (1 - len(stores) / n_before):.0f}% reduction)"
        )

    if product_ids_file:
        allowed_prods = set(_load_ids_file(product_ids_file))
        prod_ids = [p for p in prod_ids if p in allowed_prods]
        tqdm.write(f"Product filter: {len(prod_ids)} products from {product_ids_file}")
    elif limit_products:
        prod_ids = prod_ids[:limit_products]

    batches_list = list(_batches(prod_ids, BATCH_SIZE))
    n_batches = len(batches_list)

    # Pre-filter stores that are fully done (all batches in checkpoint)
    if done:
        stores_skipped = [(sid, name, lat, lon, pop)
                          for sid, name, lat, lon, pop in stores
                          if all(f"{sid}:{i}" in done for i in range(n_batches))]
        stores = [(sid, name, lat, lon, pop)
                  for sid, name, lat, lon, pop in stores
                  if not all(f"{sid}:{i}" in done for i in range(n_batches))]
        if stores_skipped:
            tqdm.write(f"Skipping {len(stores_skipped)} fully-done stores from checkpoint.")

    tqdm.write(
        f"Fetching prices: {len(stores)} stores × {len(prod_ids)} products "
        f"({n_batches} batch{'es' if n_batches != 1 else ''}/store)  "
        f"order={order}  fetched_at={fetched_at}"
    )

    run_id = start_run(conn, "fetch_prices", fetched_at)
    total_prices = 0
    stores_done = 0
    t_start = time.monotonic()

    try:
        with tqdm(stores, desc="stores", unit="store") as store_bar:
            for store_id, name, lat, lon, pop in store_bar:
                if max_runtime and (time.monotonic() - t_start) >= max_runtime:
                    elapsed = int(time.monotonic() - t_start)
                    tqdm.write(f"\nTime limit reached ({elapsed}s / {max_runtime}s). "
                               f"Checkpoint saved — resume with next run.")
                    finish_run(conn, run_id, "interrupted", stores_done, total_prices)
                    return  # finally block closes conn
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
                        try:
                            root = fetch_xml(url)
                        except requests.exceptions.RequestException as exc:
                            tqdm.write(
                                f"  WARN: skipping {name} batch {i} after all retries failed: {exc}"
                            )
                            time.sleep(SLEEP_BETWEEN)
                            continue
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
                        _save_checkpoint(checkpoint_path, fetched_at, done)
                        time.sleep(SLEEP_BETWEEN)

                tqdm.write(f"  {name}: {store_prices} price records")
                total_prices += store_prices
                stores_done += 1
                store_bar.set_postfix(total_prices=total_prices)

        _finish_checkpoint(checkpoint_path, fetched_at, done)
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
    parser.add_argument("--store-ids-file", default=None,
                        help="newline-separated store IDs to fetch (overrides --order/--limit-stores)")
    parser.add_argument("--product-ids-file", default=None,
                        help="newline-separated product IDs to fetch (overrides --limit-products)")
    parser.add_argument("--fresh", action="store_true",
                        help="ignore saved checkpoint and start a clean run")
    parser.add_argument("--resume", action="store_true",
                        help="continue a completed today run (process only new stores/batches)")
    parser.add_argument("--max-runtime", type=int, default=0, metavar="SECONDS",
                        help="stop gracefully after N seconds (checkpoint saved; resume on next run)")
    parser.add_argument("--no-cluster", action="store_true",
                        help="disable spatial clustering (query every store as its own anchor)")
    args = parser.parse_args()
    main(args.db, args.order, args.limit_stores, args.limit_products,
         args.store_ids_file, args.product_ids_file, args.fresh, args.resume,
         args.max_runtime, args.no_cluster)
