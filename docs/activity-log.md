# Activity Log

---

## General

### 2026-04-15 — GitHub Actions CI pipeline + SQL queries

- Added `.github/workflows/ci_prices.yml`: daily cron (05:00 UTC) + manual dispatch; shallow checkout; weekly reference refresh on Mondays; commits `data/prices_ci.db` back to repo.
- Created `build_ci_subset.py`: generates `data/ci_stores.txt` and `data/ci_products.txt` from the DB. Store selection = top 10 per network by population ∪ 50 middle-pop stores spread by Z-order (Romania grid). Product selection = top 50 overall ∪ top 20 per category, both ranked by blended store-coverage + record-count score.
- Extended `fetch_prices.py` with `--store-ids-file` and `--product-ids-file` flags: load a newline-separated ID list and filter stores/products accordingly; mutually exclusive with `--limit-stores`/`--limit-products`.
- Extended `docs/queries.md` with new sections: product popularity (top N overall, top N per category), CI store selection (top-per-network, middle-pop geo batch), data quality checks (store coverage, products with no prices, records per fetch date, stores fetched today).
- Updated `.gitignore` to allow committing `data/prices_ci.db`, `data/ci_stores.txt`, `data/ci_products.txt`.
- Decision: DB committed to repo (not GitHub Artifacts) for simplicity; CI DB is separate from local `data/prices.db` to avoid conflicts.

## Retail

### 2026-04-15 — fetch_prices: --resume flag + generate_map.py

- Added `--resume` flag to `fetch_prices.py`: bypasses the "already completed today" guard while keeping the existing checkpoint's `done` set, so only newly added stores are fetched and old store×batch keys are skipped.
- Added `generate_map.py`: regenerates `docs/stores_map.html` from `data/prices.db` (stores + network JOIN); assigns colors per network; updates legend counts. Run with `python generate_map.py` after any store discovery run.

### 2026-04-15 — per-store price fetching pipeline + stores map

- Rewrote `fetch_prices.py` to iterate individual stores instead of UATs; each store is queried from its own lat/lon, guaranteeing it always appears in results.
- Two ordering modes: `--order population` (surrounding_population DESC, default) and `--order geographic` (Z-order grid ~50 km cells, snake traversal for national spread).
- Added `surrounding_population REAL` column to `stores` table (migration in `db.py`).
- Fixed `upsert_store` in `db.py` to use explicit column names (`INSERT … ON CONFLICT DO UPDATE`) so new columns aren't clobbered on store updates.
- New `update_store_populations.py`: sums locality populations within 10 km radius for each store using `populatie romania siruta coords.csv`; runs in ~4s for 2,773 stores.
- Preserved old UAT-based script as `fetch_prices_by_uat.py`.
- Switched `discover_stores.py` locality source from GeoNames Excel to `populatie romania siruta coords.csv` (3,180 localities, all with coords, zero missing); default `--min-pop` lowered to 2,500 → 1,842 probe points.
- Added static Leaflet map (`docs/stores_map.html`) + CSV export (`docs/stores.csv`) for all discovered stores; markers coloured by network, clustered, popup with name/address.

### 2026-04-15 — discover_stores.py: population-based store discovery

- Rewrote `discover_stores.py` to probe `GetStoresForProductsByLatLon` using lat/lon from `data/reference/geonames-RO.xlsx` (788 Romanian populated places ≥ 5,000 pop), instead of the previous approach that was limited to the 20 UATs already in the DB.
- Deduplication: greedy haversine within 4km radius → 727 probe points; ensures no two adjacent cities trigger the same 5km API buffer twice.
- Checkpoint/resume via `data/discover_stores_checkpoint.json`; safe to interrupt and restart.
- `--dry-run` prints probe points without API calls; `--limit N` for testing; `--debug` for verbose output.
- Confirmed live: 3 probes → 51 new stores; 0 errors.
- Decision: using GeoNames lat/lon directly (no UAT ID matching) keeps the script simple and independent of the UATs table.

### 2026-04-14 — Initial pipeline implementation

- Explored API by reading sample XML responses in `docs/reference/sampleResponses/`
- Created `CLAUDE.md` with project overview, architecture, and API notes
- Implemented `db.py` — SQLite schema + upsert helpers
- Implemented `api.py` — `fetch_xml()` with retry/backoff, XML parsers for all endpoints, `centroid_from_wkt()`
- Implemented `fetch_reference.py` — one-shot pipeline: networks → UATs → categories → products
- Implemented `fetch_prices.py` — daily price pipeline: UAT × product batches
- Fixed invalid XML character entity refs (`&#x1C;` etc.) in product names — API returns these for some categories; added `_strip_invalid_char_refs()` in `api.py`
- Fixed `categ_id` always being `None` — the API doesn't echo category back in product XML; fall back to the queried category ID in `fetch_reference.py`
- Discovered API buffer limit: returns 0 results for `buffer > 5000 m`; corrected plan (was 20 000 m); updated `CLAUDE.md`
- Changed DB path from project root to `data/prices.db`
- Added `--limit` flags to both fetch scripts for fast smoke-testing
- Added tqdm progress bars to both fetch scripts

---

## Gas

### 2026-04-14 — Initial gas pipeline implementation

- Explored gas API endpoints and sample XML responses in `docs/carburanti/reference/`
- Added gas tables to `db.py`: `gas_networks`, `gas_products`, `gas_stations`, `gas_prices`
- Added gas parsers to `api.py`: `parse_gas_networks()`, `parse_gas_products()`, `parse_gas_items()`
- Implemented `fetch_gas_reference.py` — fetches gas networks and fuel product types
- Implemented `fetch_gas_prices.py` — fetches prices per UAT (single request covers all 6 fuel types)
- Gas API is simpler than retail: no batching needed, one request per UAT returns all stations + prices

---

## General

### 2026-04-14 — Checkpoint/resume, last_checked_at, and run logging

- Added `last_checked_at TEXT` column to `prices` and `gas_prices` tables; `init_db()` migrates existing DBs via `ALTER TABLE` with try/except
- Changed `insert_price` and `insert_gas_price` from `INSERT OR IGNORE` to UPSERT: new rows get `fetched_at == last_checked_at`; re-checks update only `last_checked_at`, preserving the original insert timestamp
- Added checkpoint/resume to both price fetch scripts: progress saved to `data/retail_checkpoint.json` / `data/gas_checkpoint.json` after each work unit; `--fresh` flag forces a clean run; checkpoint deleted on clean completion
- Added `runs` table (`script, started_at, finished_at, status, uats_processed, records_written, notes`) to log every pipeline execution
- Added `start_run()` / `finish_run()` helpers in `db.py`; both price scripts wrapped in try/except/finally so status (`completed`, `interrupted`, `error`) is always recorded

### 2026-04-14 — Smarter checkpoint lifecycle (never re-fetch unless `--fresh`)

- On successful completion, checkpoint is now kept with `status: "completed"` instead of being deleted
- Same-day re-runs (e.g. cron re-trigger after a perceived failure) exit immediately — no redundant API calls
- New-day runs detect the date change and start fresh automatically
- Interrupted (`in_progress`) checkpoints always resume regardless of age — supports multi-day rate-limit recovery
- `--fresh` remains the explicit escape hatch to force a clean start
- Backward-compatible: checkpoints without a `status` field are treated as `in_progress`
- Updated `readme.md` to document checkpoint behaviour for both fetch scripts
