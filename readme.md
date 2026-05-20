# Monitorul Prețurilor

[monitorulpreturilor.gov2.ro](https://monitorulpreturilor.gov2.ro/) – interfață alternativă pentru [monitorulpreturilor.info](https://monitorulpreturilor.info/)

Preia și stochează date despre prețurile alimentelor și ale combustibililor de pe [Monitorului Prețurilor](https://monitorulpreturilor.info/), proiect al [Consiliului Concurenței](https://www.consiliulconcurentei.ro/) 

> Proiectul *Monitorul Prețurilor* produselor alimentare își propune să acorde consumatorilor posibilitatea de a compara prețul aferent coșului de produse a cărui achiziție intenționează să o realizeze.


---


## Pipeline overview

**Fetch** (writes to DB):
```
fetch_reference.py          # once / weekly — retail reference data
  └─ fetch_prices.py        # daily — retail prices (per-store, population-ordered)
       └─ build_price_flags.py   # daily — price quality flags (outlier, spike, promo)

fetch_gas_reference.py      # once / weekly — gas reference data
  └─ fetch_gas_prices.py    # daily — gas prices

discover_stores.py          # one-shot — store discovery from locality centroids
  └─ update_store_populations.py   # after discovery — enrich stores with population
```

**Build** (DB → site/data/, run after fetch, before site generation):
```
build_baskets.py            # basket costs per network + UAT → site/data/baskets/
build_anomalies.py          # price-anomaly feed → site/data/anomalies_today.json
build_categories.py         # per-category spreads → site/data/categories/
build_cpi.py                # basket cost trend → site/data/cpi.json
build_stores_index.py       # store geolocation index → site/data/stores_index.json  (needs baskets)
build_uat_geojson.py        # UAT choropleth data → site/data/uats.geojson            (needs baskets)
```

**Site generation** (site/data/ → site/*.html):
```
generate_site.py            # static site (9 HTML pages + per-product CSVs)
generate_pipeline_report.py # pipeline health page → site/pipeline-health.html
generate_map.py             # standalone Leaflet store map → dashboard/stores_map.html
export_analytics.py         # export DB views to site/data/*.csv
```

**Analysis** (standalone, read-only):
```
analyse_prices.py           # price variability: intra-network + cross-network CSVs
analyse_products.py         # brand/word frequency + category anomaly CSVs
analyze_prices.py           # price uniformity: % of uniform-price groups per network
```

**CI helpers:**
```
build_ci_subset.py          # build store + product ID subsets for GitHub Actions
```

---

## Workflow

### First-time setup

```bash
# 1. Retail reference data (networks, UATs, categories, products)
python fetch_reference.py

# 2. Discover all stores across Romania
python discover_stores.py

# 3. Enrich stores with surrounding population (used for fetch ordering)
python update_store_populations.py

# 4. Gas reference data (networks, fuel types)
python fetch_gas_reference.py
```

### Daily run

> **Cron layout (VPS):** retail (`fetch_prices.py`) runs every 30 minutes (`*/30 * * * *`) with `--max-runtime 1700` (28 min 20 s), so each slice stays within its window and the lock/checkpoint carry state across firings. Gas (`fetch_gas_prices.py`) runs independently at 03:00. They are **not** chained — a stalled retail run does not block the gas fetch.
>
> **Monitoring:** each cron line is wrapped with `scripts/hc_run.sh <uuid> <cmd>`, which pings healthchecks.io with `/start`, then either the base URL on success or `/fail` on non-zero exit. Each fetcher's line also runs `check_runs.py` afterwards — so a fetch that "completed" but wrote zero records still trips `/fail`. A daily `audit_pipeline.py` at 06:00 checks data quality (store freshness, abandoned runs, network coverage gaps) and fails the same way. See `scripts/crontab.template` for the canonical layout, and [`docs/monitoring-pattern.md`](docs/monitoring-pattern.md) for the reusable approach.
>
> **Logs:** all cron output now writes to `data/logs/` (created on demand, gitignored). Historical logs in `~/g2-dev/logs/` are left in place; new writes go to the project-local path.

```bash
# Fetch
python fetch_prices.py       # retail — resumes from checkpoint if interrupted
python fetch_gas_prices.py   # gas    — resumes from checkpoint if interrupted

# Build (order matters: baskets first, then the rest)
python build_price_flags.py  # price quality flags → DB
python build_baskets.py      # basket costs → site/data/baskets/
python build_anomalies.py    # price anomalies → site/data/anomalies_today.json
python build_categories.py   # category spreads → site/data/categories/
python build_cpi.py          # basket cost trend → site/data/cpi.json
python build_stores_index.py # store index → site/data/stores_index.json
python build_uat_geojson.py  # UAT map data → site/data/uats.geojson

# Generate site
python export_analytics.py         # CSV snapshots → site/data/
python generate_site.py            # HTML pages → site/
python generate_pipeline_report.py # pipeline health → site/pipeline-health.html
```

### Weekly refresh (reference data may change)

```bash
python fetch_reference.py
python fetch_gas_reference.py
python discover_stores.py    # picks up newly opened stores
python update_store_populations.py
```

### Analysis (run any time after prices are loaded)

```bash
python analyse_prices.py     # → data/price_intra_network.csv, data/price_cross_network.csv
python analyse_products.py   # → data/brands.csv, data/product_words.csv, data/category_anomalies.csv
```

### Data files in `data/`

| File | Created by | Purpose |
|------|-----------|---------|
| `prices.db` | `db.py` (via any script) | Main SQLite database — all tables |
| `prices.db-wal` | SQLite (WAL mode) | Write-ahead log; normal when a script is running or was interrupted. Checkpointed automatically on clean close. |
| `prices.db-shm` | SQLite (WAL mode) | Shared-memory index for the WAL; accompanies `prices.db-wal` |
| `prices_checkpoint.json` | `fetch_prices.py` | Retail fetch progress; deleted/reset on fresh run |
| `gas_checkpoint.json` | `fetch_gas_prices.py` | Gas fetch progress; deleted/reset on fresh run |
| `discover_stores_checkpoint.json` | `discover_stores.py` | Store discovery progress; deleted/reset on fresh run |
| `retail_checkpoint.json` | `fetch_prices_by_uat.py` *(legacy)* | Legacy UAT-based fetch progress |
| `price_intra_network.csv` | `analyse_prices.py` | Intra-network price variance |
| `price_cross_network.csv` | `analyse_prices.py` | Cross-network price comparison |
| `brands.csv` | `analyse_products.py` | Normalized brand list |
| `product_words.csv` | `analyse_products.py` | Word/bigram frequency in product names |
| `category_anomalies.csv` | `analyse_products.py` | Products assigned to unexpected categories |
| `discover_gas_checkpoint.json` | `discover_gas_stations.py` | Gas station discovery progress; deleted/reset on fresh run |
| `ci_stores.txt` | `build_ci_subset.py` | Store IDs for CI test runs (one per line) |
| `ci_products.txt` | `build_ci_subset.py` | Product IDs for CI test runs (one per line) |

> **WAL note:** `prices.db-wal` and `prices.db-shm` are safe to leave in place between runs — SQLite manages them. Only delete them if you're sure no script is writing (e.g., after a crash and before restoring from backup).

---

## Scripts

### Retail

#### `fetch_reference.py`
Fetches slow-changing reference data: retail networks, UATs, product categories, and products. Run once, or weekly to pick up new products.

```bash
python fetch_reference.py                  # full run → data/prices.db
python fetch_reference.py --limit 5        # first 5 categories only (for testing)
python fetch_reference.py path/to/db.db    # custom DB path
```

#### `fetch_prices.py`
Fetches current prices for all stores × product combinations, ordered by store population (busiest cities first). Saves progress to `data/prices_checkpoint.json` so interrupted runs resume automatically. Requires reference data — run `fetch_reference.py` first.

Checkpoint behaviour:
- **Interrupted run** → resumes from last saved position on next run
- **Completed run, same day** → exits immediately (no redundant API calls)
- **Completed run, new day** → starts a fresh run automatically
- **`--fresh`** → ignores any checkpoint and starts clean
- **`--resume`** → continues a completed same-day run; skips already-processed store×batch keys, fetches only new stores

> **Sweep duration:** with ~87 K products (437 batches/anchor at BATCH_SIZE=200) and 683 anchors, one full pass takes roughly 7–9 daily cron windows (23 h each). The checkpoint resumes across days using the same `fetched_at` timestamp, so all prices within a single logical sweep are consistent. `price_date` reflects the API-reported store date, not the fetch date.

> **Run history:** every invocation inserts a row into the `runs` table. Rows left as `status='running'` by a prior crash or SIGKILL are automatically marked `'abandoned'` at the next startup.

Product ordering (`--products-order`):
- **`db`** (default) — products in DB insertion order
- **`stale`** — never-fetched products first, then sorted by oldest `fetched_at` ascending. Use with `--max-runtime` to cap daily overhead while always filling coverage gaps first. On interrupted runs the product order is saved in the checkpoint so resume is stable within the same day; the next day's run re-derives staleness from the DB.

```bash
python fetch_prices.py                                           # full run → data/prices.db
python fetch_prices.py --resume                                  # fetch only new stores added since today's run
python fetch_prices.py --products-order stale --max-runtime 3600 # prioritise never-fetched / stale products, stop after 1h
python fetch_prices.py --limit-stores 3 --limit-products 90      # quick smoke test
python fetch_prices.py --fresh                                   # ignore checkpoint, start clean
python fetch_prices.py path/to/db.db                             # custom DB path
```

#### `discover_stores.py`
Discovers retail stores by probing the API from Romanian locality centroids (3 180 localities from the official population CSV). Deduplicates probe points within 4 km to avoid redundant calls. Writes stores to DB; does **not** write prices. Resumes from checkpoint on restart.

Run once (or after a long gap) to populate the `stores` table before running `fetch_prices.py`.

```bash
python discover_stores.py                    # all localities ≥ 2 500 pop
python discover_stores.py --min-pop 5000     # larger threshold
python discover_stores.py --limit 50         # first 50 probe points (quick test)
python discover_stores.py --dry-run          # parse + log, no DB writes
python discover_stores.py --fresh            # ignore checkpoint, restart
python discover_stores.py --debug            # verbose logging
```

#### `update_store_populations.py`
Computes a `surrounding_population` estimate for every store: sums populations of all localities whose centroid falls within `--radius` km (default 10 km). Used by `fetch_prices.py` to prioritise high-traffic stores.

Run once after `discover_stores.py`, or re-run whenever the stores table grows.

```bash
python update_store_populations.py           # default 10 km radius
python update_store_populations.py --radius 5
python update_store_populations.py --debug
```

#### `discover_stores_by_uat.py` *(legacy)*
Earlier store-discovery approach using UAT centroids from the DB (only ~20 UATs loaded by default). Superseded by `discover_stores.py` which covers all 3 180 Romanian localities. Kept for reference.

```bash
python discover_stores_by_uat.py [--pop-threshold 10000] [--debug] [--dry-run]
```

#### `fetch_prices_by_uat.py` *(legacy)*
Earlier price-fetching approach: iterates UAT × product batches using the centroid endpoint. Superseded by `fetch_prices.py` which fetches per-store with population ordering. Kept for reference.

```bash
python fetch_prices_by_uat.py
python fetch_prices_by_uat.py --limit-uats 3 --limit-products 90
python fetch_prices_by_uat.py --fresh
```

---

### Gas

#### `fetch_gas_reference.py`
Fetches gas networks and fuel product types (6 fuel types: benzină/motorină standard & premium, GPL, electric). Run once or weekly.

```bash
python fetch_gas_reference.py              # full run → data/prices.db
python fetch_gas_reference.py path/to/db  # custom DB path
```

#### `fetch_gas_prices.py`
Fetches current fuel prices for all UATs (one request per fuel type per UAT). Saves progress to `data/gas_checkpoint.json`. Same checkpoint behaviour as the retail pipeline.

```bash
python fetch_gas_prices.py                         # full run → data/prices.db
python fetch_gas_prices.py --limit-uats 3          # quick smoke test
python fetch_gas_prices.py --fresh                 # ignore checkpoint, start clean
python fetch_gas_prices.py path/to/db              # custom DB path
```

#### `discover_gas_stations.py`
Discovers gas stations by probing `GetGasItemsByLatLon` from populated locality centroids (≥ 2 500 pop by default). Uses Motorină standard (fuel type 21) to find all stations within 5 km. Resumes from `data/discover_gas_checkpoint.json`.

Run once (or periodically) before `fetch_gas_prices.py` to populate the `gas_stations` table.

```bash
python discover_gas_stations.py                  # all localities ≥ 2 500 pop
python discover_gas_stations.py --min-pop 5000   # larger threshold
python discover_gas_stations.py --limit 50       # first 50 probe points (quick test)
python discover_gas_stations.py --dry-run        # no API calls or DB writes
python discover_gas_stations.py --fresh          # ignore checkpoint
python discover_gas_stations.py --debug          # verbose logging
```

---

### Analysis

#### `analyse_prices.py`
Analyses retail price variability. Produces two CSVs in `data/`:

| Output | Contents |
|--------|----------|
| `price_intra_network.csv` | Per (product, network): min/max/avg/CV/store count — flags chains with non-uniform pricing |
| `price_cross_network.csv` | Per product: cheapest/most-expensive network, spread, ratio, price per network |

Unit normalization collapses `kg/Kg/1kg` etc. to a common bucket. SELGROS (B2B wholesale) is excluded from cross-network rankings by default.

```bash
python analyse_prices.py
python analyse_prices.py --min-stores 3        # require at least 3 stores per group
python analyse_prices.py --include-selgros     # include SELGROS in rankings
python analyse_prices.py --debug
```

#### `analyze_prices.py`
Analyses price uniformity: for each `(product, network, date)` group, counts distinct prices across stores. Reports what percentage of groups are uniformly priced and which products have the highest within-network variance. Outputs `site/price_uniformity.csv`.

```bash
python analyze_prices.py
```

#### `analyse_products.py`
Analyses product names and brands. Produces three CSVs in `data/`:

| Output | Contents |
|--------|----------|
| `brands.csv` | Normalized brands with raw variants, counts, parent categories |
| `product_words.csv` | Most common words and bigrams in product names (diacritic-insensitive) |
| `category_anomalies.csv` | Products whose brand dominates one category but the product is assigned elsewhere |

```bash
python analyse_products.py
python analyse_products.py --db path/to/prices.db --top 200
python analyse_products.py --anomaly-threshold 0.85
```

---

### Build

These scripts transform the raw DB data into JSON/CSV files consumed by the site. Run them after the daily fetch and before site generation. `build_baskets.py` must run first — other scripts read its output.

#### `build_price_flags.py`
Persists price quality flags to the `price_flags` table in the DB. Run after each daily fetch. Safe to re-run (uses `INSERT OR IGNORE`). Flags: `outlier_price` (median + MAD), `price_spike` (day-over-day jump), `promo_too_deep` (>90% discount).

```bash
python build_price_flags.py
python build_price_flags.py --db path/to/prices.db
```

#### `build_baskets.py`
Scores each curated basket (from `config/baskets.json`) at every retail network nationally and per UAT — picking the cheapest substitute SKU for each item. Outputs basket cost data used by the Coșul de Cămară page, and as input to `build_stores_index.py` and `build_uat_geojson.py`.

```bash
python build_baskets.py                      # → site/data/baskets/
python build_baskets.py --out path/to/dir
python build_baskets.py --db path/to/db
```

#### `build_anomalies.py`
For the latest price snapshot, finds products whose priciest-network price is ≥1.5× their cheapest-network price. Drives the Anomalii page ("save X lei by buying at Y instead of Z"). Excludes B2B networks and applies the same outlier filter as `build_baskets.py`.

```bash
python build_anomalies.py                    # → site/data/anomalies_today.json
python build_anomalies.py --out path/to/file
```

#### `build_categories.py`
For the latest price snapshot, ranks products within each category by cross-network price ratio. Drives the Category Explorer page.

```bash
python build_categories.py                   # → site/data/categories/
python build_categories.py --out path/to/dir
```

#### `build_cpi.py`
Tracks the national cheapest-network basket cost for each curated basket across all available price dates. Drives the price trend chart. Becomes more meaningful as historical data accumulates.

```bash
python build_cpi.py                          # → site/data/cpi.json
python build_cpi.py --out path/to/file
```

#### `build_stores_index.py`
Emits a compact store list with coordinates, network, and UAT basket cost for each store — used by the Aproape de tine geolocation page. Requires `build_baskets.py` output.

```bash
python build_stores_index.py                 # → site/data/stores_index.json
python build_stores_index.py --out path/to/file
```

#### `build_uat_geojson.py`
Decodes the Romania UAT TopoJSON (`config/geo/ro-uats.topojson`) and joins it with DB store counts and basket costs per UAT. Drives the choropleth map. Keeps only UATs that have at least one retail store. Requires `build_baskets.py` output.

```bash
python build_uat_geojson.py                  # → site/data/uats.geojson
python build_uat_geojson.py --out path/to/file
```

---

### Site generation

#### `generate_site.py`
Generates the full static site from the database — 9 HTML pages (dashboard, price index, fuel leaderboard, pipeline health, store map, trends, compare, analytics, gas map) plus per-product CSVs in `site/data/products/`.

```bash
python generate_site.py                      # → site/
python generate_site.py --out path/to/out   # custom output directory
python generate_site.py --db path/to/db     # custom DB path
```

#### `generate_pipeline_report.py`
Generates a self-contained HTML pipeline diagnostic report with traffic-light indicators for store freshness, run completion, price outliers, price change velocity, and promo sanity.

```bash
python generate_pipeline_report.py                 # → site/pipeline-health.html
python generate_pipeline_report.py --out path/to/file
python generate_pipeline_report.py --db path/to/prices.db
```

#### `status.py`
One-shot CLI digest of pipeline state — three sections: last N runs (default 10), per-script ok/fail summary over the last 7 days, and the latest data-quality audit verdict (read from `data/logs/audit-*.json`, never recomputed). Read-only, stdlib only, always exits 0. ANSI colours auto-disable when piping or with `--no-color`.

```bash
python status.py                   # last 10 runs + 7d summary + latest audit
python status.py --runs 20         # show last 20 runs
python status.py --days 14         # 14-day per-script summary
python status.py --no-color        # plain text
```

#### `check_runs.py`
Fast post-fetch verification — used in the cron wrapper to convert a "fetch exited 0" into a real health signal. Queries the `runs` table for the most recent `status='completed'` row matching `--script`; fails if it's stale, has zero `records_written`, or is missing. Honours a per-script lock file (e.g. `data/prices_fetch.lock`) so long resumes don't trip a false fail.

```bash
python check_runs.py --script fetch_prices --max-age-hours 25
python check_runs.py --script fetch_gas_prices --max-age-hours 25
```

#### `audit_pipeline.py`
Daily data-quality audit. Reuses signal loaders from `generate_pipeline_report.py`. Writes both a text trail (`data/logs/audit-YYYY-MM-DD.txt`) and a JSON summary for later aggregation. Exits non-zero if any RED threshold is breached (store freshness >10% stale, any abandoned/error run in the last 7d, any retail network with no fresh prices in 7d, today's `price_flags` count >3× the 30-day median). Uses a read-only connection so it never blocks the live fetcher.

```bash
python audit_pipeline.py                          # → data/logs/audit-*.{txt,json}
python audit_pipeline.py --include-outliers       # also run the slow per-product outlier check
```

#### `scripts/hc_run.sh`
Bash wrapper that pings healthchecks.io `/start` before a command and either the base URL on success or `/fail` on non-zero exit. Set `HC_RUN_DRYRUN=1` to skip curl calls for local testing.

```bash
scripts/hc_run.sh <uuid> python fetch_prices.py
HC_RUN_DRYRUN=1 scripts/hc_run.sh test-uuid bash -c 'echo ok'
```

#### `generate_map.py`
Generates a self-contained Leaflet interactive map of retail stores with network colour coding and marker clustering. Standalone alternative to the map page built by `generate_site.py`.

```bash
python generate_map.py                             # → dashboard/stores_map.html
python generate_map.py --out site/stores_map.html
python generate_map.py --db path/to/prices.db
```

#### `export_analytics.py`
Exports 8 analytical SQL views to CSV files in `site/data/` for use by the static site and external tools.

| Output CSV | Contents |
|-----------|----------|
| `price_variability.csv` | Per-product price variability across networks |
| `cross_network_spread.csv` | Cross-network price spread per product |
| `popular_products.csv` | Top 200 most-recorded products |
| `private_labels.csv` | Top 100 private-label candidates |
| `stores_per_network.csv` | Store count per retail network |
| `price_freshness.csv` | Price freshness for the last 30 days |
| `products_no_prices.csv` | Products with no price records |
| `run_history.csv` | Last 30 pipeline run records |

```bash
python export_analytics.py                   # → site/data/
python export_analytics.py --out path/to/   # custom output directory
python export_analytics.py path/to/db       # custom DB path
```

---

### CI / Testing helpers

#### `build_ci_subset.py`
Builds compact store and product ID lists for GitHub Actions CI, selecting representative stores by network rank × population geography and products by coverage rank. Outputs two text files used to limit CI fetch scope.

```bash
python build_ci_subset.py                          # defaults: top-10 per net, top-50 products
python build_ci_subset.py --top-per-net 5 --top-overall 30
python build_ci_subset.py --debug                  # print selection details
python build_ci_subset.py path/to/db               # custom DB path
```

Outputs: `data/ci_stores.txt`, `data/ci_products.txt` (one ID per line).

---

### Shared modules

| File | Role |
|------|------|
| `db.py` | `init_db(path)` creates all tables; upsert/insert helpers for retail and gas |
| `api.py` | `fetch_xml(url)` with retry/backoff; all XML parsers; `centroid_from_wkt(wkt)` |
| `networks.py` | Short display names (`LIDL`, `Carrefour`, …) + `is_b2b()` flag for all retail and gas networks |
| `units.py` | Normalizes the raw `prices.unit` column to canonical buckets (`kg`, `l`, `buc`) across the wildly inconsistent per-network formats |

Not run directly.

---

### Utilities

#### `explore_api.py`
Dev tool that probes for undocumented API endpoints via WSDL/MEX metadata, root-level service discovery, and candidate pattern matching. Outputs findings to the terminal and `docs/reference/undocumented-endpoints.md`.

```bash
python explore_api.py
```

#### `backfill_prices_current.py`
One-time migration: populates the `prices_current` table from the existing `prices` table by taking the most recent price per `(product_id, store_id)`. Safe to re-run (uses upsert). Run once after the table was introduced.

```bash
python backfill_prices_current.py
python backfill_prices_current.py path/to/prices.db
```

---

## Database

All data is stored in `data/prices.db` (SQLite).

### Retail tables

```sql
retail_networks (id TEXT PK, name, logo_url)
uats            (id INT PK, name, route_id, wkt, center_lat, center_lon)
categories      (id INT PK, name, parent_id, logo_url, source TEXT)
products        (id INT PK, name, categ_id)
stores          (id INT PK, name, addr, lat, lon, uat_id, network_id, zipcode,
                 surrounding_population)
prices          (id AUTOINCREMENT PK, product_id, store_id, price, price_date, promo,
                 brand, unit, retail_categ_id, retail_categ_name,
                 fetched_at, last_checked_at,
                 UNIQUE(product_id, store_id, price_date))
```

### Gas tables

```sql
gas_networks  (id TEXT PK, name, logo_url)
gas_products  (id INTEGER PK, name, logo_url)   -- 6 fuel types
gas_stations  (id INTEGER PK, name, addr, lat, lon, uat_id, network_id, zipcode, update_date)
gas_prices    (id AUTOINCREMENT PK, product_id, station_id, price, price_date,
               fetched_at, last_checked_at,
               UNIQUE(product_id, station_id, price_date))
```

Reference tables use `INSERT OR REPLACE`; price tables use `INSERT OR IGNORE`.

### Run log

```sql
runs (id AUTOINCREMENT PK, script TEXT, started_at TEXT, finished_at TEXT,
      status TEXT,          -- 'running' | 'completed' | 'interrupted' | 'abandoned' | 'error'
      uats_processed INT, records_written INT, notes TEXT)
```

Each invocation of `fetch_prices.py` or `fetch_gas_prices.py` creates one row. Status `'abandoned'` means the process was killed (SIGKILL / unattended-upgrades) before it could write a final status — the checkpoint is still valid and will resume correctly on the next run.

### Quick queries

```bash
# latest retail prices
sqlite3 data/prices.db "SELECT s.name, p.price, p.price_date FROM prices p JOIN stores s ON p.store_id=s.id ORDER BY p.price_date DESC LIMIT 20;"

# latest gas prices
sqlite3 data/prices.db "SELECT n.name, pr.name, gp.price FROM gas_prices gp JOIN gas_stations s ON gp.station_id=s.id JOIN gas_networks n ON s.network_id=n.id JOIN gas_products pr ON gp.product_id=pr.id LIMIT 20;"

# store count per network
sqlite3 data/prices.db "SELECT n.name, COUNT(*) FROM stores s JOIN retail_networks n ON s.network_id=n.id GROUP BY n.name ORDER BY 2 DESC;"
```

See [`docs/queries.md`](docs/queries.md) for more.

---

## API

Both APIs return XML with no authentication required. They share the XML namespace: `http://schemas.datacontract.org/2004/07/pmonsvc.Models.Protos`

### Retail — base `https://monitorulpreturilor.info/pmonsvc/Retail`

| Endpoint | Description |
|----------|-------------|
| `GET /GetRetailNetworks` | All retail chains (Kaufland, Lidl, etc.) |
| `GET /GetUATByName` | Top UATs; add `?uatname=` to search |
| `GET /GetProductCategoriesNetwork` | Product category tree |
| `GET /GetProductCategoriesNetworkOUG` | OUG (emergency ordinance) categories |
| `GET /GetCatalogProductsByNameNetwork?prodname=` | Search products by name |
| `GET /GetCatalogProductsByNameNetwork?CSVcategids=` | Products by category ID(s) |
| `GET /GetCatalogProductsById?csvcatprodids=` | Products by ID(s) |
| `GET /GetStoresForProductsByLatLon?lat=&lon=&buffer=&csvprodids=&OrderBy=price` | Stores + prices near a coordinate (max buffer ~5 000 m, max 50 stores) |
| `GET /GetStoresForProductsByUat?uatId={}&csvprodids=[]&csvnetworkids=[]&OrderBy=price` | Stores + prices for a UAT |



Sample responses: [`docs/reference/sampleResponses/`](docs/reference/sampleResponses/)

### Gas — base `https://monitorulpreturilor.info/pmonsvc/Gas`

| Endpoint | Description |
|----------|-------------|
| `GET /GetGasNetworks` | All fuel networks (Petrom, OMV, MOL, Rompetrol, etc.) |
| `GET /GetGasProductsFromCatalog` | Fuel types (benzină/motorină standard & premium, GPL, electric) |
| `GET /GetGasServicesFromCatalog` | Station services catalog (shop, ATM, car wash, etc.) |
| `GET /GetUATByName` | Same UAT search as retail |
| `GET /GetGasItemsByUat?UatId=&CSVGasCatalogProductIds=&OrderBy=dist` | Stations + prices for a UAT (one product ID per request) |
| `GET /GetGasItemsByRoute?startRoutePointId=&endRoutePointId=&CSVGasCatalogProductIds=&OrderBy=dist` | Stations + prices along a route |

Sample responses: [`docs/carburanti/reference/`](docs/carburanti/reference/)

**Note:** `GetGasItemsByUat` returns HTTP 500 (not empty) for UATs with no stations — handle gracefully.

---

## Roadmap

- [x] Figure out API (retail + gas)
- [x] Create fetching scripts (retail + gas)
- [x] Store to DB
- [x] Resume interrupted runs (checkpoint files)
- [x] Track last-checked time per price (`last_checked_at`)
- [x] Store discovery from full locality list (not just top UATs)
- [x] Population-weighted store ordering in `fetch_prices.py`
- [x] Price variability analysis (`analyse_prices.py`)
- [x] Brand/product word analysis (`analyse_products.py`)
- [x] Do [carburanți](docs/carburanti/readme.md)
- [x] Automated daily fetching (cron / scheduler)
- [x] UI — monitor price variations over time
- [ ] Check price differences per UAT — maybe skip low-value UATs?
- [x] Make list of relevant products and fetch those more often
- [ ] Cross-reference with https://ro.openfoodfacts.org/ / [suntfrugal](https://www.suntfrugal.com/)
- [ ] Deduplicate products with different names but same item
- [ ] Remove dedicated brands — map to existing known brands
- [ ] check stores/location from official sites, osm?
- [ ] alternative/own API?

Vezi și: [backlog](docs/backlog.md)

### Open questions
- Does same network has different prices for different stores? — document scope and frequency
