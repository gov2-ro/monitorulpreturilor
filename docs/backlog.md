# Backlog

---

## Retail

### Bugs / Known Issues

- [ ] **Stores with missing `network_id` but network name in store name** — some stores have `network_id = NULL` in the `stores` table but the network name is embedded in `stores.name` (e.g. "Kaufland Cluj"). Backfill `network_id` by fuzzy-matching store name against `retail_networks.name`. Run as a one-off migration; add a check to flag newly inserted stores with null `network_id` going forward.

- **API returns max 50 stores per request** — city coverage is limited to the 50 nearest stores from the UAT centroid. Large cities (e.g. București) have more stores than this. Multi-point sweeping (e.g. grid or known store-dense coordinates) would improve coverage.
- **Products appear in multiple categories** — when a product is fetched under several `CSVcategids` queries, the last `INSERT OR REPLACE` wins for `categ_id`. Not harmful but the assigned category may be arbitrary.
- **OUG categories (10001–10041) return 0 products** — these are regulatory emergency-ordinance categories; likely not populated in the product catalogue. May be worth skipping in future runs to save time.

### Todo

- [ ] **Compile full UAT list** — `GetUATByName` (no params) returns only the top ~20 UATs. The API supports `GetUATByName?uatname={name}` for search, so a full list can be built by querying each Romanian county name / city name, or by maintaining a static list of all municipality IDs. Until this is done, price fetching only covers the 20 UATs currently in the DB.
- [x] **Smart grid-probe using populated UATs only** — implemented as `discover_stores.py`: uses `data/reference/geonames-RO.xlsx` (788 populated places ≥ 5k pop) → deduplicates within 4km radius → 727 probe points. Probes `GetStoresForProductsByLatLon` directly by lat/lon; no UAT ID matching needed. Checkpoint/resume, dry-run, debug flags.
- [ ] **Grid-probe Romania for complete store discovery** — write a one-off script that sweeps a lat/lon grid covering Romania's bounding box (~43.6–48.3°N, ~20.3–29.7°E) at ~5 km steps (≈ the API's 5000 m buffer radius), calling `GetStoresForProductsByLatLon` at each point with a minimal product batch. Collect and deduplicate all returned stores by `store_id`. This gives a ground-truth store list independent of UAT data, catching stores in UATs not yet in the DB. Estimated grid size: ~160 × 190 = ~30 000 points; with 0.5 s sleep that's ~4 hours — run once, results are stable.
- [ ] **Build a complete store list** — the real unit of interest is individual stores, not UATs. Two discovery strategies: (1) probe each known UAT centroid with `GetStoresForProductsByLatLon` and collect all returned stores; (2) grid-probe lat/lon across Romania at ~5 km intervals (matching the API's 5000 m buffer cap) to catch stores in UATs not yet in the DB. Store IDs are stable — deduplicate by `store_id`. This is a prerequisite for meaningful cross-store price comparisons and for knowing which stores we're missing.
- [ ] **Switch price fetching from UAT centroid to per-store lat/lon** — currently `fetch_prices.py` uses each UAT's centroid as the query coordinate, returning up to 50 nearby stores. This misses stores at city edges and caps out on large cities. Once a full store list exists (see above), iterate over known store coordinates instead: one `GetStoresForProductsByLatLon` call centred on each store guarantees that store is always included in results. Likely reduces total requests too (no duplicate coverage overlap between adjacent UATs).

- [x] **[PRIORITY] Optimise fetch_prices.py request strategy to reduce total API calls** — implemented greedy set-cover spatial clustering (5 km radius): 3813 stores → 681 anchors (82% reduction). Batch size raised from 50 → 200 products/request (API-tested; 500 hits URL-length limit). Combined: 530k → ~24k requests, ~22h → ~1h (95% reduction). `--no-cluster` flag for fallback.
- [ ] Investigate multi-point sweeping per UAT for better store coverage
- [ ] Add `fetch_prices.py` progress persistence (checkpoint file) so interrupted runs resume from where they left off instead of skipping already-fetched data
- [ ] **Price variability analysis across networks** — product IDs are shared across networks so cross-network comparison is feasible. Blockers: (1) unit field is dirty (`Kg/K/kg`, `BUC/BUCATA/Buc/Buc.`, `Litru/L/l`) — normalize before comparing; (2) compare only within same normalized unit to avoid package-size noise; (3) exclude or flag SELGROS — it's B2B wholesale, bulk sizes make it incomparable to consumer chains. After normalization: compute min/max/spread/ratio per `(product_id, unit_normalized)` grouped by network. Also run intra-network consistency check (same product, same network, different stores — should be near-zero variance; outliers flag data quality issues).
- [ ] **Check price variability before scraping all stores** — with 50 stores per UAT × 20 UATs, it's unclear whether prices actually differ meaningfully between individual stores in the same network, or even across networks. Before committing to a full scrape, analyse existing data: compute price variance per product grouped by (UAT, network) and across networks within the same UAT. If intra-network variance is near zero, scraping one store per network per UAT is sufficient and would cut request volume dramatically.

---

## Gas

### Bugs / Known Issues

- [ ] **Some UATs have NULL `name` in the `uats` table** — `fetch_gas_prices.py` crashes at `uat_name[:30]` when `name` is NULL (patched with fallback to `uat_id`). Root cause unknown — likely rows inserted without a name during reference fetch or partial UAT discovery. Fix: audit `SELECT id, name FROM uats WHERE name IS NULL;`, backfill names via `GetUATByName?uatname=` or a static lookup, and add a NOT NULL constraint or a fetch-time warning.

### Todo

- [ ] **Compile full UAT list** — same issue as retail: `Gas/GetUATByName` (no params) returns only top UATs. Use `?uatname=` search or a static list to cover all municipalities.
- [ ] **`fetch_gas_prices.py` skips newly discovered stations** — the "already completed today" check uses `fetched_at` date, so if new stations are added mid-day (e.g. after running `discover_gas_stations.py`), they won't be fetched until the next day's run. Investigate whether the skip logic should be per-station rather than a global "all done" check.
- [ ] **`GetGasItemsByRoute` broken on server** — endpoint exists and accepts real UAT `route_id` values (Bucharest→Brașov tested) but returns HTTP 500 AutoMapper error. Not usable until API owners fix it. Watch for future API updates.
- [ ] Add services data (`GetGasServicesFromCatalog`) — currently skipped; would allow filtering stations by amenities (ATM, car wash, restaurant, etc.)
- [ ] **Gas station discovery for highway/road stations** — `discover_gas_stations.py` covers cities (population-based probes), but stations along highways and national roads in low-population UATs will be missed. A later phase should add a lat/lon grid probe over Romania's bounding box at ~10–15 km steps (much sparser than the 5 km retail grid since gas stations are fewer). The `GetGasItemsByLatLon` endpoint and `parse_gas_items()` already support this — just add a grid probe source as an alternative to the population CSV.

---

## General

### Todo

- [x] **API endpoint discovery** — ran systematic probe of 50+ candidate endpoints (2026-04-18). See [`docs/reference/undocumented-endpoints.md`](reference/undocumented-endpoints.md) for full results. Key findings:
  - `GetCatalogProductsByNameNetwork` (no params) dumps all **87,448 product names** — useful for a client-side search index
  - `GetStoresForProductsByUat` confirmed working — supports `csvnetworkids` filter (UAT-bounded queries per network)
  - `GetGasItemsByRoute` exists but crashes with AutoMapper bug (server-side)
  - No price history API exists — our SQLite DB is the only source
  - [ ] **Build product search index from full catalog dump** — weekly download of all 87k product names, serialize to compact JSON (fuse.js or trie), embed in `compare.html` for client-side fuzzy search
  - [ ] **Sample unknown products from full catalog dump** — pick ~50–100 random product IDs from the 80k not in our DB, probe `GetStoresForProductsByLatLon` for each, see if any return prices. Goal: understand whether these are live products, discontinued items, B2B-only, or duplicates. Informs whether the full dump is worth deeper exploration.
- [x] **Build UI Phase 1** — `generate_site.py` generates 5 static pages into `docs/`: Dashboard, Network Price Index, Fuel Leaderboard, Pipeline Health, Enhanced Store Map. See [`docs/ui-plan.md`](ui-plan.md) for remaining phases.
- [ ] **Build UI Phase 2+** — Cheapest Basket Calculator, Product Price Comparison, Gas Price Map, Price Spread Dashboard. Then standalone app with search, geolocation, saved baskets, alerts, API.
- [ ] **Site generation in CI** — add `generate_site.py` step to CI workflows so `docs/` pages are regenerated after each fetch. Replace `generate_map.py` calls (now superseded).
- [ ] Set up automated daily fetch (cron / launchd) for both pipelines
- [ ] **Restore 4× daily CI schedule when store/product set grows** — currently one daily run at 04:00 UTC covers the full subset in ~1h (after spatial clustering). When the CI subset is expanded (more stores or products), a single run may again exceed GH Actions' 2h cap. At that point: (1) restore the 4× daily cron triggers in `ci_prices.yml` (the checkpoint/resume mechanism already supports this); (2) revisit store distribution in `build_ci_subset.py` — currently top-per-network + mid-pop geographic batch; consider whether the geographic spread is still representative as store count grows (check coverage map via `generate_map.py` before committing to a larger subset).
- [x] **Plan GitHub Actions workflow** — implemented as `.github/workflows/ci_prices.yml`: daily cron + manual dispatch; runs on a curated subset (top-per-network + mid-pop-geo stores; blended-rank products); commits `data/prices_ci.db` back to repo.
- [ ] Analyse price data — cheapest basket per city, price trends over time
- [ ] **Verify `price_date` rotation cadence** — the schema supports full price history (UNIQUE on `product_id, store_id, price_date`), so trends are preserved across days. However, `price_date` comes from the API, not the fetch timestamp. If the API serves stale data with yesterday's date, a real price change on the same day would be silently dropped (ON CONFLICT does nothing to the price column). Spot-check a few products over consecutive fetches to confirm the API rotates `price_date` daily and doesn't lag behind.
- [ ] **Manual cross-check against retailer online catalogues** — spot-check products and prices against the public websites/apps of major networks (Kaufland, Lidl, Carrefour, etc.) to validate API data quality: are product names consistent? Are prices current? Are any products missing? Useful before investing in heavier analysis or a UI. See:  [shop-scraper](https://github.com/pax/shop-scraper), [midas](https://github.com/pax/midas), [midas-cc](https://github.com/pax/midas-cc)
- [ ] **Product/brand word-frequency analysis** — run word statistics over all product names to surface the most common brands and product terms. Useful for: identifying dedicate/private-label brands, spotting near-duplicate products listed under slightly different names, and deciding which products to prioritise for more frequent fetching. Could be a small standalone script that queries `products.name` and outputs ranked token counts.
- [ ] **Brand-to-network affinity analysis** — extend `analyse_products.py` to add a `networks` column to `brands.csv`: for each brand, show which networks carry it and what share of its price rows each network accounts for. Brands appearing in only one network are private-label candidates; brands heavily skewed toward one network are de-facto exclusives even if technically available elsewhere. Query: `prices JOIN stores GROUP BY brand_key, network_id`.
- [ ] **Near-duplicate product detection** — some products are likely the same item listed under slightly different names (e.g. "Cafea Jacobs 250g" vs "Jacobs Cafea 250 G"). Explore fuzzy string matching (e.g. token-sort ratio) on `products.name` within the same category to surface candidate duplicates for manual review or merging.
- [ ] **Network-exclusive brand/product detection** — identify products or brands that appear in only one retail network (likely private-label / store-brand exclusives, e.g. "Gustona" → Kaufland). Cross-reference `prices.store_id → stores.network_id` grouped by product. De-prioritise these in future fetches since they offer no cross-network price comparison value. Output: list of products with `network_count=1` and the owning network.
- [ ] Add `requirements.txt` — currently only `requests` and `tqdm`; needed for GitHub Actions `pip install` step
- [ ] Add `get_uats(conn)` read helper to `db.py` — both `fetch_prices.py` and `fetch_gas_prices.py` run the same `SELECT id, name, center_lat, center_lon FROM uats` query inline; moving it to `db.py` keeps DB queries in one place
- [ ] **Short network IDs config file** — current network names from the API are verbose (`MEGA IMAGE SRL`, `CARREFOUR`, `PROFI`, etc.) and inconsistent in length. Create a static config (e.g. `config/networks.json`) that maps `retail_networks.id → short_name` (e.g. `"MEGA IMAGE SRL" → "Mega"`, `"CARREFOUR" → "Carrefour"`). Use these short names in analysis outputs, map popups, and future UI. Populate manually — only ~10 retail networks and ~6 gas networks.
- [x] add disclaimer: Acesta nu este un proiect oficial al Guvernului României. Date preluate de pe [monitorulpreturilor.info](https://monitorulpreturilor.info/)
    - [x] add dismissable notification in header (Phase 1 UI redesign)
- [ ] **Short network display name for stat tiles** — the first stat tile on the homepage shows the raw API name ("LIDL DISCOUNT SRL") which is verbose and 3 lines on mobile. Map to short names (e.g. "Lidl") using a lookup dict in `generate_site.py`; same dict used for the price-index spread chart labels. Requires `config/networks.json` (see Short network IDs config file item above).
