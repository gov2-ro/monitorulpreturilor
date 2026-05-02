# Activity Log

---

## Retail

### 2026-05-02 — Diagnosed 75-hour fetch cycle; fixed cron schedule

**Problem:** healthchecks.io reported "last ping 3 days 7 hours ago". `fetch_prices.py` (PID 152866) had been running since May 1 04:00 UTC — 32+ hours — with ETA of 34 more hours.

**Root cause:** `fetch_reference.py` ran Monday 2026-04-28 and grew the product catalog from ~20K → 86,994 items. This raised batches/anchor from ~100 → 435 (200 products/batch, API hard-limits at 200). At 1.3 s/batch × 480 anchor stores = **~75 hours per full cycle**. The API returns 404 for any request with >200 product IDs in the CSV.

**Secondary cause:** The May 1 run resumed the unfinished April 27 checkpoint (status=in_progress, 192,760 done keys). It used `fetched_at=2026-04-27` metadata for all inserted prices, but `price_date` comes from the API so actual price dates are correct. No data integrity issue.

**Current state at diagnosis:** 93% complete (193K / 208K keys); run finished ~May 2 17:00 UTC. DB is 5.6 GB (backfill populated `prices_current` with 12.3M rows; VACUUM not yet run; 0 freelist pages so VACUUM won't reduce size without deleting rows).

**Fix applied:** Changed cron from `0 4 */2 * *` (every 2 days) to `0 4 * * *` (daily) and added `--max-runtime 82800` (23 hours). The script already has max-runtime support: it saves the checkpoint and exits 0 on timeout, so `&&` proceeds to `fetch_gas_prices.py` and the healthchecks.io ping fires every day. A full 480-anchor cycle now spreads across ~3–4 daily runs; the checkpoint/resume mechanism handles continuity.

**Backlog added:** Per-anchor network-aware product filtering (estimated 2–4× speedup) and ghost product cleanup (12,372 products never return prices — ~17% of catalog).

### 2026-04-28 — DB size optimization: change-based deduplication + price analysis

**Problem:** prices.db grew to 3.66GB in 15 days of VPN-based fetching with ~23M rows. Root cause: the API's `price_date` field (retailer's last update timestamp) increments daily even when prices don't change. The current schema (`UNIQUE(product_id, store_id, price_date)`) creates a new row for every date tick, even for unchanged prices.

**Solution:** Implemented change-based deduplication pattern (Step 2 of 4):
- Added `prices_current` table (UNIQUE on `product_id, store_id`) to hold the current snapshot
- Modified `insert_price()` to check if price+promo have actually changed:
  - If unchanged: only UPDATE `last_checked_at` in prices_current (no changelog row)
  - If changed: INSERT to prices (changelog) + UPSERT prices_current
- Expected row reduction: 5-7× if prices are stable 5+ days/week
- The `prices` table becomes a true changelog; `prices_current` is the denormalized snapshot for fast lookup

**Supporting changes:**
- New `analyze_prices.py` script to analyze price uniformity per (product, network, date) group with ≥3 stores
- Identifies % of groups with uniform pricing vs. variance distribution
- Output: summary stats + `docs/price_uniformity.csv` for drill-down

**Next steps:**
- Backfill `prices_current` from existing prices (one-time migration, deferred)
- Update `fetch_prices.py` to use new insert_price() logic (already compatible)
- Monitor DB size on next fetch cycles; expect sub-1GB for 30+ days if dedup works as planned
- Step 3 (optional): normalize high-cardinality text columns (brand, unit, retail_categ) if size still an issue after Step 2

**Technical notes:**
- DB corruption (101 integrity errors in B-tree) discovered during analysis; recovery attempted but full migration deferred due to SQLite .dump format complexities
- The recovered DB (319-809MB clean vs. 3.4GB corrupted) suggests bloat from transaction journals and invalid index pages
- Corruption does not prevent write operations going forward; insert_price() changes are safe for new data

---

## General

### 2026-04-19 — Phase 1 UI Redesign (editorial homepage + design system)

