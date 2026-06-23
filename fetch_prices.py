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
  stale       [default] — stalest anchors first; tiebreak by surrounding_population DESC
  population            — anchors sorted by surrounding_population DESC
  geographic            — anchors spread across Romania in grid Z-order

Options:
  --order population|geographic|stale
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
import signal
import sys
import time
from datetime import date, datetime, timedelta, timezone

import requests
from tqdm import tqdm

from api import BASE, fetch_xml, parse_stores_and_prices
from db import (init_db, insert_price, upsert_store, start_run, finish_run,
                abandon_stale_runs, deactivate_stale_stores, propagate_last_checked,
                propagate_network_prices, update_store_tiers, backfill_store_network_ids,
                backfill_store_network_from_logo, check_store_network_conflicts)

# BATCH_SIZE = 30
BATCH_SIZE = 200
# SLEEP_BETWEEN = 0.5
SLEEP_BETWEEN = 0.05   # was 0.15; lowered 2026-06-02 to cut ~3h/pass of pure sleep. Watch for HTTP 429.
BUFFER_M = 5000
# Adaptive cluster split: any cluster with more than MAX_STORES_PER_CLUSTER
# members is re-clustered at half the radius (down to MIN_CLUSTER_RADIUS_M).
# The API caps each response at 50 stores, so oversize clusters silently
# truncate. Without splitting, dense-urban anchors miss 80%+ of their cluster.
MIN_CLUSTER_RADIUS_M = 1250
MAX_STORES_PER_CLUSTER = 50

# Canary networks: skip pure-uniform anchors once we've confirmed ≥ threshold stores
# unchanged this run. Threshold ≈ 20% of each chain's store count.
# network_id values match stores.network_id in the DB.
CANARY_THRESHOLDS = {
    "KAUFLAND":       36,   # 181 stores × 20%
    "4055329000008":  72,   # 361 stores (LIDL DISCOUNT SRL) × 20%
    "PENNY":          82,   # 410 stores × 20%
}

DEAD_ANCHOR_THRESHOLD = 3   # consecutive all-fail runs before skiplist
DEAD_ANCHOR_SKIP_DAYS = 7   # days to keep on skiplist before retry

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


