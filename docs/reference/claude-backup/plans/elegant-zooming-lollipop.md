# Plan: Store Discovery via Population-Filtered UAT Sampling

## Context

The current pipeline fetches prices by iterating UATs × product batches, but doesn't systematically discover *all* stores. A naive lat/lon grid over Romania (~238k km²) would require ~9,500 API calls at 5km spacing. The goal is a smarter approach: use UAT centroids as sampling points, filtered by population, so we cover areas where retail chain stores actually exist — starting with the highest-density areas and refining later.

---

## Algorithm (V1 — high-population, low call count)

### Inputs
- `uats` table: UAT id, centroid lat/lon, WKT polygon (populated by `fetch_reference.py`)
- `data/reference/populatie romania siruta coords.xlsx`: SIRUTA (= UAT id) → population

### Step 1 — Filter UATs by population
- Join `uats` with the population spreadsheet on `uats.id = SIRUTA`
- Keep only UATs with **pop ≥ 10,000** → ~243 UATs (46 cities + 197 towns)
- This is the V1 threshold; will be lowered in later passes (5k, 2k)

### Step 2 — Generate sampling points per UAT
For each filtered UAT:
- Compute bounding box diagonal from WKT polygon (or from centroid + rough area estimate)
- If diagonal ≤ 10km → **use centroid only** (one 5km-radius circle covers the UAT)
- If diagonal > 10km → **tile the bounding box** with a grid spaced ~8km apart, then keep only points inside (or within 1km of) the UAT polygon

The 8km spacing ensures adjacent 5km circles overlap ~1km, eliminating gaps within large cities.

### Step 3 — Global deduplication of sampling points
After generating all points, drop any point within 4km of an already-selected point (greedy pass, sorted by population desc). This prevents redundant calls at the borders of adjacent towns.

### Step 4 — Fetch stores per point
For each sampling point, call the discovery endpoint with all known product IDs (or a broad batch) to surface stores. Upsert into the `stores` table. Existing `INSERT OR IGNORE` on prices handles deduplication.

### Estimated call count (V1)
- 46 large cities (diagonal > 10km): avg ~4 points each → ~184 calls
- 197 towns (diagonal ≤ 10km): 1 centroid each → ~197 calls
- After dedup: **~300–400 total calls**

---

## New script: `discover_stores.py`

Separate from `fetch_prices.py`. Responsibilities:
- Load + filter UATs
- Generate + deduplicate sampling points
- Call API per point, upsert stores only (no prices)
- Log coverage stats (UATs covered, points generated, stores found)
- Config flag: `--pop-threshold` (default 10000), `--debug`

---

## Files to create/modify
- **New**: `discover_stores.py`
- **Read-only**: `data/reference/populatie romania siruta coords.xlsx`
- **Read**: `db.py` (reuse `upsert_store`), `api.py` (reuse `fetch_xml`, parsers)
- **DB**: `stores` table (already exists, upsert is safe)

---

## Verification
1. Run `fetch_reference.py` first to ensure all UATs are in DB
2. Run `discover_stores.py --pop-threshold 50000 --debug` (46 UATs, ~150 calls — fast smoke test)
3. Check store count: `sqlite3 data/prices.db "SELECT COUNT(*) FROM stores;"`
4. Spot-check a city: `sqlite3 data/prices.db "SELECT name, addr FROM stores WHERE uat_id=54975 LIMIT 10;"` (Cluj)
5. Widen to 10k, compare store count delta

---

## Later passes (not in scope now)
- V2: lower threshold to 5,000 (add ~543 UATs)
- V3: lower to 2,000 (add most communes)
- V4: polygon-aware filtering (exclude mountain/forest UATs by area/population density ratio)