Complete redesign of the static site toward an editorial data-journalism aesthetic (Datawrapper / old FiveThirtyEight / Pudding ethos). All 18 existing pages preserved; 1 new page added (`tablou.html`).

**What changed:**
- `docs/assets/app.css` — new ~500-line design system: CSS custom property tokens (paper/ink palette, rust accent, 6-hue chart palette), fluid type scale (`clamp()`), Fraunces + IBM Plex Sans + IBM Plex Mono font stacks, spacing grid, and components: `.masthead`, `.nav`, `.lede` (drop cap), `.section-title` (auto-numbered §01–§N), `.stats`/`.stat`, `.chart-block`, `.spread-chart`, `.story-grid`/`.story`, `.tool-grid`/`.tool`, `.strip`, `.disclaimer`, `.footer`.
- `docs/assets/charts.js` — Chart.js 4 defaults: paper palette, no animations, tabular numerals in tooltips, horizontal grid only, legend at bottom.
- `docs/assets/logo.svg` — rust circle + Fraunces wordmark + v2 badge.
- `generate_site.py`: new `NAV_ITEMS` (13 items, 2 separators), `nav_html()`, `page_shell()` (external CSS, skip link, masthead, disclaimer, footer), `_masthead()`, `_disclaimer()`, `_footer()`, `date_ro()` helpers, `FONTS_HEAD` (Google Fonts preconnect). Old `gen_index` renamed to `gen_tablou` (→ `tablou.html`). New `gen_index` produces "Buletinul prețurilor" editorial homepage: lede with spread-chart (no canvas), 4 stat tiles, 3 story cards, 6 tool cards, compact strip.

**Decisions:**
- Stack A kept (Python → static HTML, no bundler), editorial-led positioning chosen. Options doc saved in `docs/design-notes/2026-04-19-ui-redesign-options.md`.
- Google Fonts CDN used for Phase 1 speed; self-hosting deferred to Phase 5.
- Hero chart = CSS-only spread-chart (no Chart.js), so it renders with JS disabled.
- All old URLs preserved; old `gen_index` body lives on as `gen_tablou`.

### 2026-04-18 — API Endpoint Discovery

Wrote `explore_api.py` and probed 50+ candidate endpoints systematically. Strategy: WCF metadata first (WSDL/MEX), then pattern-based candidates, then known-endpoint variations.

Key findings:
- **`GetCatalogProductsByNameNetwork` (no params)** returns 87,448 product names (16.5 MB) — a full catalog dump. No category IDs, but names + IDs enable a client-side search index. Our current pipeline only indexes 6,932 products (monitored categories only).
- **`GetStoresForProductsByUat`** confirmed working — tested Bucharest UAT, returns 50 stores. Supports `csvnetworkids` filter not available on the ByLatLon variant. Currently unused in our pipeline.
- **`GetGasItemsByRoute`** endpoint exists but crashes server-side (AutoMapper bug). Tested with real UAT `route_id` values; the server accepts params but fails during response mapping. Not usable until API owners fix it.
- No price history API exists anywhere. Our SQLite DB is the only historical record.
- WSDL/MEX not exposed; no swagger/help. Manual probing is the only discovery path.
- All other guessed endpoints (store details, brands, promos, history variants) return 404.

Results documented in `docs/reference/undocumented-endpoints.md`. Backlog updated.

### 2026-04-18 — Phase D: Aproape de tine — Geolocation Store Finder

- Added `build_stores_index.py`: emits `docs/data/stores_index.json` (2,624 consumer-network stores with coordinates, compact array format, 319 KB). Joins basket camara per-UAT cheapest cost (national fallback 302.61 lei/lună where UAT not scored). B2B (SELGROS) excluded.
- Added `gen_aproape()` + `aproape.html`: Leaflet map + store card grid. Browser GPS button + **manual lat/lon inputs** (for users outside Romania or with GPS disabled — default pre-filled to Bucharest centre 44.4268, 26.1025). Radius slider (1–50 km), network filter dropdown populated from data. Store cards show distance, network color badge, address, basket cheapest cost for the store's UAT. Map markers colored by network. Cap at 200 displayed results; shows total count above.
- Added "Aproape" to nav. Wired `build_stores_index.py` into CI daily run (runs after basket build).
- **Verified:** 175 stores found within 5 km of Bucharest centre, map + cards render correctly, network color coding works, status message shows manual coordinates.