def _greedy_set_cover(stores, radius_m):
    """Single-pass greedy set cover at a fixed radius.

    Returns (anchors, anchor_covers) where anchor_covers maps
    anchor store_id -> list of covered store_ids (including the anchor itself).
    """
    n = len(stores)
    neighbors = [[] for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            # Quick lat/lon pre-filter (~0.05° ≈ 5.5 km — sized for the
            # 5 km starting radius; harmless at smaller radii, just wider).
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
    anchor_covers = {}
    while uncovered:
        best = max(uncovered,
                   key=lambda i: sum(1 for j in neighbors[i] if j in uncovered))
        anchor_sid = stores[best][0]
        anchors.append(stores[best])
        anchor_covers[anchor_sid] = [stores[j][0] for j in neighbors[best]] + [anchor_sid]
        uncovered.discard(best)
        for j in neighbors[best]:
            uncovered.discard(j)
    return anchors, anchor_covers


def _cluster_anchors(stores, radius_m=BUFFER_M,
                     min_radius_m=MIN_CLUSTER_RADIUS_M,
                     max_per_cluster=MAX_STORES_PER_CLUSTER):
    """Greedy set-cover with adaptive radius split.

    Any cluster with >max_per_cluster members is recursively re-clustered
    at half the radius, bottoming out at min_radius_m. Rural anchors stay
    at radius_m; only dense-urban anchors split.

    Returns (anchors, anchor_covers, anchor_radius) where anchor_radius
    maps anchor store_id -> effective cluster radius in metres.
    """
    anchors, covers = _greedy_set_cover(stores, radius_m)
    refined_anchors = []
    refined_covers = {}
    refined_radius = {}
    can_split = radius_m // 2 >= min_radius_m
    for a in anchors:
        sid = a[0]
        covered_ids = covers[sid]
        if len(covered_ids) <= max_per_cluster or not can_split:
            refined_anchors.append(a)
            refined_covers[sid] = covered_ids
            refined_radius[sid] = radius_m
            continue
        cover_set = set(covered_ids)
        subset = [s for s in stores if s[0] in cover_set]
        sub_anchors, sub_covers, sub_radius = _cluster_anchors(
            subset, radius_m=radius_m // 2,
            min_radius_m=min_radius_m, max_per_cluster=max_per_cluster,
        )
        refined_anchors.extend(sub_anchors)
        refined_covers.update(sub_covers)
        refined_radius.update(sub_radius)
    return refined_anchors, refined_covers, refined_radius


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
    if mode in ("population", "stale"):
        # stale pre-cluster: use population so cluster representative quality is preserved;
        # actual stale sort is applied post-cluster once anchor_covers is known
        return sorted(stores, key=lambda s: (-(s[4] or 0), s[0]))
    elif mode == "geographic":
        return sorted(
            stores,
            key=lambda s: (*_geo_sort_key(s[2], s[3]), -(s[4] or 0))
        )
    else:
        raise ValueError(f"Unknown order mode: {mode!r}")


def _load_stale_map(conn, store_ids):
    """Return {store_id: days_since_last_checked} for the given store IDs.

    Absent entries mean never fetched (treated as infinity by callers).
    """
    if not store_ids:
        return {}
    placeholders = ",".join("?" * len(store_ids))
    rows = conn.execute(
        f"SELECT store_id, julianday('now') - julianday(date(MAX(last_checked_at)))"
        f" FROM prices_current WHERE store_id IN ({placeholders}) GROUP BY store_id",
        store_ids,
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def _stale_age(anchor_id, anchor_covers, stale_map):
    """Max staleness (days) across all stores in an anchor's cluster.

    Returns 9999 for any store absent from stale_map (never fetched).
    """
    covered = anchor_covers.get(anchor_id, [anchor_id])
    return max(stale_map.get(sid, 9999) for sid in covered)


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


def _save_checkpoint(path, fetched_at, done, product_ids=None, iso_week=None,
                     anchor_batch_counts=None, canary_seen=None, canary_changed=None,
                     weekly_tier_ids=None, weekly_store_ids=None, anchor_failures=None,
                     inflight_prod_ids=None):
    data = {"fetched_at": fetched_at, "status": "in_progress", "done": sorted(done)}
    if product_ids is not None:
        data["product_ids"] = product_ids
    if iso_week is not None:
        data["iso_week"] = iso_week
    if anchor_batch_counts is not None:
        data["anchor_batch_counts"] = anchor_batch_counts
    if canary_seen is not None:
        data["canary_seen"] = {k: sorted(v) for k, v in canary_seen.items()}
    if canary_changed is not None:
        data["canary_changed"] = sorted(canary_changed)
    if weekly_tier_ids is not None:
        data["weekly_tier_ids"] = sorted(weekly_tier_ids)
    if weekly_store_ids is not None:
        data["weekly_store_ids"] = sorted(weekly_store_ids)
    if anchor_failures:
        data["anchor_failures"] = anchor_failures
    if inflight_prod_ids:
        data["inflight_prod_ids"] = {str(k): v for k, v in inflight_prod_ids.items()}
    with open(path, "w") as f:
        json.dump(data, f)


def _finish_checkpoint(path, fetched_at, done, iso_week=None, anchor_batch_counts=None,
                       product_ids=None, canary_seen=None, canary_changed=None,
                       weekly_tier_ids=None, weekly_store_ids=None, anchor_failures=None,
                       inflight_prod_ids=None):
    data = {"fetched_at": fetched_at, "status": "completed", "done": sorted(done)}
    if iso_week is not None:
        data["iso_week"] = iso_week
    if anchor_batch_counts is not None:
        data["anchor_batch_counts"] = anchor_batch_counts
    if product_ids is not None:
        data["product_ids"] = product_ids
    if canary_seen is not None:
        data["canary_seen"] = {k: sorted(v) for k, v in canary_seen.items()}
    if canary_changed is not None:
        data["canary_changed"] = sorted(canary_changed)
    if weekly_tier_ids is not None:
        data["weekly_tier_ids"] = sorted(weekly_tier_ids)
    if weekly_store_ids is not None:
        data["weekly_store_ids"] = sorted(weekly_store_ids)
    if anchor_failures:
        data["anchor_failures"] = anchor_failures
    if inflight_prod_ids:
        data["inflight_prod_ids"] = {str(k): v for k, v in inflight_prod_ids.items()}
    with open(path, "w") as f:
        json.dump(data, f)


# ---------------------------------------------------------------------------
# Product ordering
# ---------------------------------------------------------------------------

def _order_products(conn, prod_ids, mode):
    """Reorder product IDs based on mode.

    db    — original insertion order (no-op)
    stale — never-fetched first, then sorted by oldest MAX(fetched_at) ascending
    """
    if mode == "db":
        return prod_ids
    # mode == "stale": sort by last fetch timestamp, NULLs (never fetched) first
    rows = conn.execute(
        "SELECT product_id, MAX(fetched_at) FROM prices GROUP BY product_id"
    ).fetchall()
    last_fetched = {r[0]: r[1] for r in rows}
    never = sum(1 for pid in prod_ids if pid not in last_fetched)
    ordered = sorted(prod_ids, key=lambda pid: (
        last_fetched.get(pid) is not None,   # False (0) = never fetched → sorts first
        last_fetched.get(pid) or ""
    ))
    tqdm.write(
        f"Products ordered by staleness: {never} never-fetched first, "
        f"then {len(prod_ids) - never} sorted by oldest fetch date."
    )
    return ordered


def _ghost_filter(conn, prod_ids, cp):
    """Remove products never seen in prices_current; skip on first run of ISO week.

    First run of each ISO week uses the full product list so newly added
    products are discovered.  All other runs skip ghost products (never
    returned a price) — ~17 % of the catalogue currently — cutting batch
    count proportionally.
    """
    today_week = datetime.now(timezone.utc).isocalendar()[1]
    cp_week = cp.get("iso_week") if cp else None
    if cp_week != today_week:
        tqdm.write(f"Ghost filter: new ISO week {today_week} — scanning all {len(prod_ids)} products.")
        return prod_ids, today_week
    seen = {r[0] for r in conn.execute("SELECT DISTINCT product_id FROM prices_current")}
    filtered = [pid for pid in prod_ids if pid in seen]
    tqdm.write(f"Ghost filter: removed {len(prod_ids) - len(filtered)} ghost products, "
               f"{len(filtered)} remain.")
    return filtered, today_week


def _products_for_anchor(conn, store_ids, fallback):
    """Return product IDs seen in prices_current for these stores, intersected with fallback.

    fallback is the global (ghost-filtered) product list; it acts as both the
    allowed-set and the result when prices_current has no records for these stores.
    Intersecting ensures --limit-products and ghost filter are always respected.
    """
    if not store_ids:
        return fallback
    ph = ",".join("?" * len(store_ids))
    rows = conn.execute(
        f"SELECT DISTINCT product_id FROM prices_current WHERE store_id IN ({ph})",
        store_ids,
    ).fetchall()
    if not rows:
        return fallback
    allowed = set(fallback)
    return [pid for pid in (r[0] for r in rows) if pid in allowed]


# ---------------------------------------------------------------------------
# Canary helpers
# ---------------------------------------------------------------------------

def _is_uniform(net_id, canary_seen, canary_changed, thresholds):
    """True when we've seen ≥ threshold stores for this network with zero price changes."""
    return (net_id in thresholds
            and len(canary_seen.get(net_id, set())) >= thresholds[net_id]
            and net_id not in canary_changed)


# ---------------------------------------------------------------------------
# Product-level tiering
# ---------------------------------------------------------------------------

def _build_weekly_product_tier(conn):
    """Return set of product_ids whose price hasn't changed in the last 30 days.

    Cold-start: returns empty set until last_changed_at data accumulates (~30d).
    These products are excluded from daily batches and only fetched on ISO-week-start.
    """
    rows = conn.execute(
        "SELECT DISTINCT product_id FROM prices_current "
        "WHERE last_changed_at < date('now', '-30 days')"
    ).fetchall()
    return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _load_ids_file(path):
    """Return a list of integer IDs from a newline-separated file."""
    with open(path) as f:
        return [int(line.strip()) for line in f if line.strip()]


def main(db_path="data/prices.db", order="stale", limit_stores=None,
         limit_products=None, store_ids_file=None, product_ids_file=None,
         fresh=False, resume=False, max_runtime=0, no_cluster=False,
         products_order="db", reset_skiplist=False):
    if store_ids_file and (limit_stores is not None):
        raise ValueError("--store-ids-file and --limit-stores are mutually exclusive")
    if product_ids_file and (limit_products is not None):
        raise ValueError("--product-ids-file and --limit-products are mutually exclusive")

    checkpoint_path = db_path.replace(".db", "_checkpoint.json")
    lock_path = db_path.replace(".db", "_fetch.lock")

    # Prevent two instances running simultaneously.
    if os.path.exists(lock_path):
        with open(lock_path) as _lf:
            old_pid = _lf.read().strip()
        if old_pid and os.path.exists(f"/proc/{old_pid}"):
            tqdm.write(f"Another fetch_prices is running (PID {old_pid}). Exiting.")
            return
        # Stale lock — previous run crashed without cleanup.
        os.remove(lock_path)

    with open(lock_path, "w") as _lf:
        _lf.write(str(os.getpid()))

    # Convert SIGTERM → SystemExit so the finally block below removes the lock.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    try:
        _main_body(db_path, checkpoint_path, lock_path, order, limit_stores,
                   limit_products, store_ids_file, product_ids_file, fresh,
                   resume, max_runtime, no_cluster, products_order, reset_skiplist)
    finally:
        if os.path.exists(lock_path):
            os.remove(lock_path)


def _main_body(db_path, checkpoint_path, lock_path, order, limit_stores,
               limit_products, store_ids_file, product_ids_file, fresh,
               resume, max_runtime, no_cluster, products_order, reset_skiplist=False):
    conn = init_db(db_path)

    n_abandoned = abandon_stale_runs(conn, "fetch_prices")
    if n_abandoned:
        tqdm.write(f"Marked {n_abandoned} stale 'running' run(s) as 'abandoned'.")

    n_deactivated = deactivate_stale_stores(conn)
    if n_deactivated:
        tqdm.write(f"Dead-store pruning: marked {n_deactivated} store(s) as inactive (no activity >21d).")

    net_backfill = backfill_store_network_ids(conn)
    if net_backfill:
        total_backfilled = sum(net_backfill.values())
        tqdm.write(f"Network backfill (name): tagged {total_backfilled} store(s) — "
                   + ", ".join(f"{n}:{c}" for n, c in net_backfill.items()))

    logo_backfill = backfill_store_network_from_logo(conn)
    if logo_backfill:
        tqdm.write(f"Network backfill (logo): tagged {logo_backfill} store(s).")

    conflicts = check_store_network_conflicts(conn)
    if conflicts:
        tqdm.write(f"Network conflicts: {len(conflicts)} store(s) where logo implies a different network:")
        for c in conflicts[:10]:
            tqdm.write(f"  store {c['store_id']} ({c['store_name']}): db={c['db_network_id']} vs logo→{c['logo_network_id']} ({c['logo_network_name']})")
        if len(conflicts) > 10:
            tqdm.write(f"  … and {len(conflicts) - 10} more.")

    weekly_count, total_active = update_store_tiers(conn)
    if total_active:
        tqdm.write(f"Store tiers: {weekly_count}/{total_active} on weekly tier "
                   f"({100 * weekly_count // total_active}%).")

    # Carry anchor_failures across --fresh so the skiplist survives a daily reset.
    _old_anchor_failures = {}
    if fresh and os.path.exists(checkpoint_path):
        try:
            with open(checkpoint_path) as _f:
                _old_cp = json.load(_f)
            _old_anchor_failures = _old_cp.get("anchor_failures", {})
        except Exception:
            pass

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
        done = cp["done"]
        saved_at = cp["fetched_at"]
        _today = datetime.now(timezone.utc).date()
        _cp_date = datetime.fromisoformat(saved_at).date()
        _age_days = (_today - _cp_date).days
        # Auto-refresh: if resuming an in_progress session started on a prior day,
        # reset fetched_at to now so prices are stamped with today's date.
        # The done-key set is preserved — no work is re-fetched.
        if cp.get("status", "in_progress") == "in_progress" and _age_days > 0:
            fetched_at = datetime.now(timezone.utc).isoformat()
            tqdm.write(
                f"Resuming checkpoint ({len(done)} work units done)  "
                f"fetched_at refreshed: {_cp_date} → {_today} "
                f"({_age_days}d stale, auto-corrected)"
            )
        else:
            fetched_at = saved_at
            tqdm.write(f"Resuming checkpoint ({len(done)} work units done)  fetched_at={fetched_at}")
    else:
        fetched_at = datetime.now(timezone.utc).isoformat()
        done = set()

    stores_raw = conn.execute(
        "SELECT id, name, lat, lon, surrounding_population FROM stores "
        "WHERE lat IS NOT NULL AND lon IS NOT NULL "
        "AND (is_active IS NULL OR is_active = 1)"
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

    # Spatial clustering: every store within radius_m of some anchor.
    # Adaptive split — oversize clusters re-cluster at half radius until
    # ≤ MAX_STORES_PER_CLUSTER members (or MIN_CLUSTER_RADIUS_M floor).
    anchor_covers = {}   # anchor store_id → list of covered store_ids
    anchor_radius = {}   # anchor store_id → effective radius (m) for URL builder
    if not no_cluster and not store_ids_file:
        n_before = len(stores)
        tqdm.write(f"Clustering {n_before} stores (radius={BUFFER_M}m, "
                   f"max/cluster={MAX_STORES_PER_CLUSTER}, "
                   f"min_radius={MIN_CLUSTER_RADIUS_M}m)...")
        stores, anchor_covers, anchor_radius = _cluster_anchors(stores, radius_m=BUFFER_M)
        if order == "stale":
            all_sids = list({s[0] for s in stores} | {sid for v in anchor_covers.values() for sid in v})
            stale_map = _load_stale_map(conn, all_sids)
            stores = sorted(stores, key=lambda s: (-_stale_age(s[0], anchor_covers, stale_map), -(s[4] or 0)))
            tqdm.write(f"Anchors ordered by staleness (stale-first); tiebreak: population.")
        else:
            stores = _order_stores(stores, order)
        split_anchors = sum(1 for r in anchor_radius.values() if r < BUFFER_M)
        tqdm.write(
            f"Clustered {n_before} stores → {len(stores)} anchors "
            f"({100 * (1 - len(stores) / n_before):.0f}% reduction; "
            f"{split_anchors} sub-anchors from adaptive split)"
        )

    if product_ids_file:
        allowed_prods = set(_load_ids_file(product_ids_file))
        prod_ids = [p for p in prod_ids if p in allowed_prods]
        iso_week = datetime.now(timezone.utc).isocalendar()[1]
        tqdm.write(f"Product filter: {len(prod_ids)} products from {product_ids_file}")
    elif cp and cp.get("product_ids"):
        # Resuming mid-run: restore saved product list so batch indices stay stable
        prod_ids = cp["product_ids"]
        iso_week = cp.get("iso_week")
        if limit_products:
            prod_ids = prod_ids[:limit_products]
        tqdm.write(f"Products: restored {len(prod_ids)} from checkpoint.")
    else:
        prod_ids, iso_week = _ghost_filter(conn, prod_ids, cp)
        if products_order == "stale":
            prod_ids = _order_products(conn, prod_ids, "stale")
        if limit_products:
            prod_ids = prod_ids[:limit_products]

    # full_scan=True on first run of a new ISO week (same trigger as ghost filter).
    # Disables canary skipping and product tiering so nothing is missed for >7d.
    today_week = datetime.now(timezone.utc).isocalendar()[1]
    cp_week = cp.get("iso_week") if cp else None
    full_scan = (cp_week is None or cp_week != today_week)

    # ------------------------------------------------------------------
    # Product-level tiering (skip daily; disabled on full-scan week)
    # ------------------------------------------------------------------
    weekly_tier: set = set()
    if not full_scan:
        if cp and cp.get("weekly_tier_ids"):
            weekly_tier = set(cp["weekly_tier_ids"])
            tqdm.write(f"Product tier: restored {len(weekly_tier)} weekly-tier IDs from checkpoint.")
        else:
            weekly_tier = _build_weekly_product_tier(conn)
            if weekly_tier:
                tqdm.write(f"Product tier: computed {len(weekly_tier)} products unchanged >30d.")
        if weekly_tier:
            before = len(prod_ids)
            prod_ids = [p for p in prod_ids if p not in weekly_tier]
            tqdm.write(f"Product tier: removed {before - len(prod_ids)} weekly-tier products, "
                       f"{len(prod_ids)} remain for daily run.")
    else:
        tqdm.write("Product tier: full-scan week — using complete product set.")

    # ------------------------------------------------------------------
    # Store-level tiering: skip anchors whose entire cluster is on weekly tier
    # ------------------------------------------------------------------
    weekly_store_tier: set = set()
    if not full_scan:
        if cp and cp.get("weekly_store_ids"):
            weekly_store_tier = set(cp["weekly_store_ids"])
            tqdm.write(f"Store tier: restored {len(weekly_store_tier)} weekly-tier stores from checkpoint.")
        else:
            weekly_store_tier = {
                row[0] for row in conn.execute(
                    "SELECT id FROM stores WHERE fetch_tier = 'weekly' "
                    "AND (is_active IS NULL OR is_active = 1)"
                )
            }
            if weekly_store_tier:
                tqdm.write(f"Store tier: {len(weekly_store_tier)} stores on weekly tier — "
                           "pure-weekly anchors will be skipped.")
    else:
        tqdm.write("Store tier: full-scan week — weekly tier disabled.")

    # ------------------------------------------------------------------
    # Sentinel mode: load sentinel_stores.json (generated by analyze_price_similarity.py
    # --export-sentinels). Disabled during full-scan weeks so nothing is missed >7d.
    # Only networks where ALL store types are Tier A are included in the config.
    # ------------------------------------------------------------------
    sentinel_store_ids: set = set()          # store IDs that ARE sentinels
    non_sentinel_store_ids: set = set()      # store IDs in sentinel-mode networks that are NOT sentinels
    non_sentinel_by_network: dict = {}       # network_id → list of non-sentinel store IDs
    propagated_networks: set = set()         # networks where propagation has already fired this run
    sentinel_skipped = 0

    sentinel_path = os.path.join(os.path.dirname(os.path.abspath(db_path)), "sentinel_stores.json")
    if os.path.exists(sentinel_path) and not full_scan:
        try:
            with open(sentinel_path) as _sf:
                _sentinel_cfg = json.load(_sf)
            for _net_id, _cfg in _sentinel_cfg.items():
                if _cfg.get("tier") != "A":
                    continue
                _sids = set(_cfg["sentinel_ids"])
                sentinel_store_ids |= _sids
                _net_stores = {
                    row[0] for row in conn.execute(
                        "SELECT id FROM stores WHERE network_id = ? "
                        "AND (is_active IS NULL OR is_active = 1)",
                        (_net_id,),
                    )
                }
                _non_sids = _net_stores - _sids
                non_sentinel_store_ids |= _non_sids
                non_sentinel_by_network[_net_id] = list(_non_sids)
            if sentinel_store_ids:
                tqdm.write(
                    f"Sentinel mode: {len(_sentinel_cfg)} network(s), "
                    f"{len(sentinel_store_ids)} sentinel stores, "
                    f"{len(non_sentinel_store_ids)} non-sentinel stores will be propagated."
                )
        except Exception as _e:
            tqdm.write(f"Sentinel mode: failed to load {sentinel_path}: {_e} — disabled.")
            sentinel_store_ids = set()
            non_sentinel_store_ids = set()
            non_sentinel_by_network = {}
    elif full_scan:
        tqdm.write("Sentinel mode: full-scan week — disabled.")

    # ------------------------------------------------------------------
    # Canary state: pre-compute store→network map; restore from checkpoint
    # ------------------------------------------------------------------
    store_network_map: dict = {
        row[0]: row[1]
        for row in conn.execute("SELECT id, network_id FROM stores")
    }
    canary_nets = set(CANARY_THRESHOLDS)
    active_thresholds = {} if full_scan else CANARY_THRESHOLDS
    # Restore canary state from checkpoint on resume; fresh on full-scan or new run
    if cp and cp.get("canary_seen") and not full_scan:
        canary_seen: dict = {k: set(v) for k, v in cp["canary_seen"].items()}
        canary_changed: set = set(cp.get("canary_changed", []))
        tqdm.write(
            "Canary: restored state — seen: "
            + ", ".join(f"{k}={len(v)}" for k, v in canary_seen.items())
            + (f"  changed: {canary_changed}" if canary_changed else "")
        )
    else:
        canary_seen = {n: set() for n in canary_nets}
        canary_changed = set()
    if full_scan:
        tqdm.write("Canary: full-scan week — skipping disabled.")

    # Restore per-anchor batch counts from checkpoint (needed for correct pre-filter on resume)
    anchor_batch_counts = {}
    if cp and cp.get("anchor_batch_counts"):
        anchor_batch_counts = {int(k): v for k, v in cp["anchor_batch_counts"].items()}

    # In-flight anchors: the exact filtered product list (with stable batch indices) for any
    # anchor interrupted mid-processing. Lets resume continue that SAME narrow list instead of
    # falling back to the full global batch list — which fires empty requests AND can skip the
    # anchor's real products, since prices_current DISTINCT order isn't stable and the anchor's
    # own writes mutate it mid-pass. Stays tiny: fetching is serial, so ≤1 anchor is in-flight.
    inflight_prod_ids = {}
    if cp and cp.get("inflight_prod_ids"):
        inflight_prod_ids = {int(k): v for k, v in cp["inflight_prod_ids"].items()}

    # anchor_failures: persists across both resume and --fresh (skiplist survives daily resets)
    anchor_failures = cp.get("anchor_failures", {}) if cp else _old_anchor_failures
    if reset_skiplist:
        anchor_failures = {}
        tqdm.write("Skiplist cleared (--reset-skiplist).")

    global_batches = list(_batches(prod_ids, BATCH_SIZE))
    n_batches = len(global_batches)
    started_anchors = {int(k.split(":")[0]) for k in done} if done else set()

    # Pre-filter anchors fully done in checkpoint.
    # Uses anchor_batch_counts so per-anchor and global-batch anchors are handled correctly.
    if done:
        n_before_filter = len(stores)
        stores = [(sid, name, lat, lon, pop) for sid, name, lat, lon, pop in stores
                  if not all(f"{sid}:{i}" in done
                             for i in range(anchor_batch_counts.get(sid, n_batches)))]
        n_skipped = n_before_filter - len(stores)
        if n_skipped:
            tqdm.write(f"Skipping {n_skipped} fully-done anchors from checkpoint.")

    tqdm.write(
        f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] Fetching prices: "
        f"{len(stores)} anchors × ~{len(prod_ids)} products "
        f"(≤{n_batches} batches/anchor; ~{len(stores) * n_batches} total batches)  "
        f"order={order}  fetched_at={fetched_at}"
    )

    # Single checkpoint writer — captures live state by reference (all the sets/dicts below
    # are mutated in place, never reassigned, after this point). Called once per anchor and on
    # every exit path, NOT per batch: the old per-batch save re-serialised the whole (growing)
    # done set ~100k times/pass, which is O(n²) in the size of done.
    def save_cp():
        _save_checkpoint(checkpoint_path, fetched_at, done,
                         product_ids=prod_ids, iso_week=iso_week,
                         anchor_batch_counts=anchor_batch_counts,
                         canary_seen=canary_seen, canary_changed=canary_changed,
                         weekly_tier_ids=weekly_tier, weekly_store_ids=weekly_store_tier,
                         anchor_failures=anchor_failures, inflight_prod_ids=inflight_prod_ids)

    run_id = start_run(conn, "fetch_prices", fetched_at)
    total_prices = 0
    stores_done = 0
    canary_skipped = 0   # anchors skipped via canary logic
    tier_skipped = 0     # anchors skipped via store-level tiering
    t_start = time.monotonic()

    try:
        with tqdm(stores, desc="stores", unit="store") as store_bar:
            for store_id, name, lat, lon, pop in store_bar:
                if max_runtime and (time.monotonic() - t_start) >= max_runtime:
                    elapsed = int(time.monotonic() - t_start)
                    tqdm.write(f"\nTime limit reached ({elapsed}s / {max_runtime}s). "
                               f"Checkpoint saved — resume with next run.")
                    save_cp()
                    finish_run(conn, run_id, "interrupted", stores_done, total_prices)
                    print(f"SUMMARY status=timelimit stores={stores_done} prices={total_prices} "
                          f"canary_skipped={canary_skipped} tier_skipped={tier_skipped} "
                          f"weekly_tier={len(weekly_tier)} weekly_store_tier={len(weekly_store_tier)} "
                          f"elapsed={elapsed}s fetched_at={fetched_at}", flush=True)
                    return  # finally block closes conn
                store_bar.set_description(name[:30])

                anchor_store_ids = anchor_covers.get(store_id, [store_id])

                # ----------------------------------------------------------
                # Sentinel skip: anchor covers only non-sentinel stores in a
                # Tier-A network → skip API call, propagate freshness only.
                # Anchors that include a sentinel store are fetched normally;
                # propagation to non-sentinels fires after that fetch completes.
                # ----------------------------------------------------------
                if non_sentinel_store_ids:
                    _anchor_set = set(anchor_store_ids)
                    _has_sentinel = bool(_anchor_set & sentinel_store_ids)
                    _all_non_sentinel = _anchor_set and _anchor_set.issubset(non_sentinel_store_ids)
                    if _all_non_sentinel and not _has_sentinel:
                        propagate_last_checked(conn, list(_anchor_set), fetched_at)
                        _n_skip = anchor_batch_counts.get(store_id, n_batches)
                        for _i in range(_n_skip or n_batches):
                            done.add(f"{store_id}:{_i}")
                        sentinel_skipped += 1
                        stores_done += 1
                        store_bar.set_postfix(total_prices=total_prices,
                                              sentinel_skip=sentinel_skipped)
                        inflight_prod_ids.pop(store_id, None)
                        save_cp()
                        continue

                # ----------------------------------------------------------
                # Canary skip / product filter
                # ----------------------------------------------------------
                uniform_sids = set()
                non_unif_sids = set(anchor_store_ids)
                if active_thresholds:
                    uniform_sids = {
                        s for s in anchor_store_ids
                        if _is_uniform(store_network_map.get(s), canary_seen,
                                       canary_changed, active_thresholds)
                    }
                    non_unif_sids = set(anchor_store_ids) - uniform_sids

                if not non_unif_sids:
                    # Pure-uniform anchor: propagate freshness, skip API call
                    propagate_last_checked(conn, list(uniform_sids), fetched_at)
                    n_skipped_batches = anchor_batch_counts.get(
                        store_id, anchor_batch_counts.get(store_id, n_batches)
                    )
                    for i in range(n_skipped_batches or n_batches):
                        done.add(f"{store_id}:{i}")
                    canary_skipped += 1
                    stores_done += 1
                    store_bar.set_postfix(total_prices=total_prices,
                                         canary_skip=canary_skipped)
                    inflight_prod_ids.pop(store_id, None)
                    save_cp()
                    continue

                # Store-level tier skip: all covered stores on weekly tier → no API call
                if weekly_store_tier and all(s in weekly_store_tier for s in anchor_store_ids):
                    propagate_last_checked(conn, anchor_store_ids, fetched_at)
                    n_skip = anchor_batch_counts.get(store_id, n_batches)
                    for i in range(n_skip or n_batches):
                        done.add(f"{store_id}:{i}")
                    tier_skipped += 1
                    stores_done += 1
                    store_bar.set_postfix(total_prices=total_prices,
                                         tier_skip=tier_skipped)
                    inflight_prod_ids.pop(store_id, None)
                    save_cp()
                    continue

                # Dead anchor skiplist: skip until retry date
                _af = anchor_failures.get(str(store_id))
                if _af and _af.get("skip_until") and _af["skip_until"] > date.today().isoformat():
                    tqdm.write(f"  SKIPLIST: {name} (skip_until={_af['skip_until']}, n={_af['n']})")
                    for _i in range(anchor_batch_counts.get(store_id, n_batches)):
                        done.add(f"{store_id}:{_i}")
                    stores_done += 1
                    inflight_prod_ids.pop(store_id, None)
                    save_cp()
                    continue

                store_prices = 0
                cap_hit_batches = 0
                batch_total = 0
                batch_failures = 0

                # Per-anchor product filtering:
                # - Started anchors (resume): use global list for stable batch indices.
                # - New anchors with uniform stores: query only non-uniform store products.
                # - New anchors, all non-uniform: standard per-anchor filter.
                if store_id in started_anchors and store_id in inflight_prod_ids:
                    # Resume the exact filtered list persisted when this anchor was first
                    # started → stable batch indices, no wasted/empty requests, no skipped
                    # products.
                    anchor_prod_ids = inflight_prod_ids[store_id]
                    batches_list = list(_batches(anchor_prod_ids, BATCH_SIZE))
                elif store_id in started_anchors:
                    # Legacy checkpoint without a persisted list (pre-2026-06-02): fall back to
                    # the global list for index stability. May fire empty requests for this
                    # anchor, but stays correct.
                    batches_list = global_batches
                else:
                    fetch_store_ids = list(non_unif_sids) if uniform_sids else anchor_store_ids
                    anchor_prod_ids = _products_for_anchor(conn, fetch_store_ids, prod_ids)
                    batches_list = list(_batches(anchor_prod_ids, BATCH_SIZE))
                    anchor_batch_counts[store_id] = len(batches_list)
                    inflight_prod_ids[store_id] = anchor_prod_ids

                batch_total = len(batches_list)
                with tqdm(batches_list, desc="  batches", unit="batch", leave=False) as batch_bar:
                    for i, batch in enumerate(batch_bar):
                        if max_runtime and (time.monotonic() - t_start) >= max_runtime:
                            break  # partial anchor — resume will pick up from last saved batch key
                        key = f"{store_id}:{i}"
                        if key in done:
                            batch_bar.set_postfix(status="resumed")
                            continue

                        csv_ids = ",".join(str(p) for p in batch)
                        buf = anchor_radius.get(store_id, BUFFER_M)
                        url = (
                            f"{BASE}/GetStoresForProductsByLatLon"
                            f"?lat={lat}&lon={lon}&buffer={buf}"
                            f"&csvprodids={csv_ids}&OrderBy=price"
                        )
                        try:
                            root = fetch_xml(url)
                        except requests.exceptions.RequestException as exc:
                            tqdm.write(
                                f"  WARN: skipping {name} batch {i} after all retries failed: {exc}"
                            )
                            batch_failures += 1
                            time.sleep(SLEEP_BETWEEN)
                            continue
                        result_stores, prices = parse_stores_and_prices(root, fetched_at)
                        # API caps at 50 stores per response. If we see 50 returned
                        # while our local cluster has >50 members, the response
                        # truncated — flag it (after adaptive split, this should
                        # be rare; persistent hits mean either density at the
                        # MIN_CLUSTER_RADIUS_M floor or stores the API knows about
                        # that aren't yet in our local `stores` table).
                        if len(result_stores) >= 50 and len(anchor_covers.get(store_id, [])) > 50:
                            cap_hit_batches += 1

                        for s in result_stores:
                            upsert_store(
                                conn, s["id"], s["name"], s["addr"],
                                s["lat"], s["lon"], s["uat_id"],
                                s["network_id"], s["zipcode"],
                                logo_url=s.get("logo_url"),
                                type_id=s.get("type_id"),
                                type_name=s.get("type_name"),
                            )
                        for p in prices:
                            changed = insert_price(
                                conn,
                                p["product_id"], p["store_id"], p["price"],
                                p["price_date"], p["promo"], p["brand"], p["unit"],
                                p["retail_categ_id"], p["retail_categ_name"],
                                p["fetched_at"],
                            )
                            # Update canary tracking
                            if active_thresholds:
                                net = store_network_map.get(p["store_id"])
                                if net in canary_nets:
                                    canary_seen[net].add(p["store_id"])
                                    if changed:
                                        canary_changed.add(net)

                        conn.commit()
                        store_prices += len(prices)
                        batch_bar.set_postfix(prices=store_prices)

                        done.add(key)
                        # Checkpoint is written once per anchor (and on every exit path), not
                        # here per batch — see save_cp(). done.add stays in memory; on a hard
                        # kill mid-anchor we re-fetch this anchor's batches next run (idempotent
                        # via INSERT OR IGNORE). The timelimit break below persists progress.
                        time.sleep(SLEEP_BETWEEN)

                # Intra-anchor timelimit: break fired mid-batch → exit cleanly now
                if max_runtime and (time.monotonic() - t_start) >= max_runtime:
                    elapsed = int(time.monotonic() - t_start)
                    tqdm.write(f"\nTime limit reached mid-anchor ({elapsed}s). Checkpoint saved.")
                    save_cp()  # persists this anchor's completed batches + its in-flight list
                    finish_run(conn, run_id, "interrupted", stores_done, total_prices)
                    print(f"SUMMARY status=timelimit stores={stores_done} prices={total_prices} "
                          f"canary_skipped={canary_skipped} tier_skipped={tier_skipped} "
                          f"sentinel_skipped={sentinel_skipped} "
                          f"elapsed={elapsed}s fetched_at={fetched_at}", flush=True)
                    return

                # Dead anchor detection: all non-skipped batches failed → track failure
                if store_prices > 0:
                    anchor_failures.pop(str(store_id), None)
                elif batch_total > 0 and batch_failures == batch_total:
                    _entry = anchor_failures.get(str(store_id), {"n": 0, "skip_until": None})
                    _entry["n"] += 1
                    if _entry["n"] >= DEAD_ANCHOR_THRESHOLD:
                        _skip_until = (date.today() + timedelta(days=DEAD_ANCHOR_SKIP_DAYS)).isoformat()
                        _entry["skip_until"] = _skip_until
                        tqdm.write(
                            f"  DEAD ANCHOR: {name} ({_entry['n']} consecutive all-fail runs)"
                            f" → skipping until {_skip_until}"
                        )
                    anchor_failures[str(store_id)] = _entry

                # Sentinel propagation: if this anchor contained a sentinel store,
                # copy its prices_current to all non-sentinel stores in the same network.
                # Fires once per network per run (propagated_networks guard).
                if non_sentinel_by_network and store_prices > 0:
                    for _sid in set(anchor_store_ids) & sentinel_store_ids:
                        _net = store_network_map.get(_sid)
                        if _net in non_sentinel_by_network and _net not in propagated_networks:
                            _targets = non_sentinel_by_network[_net]
                            _n = propagate_network_prices(conn, _sid, _targets, fetched_at)
                            propagated_networks.add(_net)
                            tqdm.write(
                                f"  Sentinel propagated: {_net} store {_sid} → "
                                f"{_n} target stores ({len(_targets)} in network)"
                            )

                tqdm.write(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}]  "
                           f"{name}: {store_prices} prices  ({stores_done + 1}/{len(stores)})")
                if cap_hit_batches:
                    tqdm.write(
                        f"  CAP-HIT anchor={store_id} cluster={len(anchor_covers.get(store_id, []))} "
                        f"radius={anchor_radius.get(store_id, BUFFER_M)}m "
                        f"batches_capped={cap_hit_batches}"
                    )
                # Log when a canary network just crossed its threshold
                if active_thresholds:
                    for net, threshold in active_thresholds.items():
                        seen_count = len(canary_seen.get(net, set()))
                        if seen_count == threshold:
                            status = "CHANGED" if net in canary_changed else "uniform"
                            tqdm.write(f"  Canary: {net} reached {threshold} stores — {status}")
                total_prices += store_prices
                stores_done += 1
                store_bar.set_postfix(total_prices=total_prices)
                # Anchor fully fetched — drop its in-flight list and checkpoint once.
                inflight_prod_ids.pop(store_id, None)
                save_cp()

        _finish_checkpoint(checkpoint_path, fetched_at, done, iso_week=iso_week,
                           anchor_batch_counts=anchor_batch_counts, product_ids=prod_ids,
                           canary_seen=canary_seen, canary_changed=canary_changed,
                           weekly_tier_ids=weekly_tier, weekly_store_ids=weekly_store_tier,
                           anchor_failures=anchor_failures, inflight_prod_ids=inflight_prod_ids)
        finish_run(conn, run_id, "completed", stores_done, total_prices)
        elapsed = int(time.monotonic() - t_start)
        tqdm.write(f"\nDone. {total_prices} price records inserted.")
        print(f"SUMMARY status=completed stores={stores_done} prices={total_prices} "
              f"canary_skipped={canary_skipped} tier_skipped={tier_skipped} "
              f"sentinel_skipped={sentinel_skipped} "
              f"weekly_tier={len(weekly_tier)} weekly_store_tier={len(weekly_store_tier)} "
              f"elapsed={elapsed}s fetched_at={fetched_at}", flush=True)

    except (KeyboardInterrupt, SystemExit):
        elapsed = int(time.monotonic() - t_start)
        try:
            save_cp()
        except Exception:
            pass
        try:
            finish_run(conn, run_id, "interrupted", stores_done, total_prices)
        except Exception:
            pass
        tqdm.write(f"\nInterrupted. {total_prices} price records written so far.")
        print(f"SUMMARY status=interrupted stores={stores_done} prices={total_prices} "
              f"canary_skipped={canary_skipped} tier_skipped={tier_skipped} "
              f"sentinel_skipped={sentinel_skipped} "
              f"weekly_tier={len(weekly_tier)} weekly_store_tier={len(weekly_store_tier)} "
              f"elapsed={elapsed}s fetched_at={fetched_at}", flush=True)
        raise
    except Exception as exc:
        elapsed = int(time.monotonic() - t_start)
        try:
            save_cp()
        except Exception:
            pass
        try:
            finish_run(conn, run_id, "error", stores_done, total_prices, notes=str(exc))
        except Exception:
            pass
        print(f"SUMMARY status=error stores={stores_done} prices={total_prices} "
              f"canary_skipped={canary_skipped} tier_skipped={tier_skipped} "
              f"elapsed={elapsed}s error={exc!r} fetched_at={fetched_at}", flush=True)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("db", nargs="?", default="data/prices.db")
    parser.add_argument("--order", choices=["population", "geographic", "stale"],
                        default="stale",
                        help="anchor ordering mode (default: stale — stalest anchors first)")
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
    parser.add_argument("--products-order", choices=["db", "stale"], default="db",
                        help="product ordering: db=insertion order (default), "
                             "stale=never-fetched first then oldest fetched_at")
    parser.add_argument("--reset-skiplist", action="store_true",
                        help="clear the dead-anchor skiplist and retry all skipped anchors")
    args = parser.parse_args()
    main(args.db, args.order, args.limit_stores, args.limit_products,
         args.store_ids_file, args.product_ids_file, args.fresh, args.resume,
         args.max_runtime, args.no_cluster, args.products_order, args.reset_skiplist)
