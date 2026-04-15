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
- [ ] Investigate multi-point sweeping per UAT for better store coverage
- [ ] Add `fetch_prices.py` progress persistence (checkpoint file) so interrupted runs resume from where they left off instead of skipping already-fetched data
- [ ] **Price variability analysis across networks** — product IDs are shared across networks so cross-network comparison is feasible. Blockers: (1) unit field is dirty (`Kg/K/kg`, `BUC/BUCATA/Buc/Buc.`, `Litru/L/l`) — normalize before comparing; (2) compare only within same normalized unit to avoid package-size noise; (3) exclude or flag SELGROS — it's B2B wholesale, bulk sizes make it incomparable to consumer chains. After normalization: compute min/max/spread/ratio per `(product_id, unit_normalized)` grouped by network. Also run intra-network consistency check (same product, same network, different stores — should be near-zero variance; outliers flag data quality issues).
- [ ] **Check price variability before scraping all stores** — with 50 stores per UAT × 20 UATs, it's unclear whether prices actually differ meaningfully between individual stores in the same network, or even across networks. Before committing to a full scrape, analyse existing data: compute price variance per product grouped by (UAT, network) and across networks within the same UAT. If intra-network variance is near zero, scraping one store per network per UAT is sufficient and would cut request volume dramatically.

---

## Gas

### Todo

- [ ] **Compile full UAT list** — same issue as retail: `Gas/GetUATByName` (no params) returns only top UATs. Use `?uatname=` search or a static list to cover all municipalities.
- [ ] Investigate `GetGasItemsByRoute` endpoint — enables price comparison along a driving route (start/end/mid route point IDs), which could be a useful feature for the UI.
- [ ] Add services data (`GetGasServicesFromCatalog`) — currently skipped; would allow filtering stations by amenities (ATM, car wash, restaurant, etc.)

---

## General

### Todo

- [ ] Build UI — monitor price variations for both retail and gas
- [ ] Set up automated daily fetch (cron / launchd) for both pipelines
- [ ] **Plan GitHub Actions workflow** — evaluate using GH Actions (free tier cron) as an alternative to local cron/launchd for daily fetching; consider artifact/DB storage strategy (commit DB to repo vs. upload to object storage vs. keep local only)
- [ ] Analyse price data — cheapest basket per city, price trends over time
- [ ] **Verify `price_date` rotation cadence** — the schema supports full price history (UNIQUE on `product_id, store_id, price_date`), so trends are preserved across days. However, `price_date` comes from the API, not the fetch timestamp. If the API serves stale data with yesterday's date, a real price change on the same day would be silently dropped (ON CONFLICT does nothing to the price column). Spot-check a few products over consecutive fetches to confirm the API rotates `price_date` daily and doesn't lag behind.
- [ ] **Manual cross-check against retailer online catalogues** — spot-check products and prices against the public websites/apps of major networks (Kaufland, Lidl, Carrefour, etc.) to validate API data quality: are product names consistent? Are prices current? Are any products missing? Useful before investing in heavier analysis or a UI. See:  [shop-scraper](https://github.com/pax/shop-scraper), [midas](https://github.com/pax/midas), [midas-cc](https://github.com/pax/midas-cc)
- [ ] **Product/brand word-frequency analysis** — run word statistics over all product names to surface the most common brands and product terms. Useful for: identifying dedicate/private-label brands, spotting near-duplicate products listed under slightly different names, and deciding which products to prioritise for more frequent fetching. Could be a small standalone script that queries `products.name` and outputs ranked token counts.
- [ ] **Brand-to-network affinity analysis** — extend `analyse_products.py` to add a `networks` column to `brands.csv`: for each brand, show which networks carry it and what share of its price rows each network accounts for. Brands appearing in only one network are private-label candidates; brands heavily skewed toward one network are de-facto exclusives even if technically available elsewhere. Query: `prices JOIN stores GROUP BY brand_key, network_id`.
- [ ] **Near-duplicate product detection** — some products are likely the same item listed under slightly different names (e.g. "Cafea Jacobs 250g" vs "Jacobs Cafea 250 G"). Explore fuzzy string matching (e.g. token-sort ratio) on `products.name` within the same category to surface candidate duplicates for manual review or merging.
- [ ] **Network-exclusive brand/product detection** — identify products or brands that appear in only one retail network (likely private-label / store-brand exclusives, e.g. "Gustona" → Kaufland). Cross-reference `prices.store_id → stores.network_id` grouped by product. De-prioritise these in future fetches since they offer no cross-network price comparison value. Output: list of products with `network_count=1` and the owning network.
- [ ] Add `get_uats(conn)` read helper to `db.py` — both `fetch_prices.py` and `fetch_gas_prices.py` run the same `SELECT id, name, center_lat, center_lon FROM uats` query inline; moving it to `db.py` keeps DB queries in one place
- [ ] **Short network IDs config file** — current network names from the API are verbose (`MEGA IMAGE SRL`, `CARREFOUR`, `PROFI`, etc.) and inconsistent in length. Create a static config (e.g. `config/networks.json`) that maps `retail_networks.id → short_name` (e.g. `"MEGA IMAGE SRL" → "Mega"`, `"CARREFOUR" → "Carrefour"`). Use these short names in analysis outputs, map popups, and future UI. Populate manually — only ~10 retail networks and ~6 gas networks.