### 2026-04-18 — Phase C: CPI prototype, Stories, Open Data Hub, Methodology

- **#10 Metodologie & Transparență** (`metodologie.html`): live snapshot grid (products, stores, networks, price rows, dates, gas stats), API endpoint table with limits, known-gaps warning cards (fresh produce absent, 1367 stores with NULL network, 723 products without today's price, 7-day retail history), methodology explanations for each calculator (basket, anomalies, categories, choropleth, price index), code/license card. Data pulled at site-gen time via `load_metodologie_stats()`.
- **#9 Date Deschise** (`date-deschise.html`): 9 downloadable datasets with format badge, file size, freshness, schema description, and direct download link. CC BY 4.0 license. Covers anomalies JSON, 4 basket JSONs, UAT GeoJSON, category index, 3 analytics CSVs.
- **#4 Indice de Inflație Civică — prototype** (`inflatie.html` + `build_cpi.py`): tracks national cheapest-network basket cost per available price_date (7 days); Chart.js multi-line trend per basket; product change table (first vs last date, sorted by abs % change). Heavy "PROTOTIP" labeling + yellow caveat banner + methodology card. Day-to-day swings (e.g. cămară 335→267 lei) reflect coverage variation as much as price changes — caveated explicitly. Wired into CI via `build_cpi.py --db ... --out docs/data/cpi.json`.
- **#8 Povești cu Date — prototype** (`povesti.html`): 5 auto-generated story cards built from today's anomaly + basket + category data (no historical trends needed). Stories: biggest spread today, network cheapest most often (Profi: 77% of compared products), basket savings opportunity (+59.70 lei/lună if choosing wrong network), category with most total spread (Cofetărie: 1,277 lei), products with ≥3× ratio (127 products, 1,738 lei combined savings). All link to relevant pages. Fully client-side, updates daily with data.
- All 4 pages added to nav; `build_cpi.py` added to CI daily run.

### 2026-04-18 — Harta Costurilor — choropleth map (Phase B #2)

- Added `config/geo/ro-uats.topojson` (source file, 706 KB — Romania UAT polygons, 3175 features, SIRUTA join key). 365/366 DB UATs matched by SIRUTA code; all 835 store-UATs matched.
- Added `build_uat_geojson.py`: decodes TopoJSON manually (arc stitching from spec — the `topojson` Python library fails on null-geometry features), joins DB stats (store count, consumer network count — queried directly from stores, not via the uats table which only covers 366/835 UATs), joins basket cheapest/priciest monthly cost from `docs/data/baskets/camara.json` per-UAT data (national fallback for 674/834 UATs not yet scored at local level). Outputs `docs/data/uats.geojson` (834 features, 475 KB, only store-UATs).
- Added `gen_harta()` + `harta.html`: MapLibre GL JS v4 choropleth over CARTO Positron basemap. Three layer toggles: networks present (food-desert detection — red=1 network, green=5+), basket monthly cost (green=cheap, red=expensive), store count (blue scale). Hover highlight + click → side panel with UAT name, network count, store count, basket min/max, cheapest network. Responsive (420px height on mobile).
- KPIs: 398 localities with identified networks, 218 single-network "food deserts", cheapest basket locality = Municipiul Baia Mare 122.26 lei/lună.
- **Verified.** Layer toggle (networks → basket cost) works. Click on "Valea Doftanei": panel shows 1 store, 0 identified networks, national basket fallback 302.61–362.31 lei/lună, Profi cheapest.
- Wired `build_uat_geojson.py` into CI daily run (runs after basket build so baskets data is ready).

### 2026-04-18 — Category Explorer (Phase B #6)

