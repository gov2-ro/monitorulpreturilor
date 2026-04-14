# Activity Log

## 2026-04-14

### Session: initial pipeline implementation

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
