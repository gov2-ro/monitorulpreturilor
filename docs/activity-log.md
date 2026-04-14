# Activity Log

---

## Retail

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