- Added `build_categories.py`: for the latest price_date, groups products by their category (level-2 of the 143-node tree — all 6932 products attach directly there), computes per-category spread rankings using the same outlier filter and B2B exclusion as the anomaly feed, emits `docs/data/categories/index.json` + one JSON per category (up to 200 products each ranked by ratio desc). 7 categories have meaningful multi-network price data today; the other 136 categories have no prices (API tracks shelf-stable goods only — meat, dairy, fresh produce categories are empty).
- Added `gen_categorii()` + `categorii.html` page: category tabs with product counts, KPI summary (comparables count, top spread, total potential savings), network leaderboard (how many products each network prices cheapest in the category — Profi wins 146/200 in Panificatie), product card grid with search + sort (ratio/lei/pct) + min-networks filter, paginated at 24. Each card links to compare.html?pid=X.
- Added "Categorii" to nav (4th position). Wired `build_categories.py` into CI daily run.
- **Verified.** Tab switch (Panificatie → Cafea), search "lavazza" → 9 products, all correctly filtered. Mobile (375px): tabs wrap to 4 lines, KPI cards stack single-column — all readable.

### 2026-04-18 — CI: wire baskets + anomalies builders into daily run

- Added `--db` and `--out` CLI args to `build_baskets.py` and `build_anomalies.py` (both previously hard-coded to `data/prices.db`).
- Added two CI steps after analytics export: `build_baskets.py` and `build_anomalies.py`, both pointed at `data/prices_ci.db`. Daily refresh of `docs/data/baskets/*.json` and `docs/data/anomalies_today.json` is now automatic.
- Updated commit step to git-add the new HTML pages (`cos.html`, `anomalii.html`) and the new JSON outputs. Also added the previously-missing `compare.html`, `analytics.html`, and `docs/data/products/*.csv` to the add list — they were being regenerated in CI but not committed (`git add` was incomplete). Confirmed by running both builders locally on the live DB after the refactor.

### 2026-04-18 — Anomalii de preț — daily cross-network spread feed

- Added `build_anomalies.py`: for the latest `price_date`, computes per-network min price for each product, drops outliers ([0.30, 3.0]× of cross-network median per product — same filter as baskets), keeps products with ratio ≥ 1.5, ranks by ratio desc, writes top 300 to `docs/data/anomalies_today.json` (101 KB). SELGROS excluded (B2B).
- Added `gen_anomalii()` + `anomalii.html` page: KPI summary (count, biggest spread, top-10 potential savings), filters (search, category, cheapest-at network, min ratio threshold), card list with cheapest→priciest flow, savings callout, ratio chip, expandable per-network chip row, link to compare.html?pid=… for each product. Pagination at 30 cards/page.
- Added "Anomalii" to nav, third position after Coșul.
- **Verified.** Top anomaly: Lavazza Qualita Rossa 250g, Kaufland 5.75 lei vs Mega 39.09 lei = 6.8× = +33.34 lei savings. SQL spot-check confirms: Kaufland has the SKU at 5.75 across 11 stores (deep promo), Mega at 39.09 across 256 stores (full price). Real, useful signal — exactly the kind of leak the feed should catch. Outlier filter passed in this case because the median across networks (≈16.67) keeps the 0.30× threshold at 5.0 — promos survive, true data errors don't.
- Mobile (375×812): cards stack, savings line wraps below product info, filters go full-width. Filters tested: search "cafea" → 25 results; min-ratio 3× → 127 results.

### 2026-04-18 — Coșul de Cămară (Phase A: foundations + basket calculator)

