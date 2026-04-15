# monitorulpreturilor.info

Fetch and store food price and fuel price data from the Romanian government price monitor API.

> Proiectul *Monitorul Prețurilor* produselor alimentare își propune să acorde consumatorilor posibilitatea de a compara prețul aferent coșului de produse a cărui achiziție intenționează să o realizeze.

start with [Retail](https://monitorulpreturilor.info/Home/Retail). See also [Gas](https://monitorulpreturilor.info/Home/Gas)

## Setup

```bash
source ~/devbox/envs/240826/bin/activate
```

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
Fetches current prices for all UAT × product combinations. Saves progress to `data/retail_checkpoint.json` so interrupted runs resume automatically. Requires reference data — run `fetch_reference.py` first.

Checkpoint behaviour:
- **Interrupted run** → resumes from last saved position on next run (regardless of how old the checkpoint is)
- **Completed run, same day** → exits immediately — no redundant API calls
- **Completed run, new day** → starts a fresh run automatically
- **`--fresh`** → ignores any checkpoint and starts clean

Prices are stored with `fetched_at` (original insert time) and `last_checked_at` (updated on every re-check, even when price hasn't changed).

```bash
python fetch_prices.py                                      # full run → data/prices.db
python fetch_prices.py --limit-uats 3 --limit-products 90  # quick smoke test
python fetch_prices.py --fresh                              # ignore checkpoint, start clean
python fetch_prices.py path/to/db.db                        # custom DB path
```

> **Note:** `GetUATByName` (no params) returns only ~20 top UATs. Full city coverage requires searching by name. See backlog.

### Gas

#### `fetch_gas_reference.py`
Fetches gas networks and fuel product types (6 fuel types: benzină/motorină standard & premium, GPL, electric). Run once or weekly.

```bash
python fetch_gas_reference.py              # full run → data/prices.db
python fetch_gas_reference.py path/to/db  # custom DB path
```

#### `fetch_gas_prices.py`
Fetches current fuel prices for all UATs. One request per fuel type per UAT. Saves progress to `data/gas_checkpoint.json` so interrupted runs resume automatically. Requires UAT data (run `fetch_reference.py` or `fetch_gas_reference.py` first).

Same checkpoint behaviour and `fetched_at` / `last_checked_at` tracking as retail (see above).

```bash
python fetch_gas_prices.py                         # full run → data/prices.db
python fetch_gas_prices.py --limit-uats 3          # quick smoke test
python fetch_gas_prices.py --fresh                 # ignore checkpoint, start clean
python fetch_gas_prices.py path/to/db              # custom DB path
```

### `db.py` / `api.py`
Not run directly. `db.py` provides `init_db()` and all upsert helpers. `api.py` wraps HTTP calls and XML parsers for both retail and gas endpoints.

## API

Both APIs return XML with no authentication required. They share the same XML namespace: `http://schemas.datacontract.org/2004/07/pmonsvc.Models.Protos`

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
| `GET /GetStoresForProductsByLatLon?lat=&lon=&buffer=&csvprodids=&OrderBy=price` | Stores + prices near a coordinate (max buffer ~5000 m, max 50 stores) |

Sample responses: [`docs/reference/sampleResponses/`](docs/reference/sampleResponses/)

### Gas — base `https://monitorulpreturilor.info/pmonsvc/Gas`

| Endpoint | Description |
|----------|-------------|
| `GET /GetGasNetworks` | All fuel networks (Petrom, OMV, MOL, Rompetrol, etc.) |
| `GET /GetGasProductsFromCatalog` | Fuel types (benzină/motorină standard & premium, GPL, electric) |
| `GET /GetGasServicesFromCatalog` | Station services catalog (shop, ATM, car wash, etc.) |
| `GET /GetUATByName` | Same UAT search as retail |
| `GET /GetGasItemsByUat?UatId=&CSVGasCatalogProductIds=&OrderBy=dist` | Stations + prices for a UAT |
| `GET /GetGasItemsByRoute?startRoutePointId=&endRoutePointId=&CSVGasCatalogProductIds=&OrderBy=dist` | Stations + prices along a route |

Sample responses: [`docs/carburanti/reference/`](docs/carburanti/reference/)

## Roadmap

- [x] Figure out API (retail + gas)
- [x] Create fetching scripts (retail + gas)
- [x] Store to DB
- [ ] Check price differences per UAT — maybe it doesn't make sense to always fetch all stores?
    - Maybe check distributed UATs, top 50, bottom 50, and some in the middle, also geographically distributed?
- [x] Resume interrupted runs (checkpoint files)
- [x] Track last-checked time per price (`last_checked_at`)
- [ ] Automated fetching
    - [ ] Make list of relevant products? — fetch those more often?
- [ ] UI
    - [ ] Monitor price variations
- [x] Do [carburanți](docs/carburanti/readme.md)
- [ ] check against https://ro.openfoodfacts.org/  and similar? [suntfrugal](https://www.suntfrugal.com/)
- [ ] check against other countries prices?
- [ ] remove branduri dedicate - find existing brands
- [ ] duplicates? different names, same product?
- [ ] check how much the dev project costed - explain why it's not worthy! - app Ultima actualizare 7 apr. 2023 - fb.com/monitorulpreturilor page, last post, march 2022

### Questions
- Same network/shop has different prices for different stores?
