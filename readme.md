# monitorulpreturilor.info

Fetch and store food price and fuel price data from the Romanian government price monitor API.

> Proiectul *Monitorul Prețurilor* produselor alimentare își propune să acorde consumatorilor posibilitatea de a compara prețul aferent coșului de produse a cărui achiziție intenționează să o realizeze.

Start with [Retail](https://monitorulpreturilor.info/Home/Retail). See also [Gas](https://monitorulpreturilor.info/Home/Gas).

---

## Setup

```bash
source ~/devbox/envs/240826/bin/activate
```

Dependencies: stdlib + `requests` + `sqlite3` + `tqdm` (+ `openpyxl` for `discover_stores_by_uat.py`)

---

## Pipeline overview

```
fetch_reference.py          # once / weekly — retail reference data
  └─ fetch_prices.py        # daily — retail prices (per-store, population-ordered)

fetch_gas_reference.py      # once / weekly — gas reference data
  └─ fetch_gas_prices.py    # daily — gas prices

discover_stores.py          # one-shot — store discovery from locality centroids
  └─ update_store_populations.py   # after discovery — enrich stores with population
```

Analysis (standalone, read-only):
```
analyse_prices.py           # price variability: intra-network + cross-network CSVs
analyse_products.py         # brand/word frequency + category anomaly CSVs
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

```bash
python fetch_prices.py       # retail — resumes from checkpoint if interrupted
python fetch_gas_prices.py   # gas    — resumes from checkpoint if interrupted
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

```bash
python fetch_prices.py                                      # full run → data/prices.db
python fetch_prices.py --resume                             # fetch only new stores added since today's run
python fetch_prices.py --limit-stores 3 --limit-products 90 # quick smoke test
python fetch_prices.py --fresh                              # ignore checkpoint, start clean
python fetch_prices.py path/to/db.db                        # custom DB path
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

### Shared modules

| File | Role |
|------|------|
| `db.py` | `init_db(path)` creates all tables; upsert/insert helpers for retail and gas |
| `api.py` | `fetch_xml(url)` with retry/backoff; all XML parsers; `centroid_from_wkt(wkt)` |

Not run directly.

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
- [ ] Automated daily fetching (cron / scheduler)
- [ ] UI — monitor price variations over time
- [ ] Check price differences per UAT — maybe skip low-value UATs?
- [ ] Make list of relevant products and fetch those more often
- [ ] Cross-reference with https://ro.openfoodfacts.org/ / [suntfrugal](https://www.suntfrugal.com/)
- [ ] Deduplicate products with different names but same item
- [ ] Remove dedicated brands — map to existing known brands

### Open questions
- Same network has different prices for different stores — document scope and frequency