- **Foundations.** Added `units.py` (normaliser for messy `prices.unit` strings → 'kg'|'L'|'buc'|None, 99.6% coverage of 2.25M rows) and `networks.py` + `config/networks.json` (short display names + B2B flag for the 10 retail / 7 gas networks; SELGROS flagged B2B and excluded from consumer comparisons). Network IDs in the API are inconsistent (some are slugs like `PROFI`, others are barcodes like `5940475006709` for Carrefour) — the JSON config + `short()` / `is_b2b()` helpers give the rest of the codebase one place to look.
- **Curated baskets.** `config/baskets.json` defines 4 baskets (Cămară 11 items, Student 8, Copt 8, Sărbători 9 — 38 distinct SKUs). Each item lists 1+ substitute `product_ids` so the builder can pick the cheapest available at a given network/UAT. SKUs were filtered to those carried by ≥7 networks today. Honest framing: API only tracks shelf-stable goods, so these are *pantry* baskets (no fresh dairy, meat, produce) — copy and disclaimer say so.
- **Builder.** `build_baskets.py` scores each basket nationally and per UAT: cheapest substitute per item per network → `weekly_cost`, `monthly_cost = weekly × 52/12`. `comparable` flag requires ≥50% items found at the network (protects ranking from missing-data networks). Outlier filter drops prices outside [0.30, 3.0]× cross-network median per product — surfaced after Cora artificially won Cămară due to 3 stores selling 1L Floriol oil at 0.50 lei (data-source error). After filter, PROFI is genuine #1 nationally at 302.61 lei/lună. Outputs `docs/data/baskets/index.json` + 4 per-basket files (~70 KB each, all UATs in one payload, lazy-loaded by tab).
- **UI.** New `cos.html` page with tabbed basket switcher, UAT picker (national or per-locality), hero KPI ("how much extra you'd pay at the priciest network vs the cheapest"), ranked network table with bars and items-found counts, and per-product drill table showing the cheapest price per network with the chosen substitute highlighted. Added "Coșul" as second nav item across the site.
- **Verified.** SQL spot-check reproduced Profi/Cluj-Napoca/Cămară at 160.82 lei/lună exactly (10/11 items found — bread missing in Cluj's PROFI feed). Mobile (375×812) renders cleanly. Tab switch (Cămară → Student) and UAT switch (national → Cluj-Napoca) both work in browser.

### 2026-04-16 — Gas price spread analysis + dashboard fuel trends

- Confirmed gas price variation: premiums vary ~1 RON across networks, benzine ~0.62 RON, GPL only 0.14 RON (smallest). Electric charging has the widest spread (26+ RON). The earlier screenshot showing GPL variation was misleading — based on only 20 UATs.
- Fixed `load_fuel_trends()`: was grouping by raw API timestamp (each station's individual `Updatedate`), now groups by calendar day (`SUBSTR(price_date, 1, 10)`). Trend charts now show one point per day per network.
- Added fuel trend chart to `index.html` dashboard: full-width card below the existing KPIs, fuel type tabs, one line per network, updates daily with CI data.
- Added "Diferență" (spread) column to `fuel.html` table: shows max−min per network inline.
- Added `discover_gas_stations.py`: probes `GetGasItemsByLatLon` from 1842 populated locality centroids (same strategy as `discover_stores.py`). Upserts discovered stations + their UAT IDs; `fetch_gas_prices.py` picks up new UATs automatically. Full run ~73 min; checkpoint/resume. Added `ensure_uat()` helper to `db.py` (`INSERT OR IGNORE`) to avoid clobbering existing UAT data.
- Added cross-link "Hartă Carburanți" to `stores_map.html` top-bar.

### 2026-04-16 — Gas station map + gas pipeline in CI

- Added `gen_gas_map()` and `load_gas_map_data()` to `generate_site.py`: generates `docs/gas_map.html` — Leaflet map of 413 gas stations, markers coloured by network, popup with full price table (all available fuel types + date), network filter legend. Reuses `GAS_COLORS` and `net_color()` already defined.
- Added "Hartă Carburanți" to `NAV_ITEMS` — appears in nav across all generated pages.
- Added gas steps to `.github/workflows/ci_prices.yml`: daily `fetch_gas_prices.py --max-runtime 900` (runs after retail fetch, well within 2h CI limit); weekly `fetch_gas_reference.py` on Mondays. Gas checkpoint and `gas_map.html` added to commit step.
- Added backlog item: highway/road gas station discovery via lat/lon grid probe (city-based coverage now handled by `discover_gas_stations.py`).
- Note: gas coverage limited to 20 UATs from initial setup. Run `python discover_gas_stations.py` (~73 min, checkpoint/resume) to expand to all city-area stations; `fetch_gas_prices.py` picks up newly added UATs automatically. Highway stations require a grid probe (see backlog).

### 2026-04-16 — Analytics page + SQLite views + product CSVs for compare tab

- Added 7 analytical views to `db.py` (created by `init_db()`, idempotent): `v_price_variability`, `v_cross_network_spread`, `v_product_popularity`, `v_private_label_candidates`, `v_stores_per_network`, `v_price_freshness`, `v_products_no_prices`.
- Added `export_analytics.py`: dumps all views to `docs/data/*.csv` (stdlib only); wired into CI after `generate_site.py`; CSVs committed to repo.
- Added `analytics.html`: 7-tab page (one tab per view), client-side sortable columns, row count, per-tab description, CSV download link per tab. Added to nav between Carburanți and Pipeline.
- Compare tab (`compare.html`) loads per-product CSVs from `docs/data/products/{id}.csv` rather than embedding all data as JSON; CSVs generated by `export_analytics.py`. Fixed `.gitignore` — `docs/data/*` was blocking `docs/data/products/*.csv` (subdirectory not un-ignored by `!docs/data/*.csv`); added explicit negation for the subdirectory.
- Added `--products-order` flag to `fetch_prices.py` (`db` | `stale`; default `db`). Stale mode sorts products by oldest `MAX(fetched_at)` ASC, never-fetched first — fills coverage gaps before re-fetching fresh products. Checkpoint saves ordered product IDs in stale mode for stable mid-run resume.

### 2026-04-16 — Trend & Comparison Dashboard (Phase 2)

- Added `trends.html` — time-series line charts: Network Price Index over time (one line per network), category average prices over time (tab per category), fuel placeholder (auto-shows when gas data lands in CI). Graceful degradation when <2 dates available.
- Added `compare.html` — product-level cross-network comparison: dropdown (grouped by category), bar chart of latest prices, trend line chart per network, ranked table with avg/min/max. All data embedded as JSON (224 KB in CI, ~3.7 MB from full DB).
- Both pages added to nav; stores_map hardcoded nav also updated.
- No schema changes — DB already accumulates rows by date (UNIQUE on product+store+date). `git-history` not needed; `prices_ci.db` itself is the history.

### 2026-04-16 — Static GitHub Pages UI (Phase 1)

- Created `generate_site.py`: generates 5 static HTML pages into `docs/` from `data/prices.db`:
  - **index.html** — Dashboard with KPI cards (stores, products, prices, gas stations), Network Price Index bar chart, cheapest fuel summary, latest dates.
  - **price-index.html** — Network Price Index: overall ranking + per-category breakdown with tab selector. Normalized to 100 = cheapest network, computed on products available in 3+ networks.
  - **fuel.html** — Fuel Price Leaderboard: per-fuel-type tabs, horizontal bar chart + sortable table (avg/min/max/stations per network).
  - **pipeline.html** — Pipeline health: KPI cards, coverage-by-network table with % bars, run history from `runs` audit table.
  - **stores_map.html** — Enhanced store map: network filter checkboxes (show/hide per network), visible count display, floating nav bar. Replaces old `generate_map.py` output.
- Design: clean card-based responsive layout, Chart.js for charts, Leaflet + MarkerCluster for map, all data embedded as JSON (~600 KB total, under 2 MB target).
- Supersedes `generate_map.py` (still functional but `generate_site.py` produces the enhanced version).
- Fixed: category query used `parent_id IS NULL` but top-level categories have `parent_id = 1` (virtual root).

### 2026-04-15 — Optimise fetch_prices.py: spatial clustering + larger batches

- Added greedy set-cover spatial clustering to `fetch_prices.py`: groups stores within 5 km and picks one anchor per cluster. Reduces 3,813 stores → 681 anchors (82% fewer API calls).
- Raised `BATCH_SIZE` from 50 to 200 products/request (API-tested; 500 hits URL-length 404). Cuts batches from 139 to 35 per anchor.
- Combined effect: 530k → ~24k requests, ~22h → ~1h (95% reduction).
- Added `--no-cluster` flag as escape hatch to revert to per-store querying.
- Clustering runs in <1s on 3,813 stores (O(n²) with lat/lon pre-filter).
- `INSERT OR IGNORE` on prices means overlapping coverage from neighboring anchors is harmless — no data loss or duplication.

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
