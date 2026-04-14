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
Fetches current prices for all UAT × product combinations. Requires reference data — run `fetch_reference.py` first.

```bash
python fetch_prices.py                                      # full run → data/prices.db
python fetch_prices.py --limit-uats 3 --limit-products 90  # quick smoke test
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
Fetches current fuel prices for all UATs. One request per UAT covers all fuel types — much faster than retail. Requires UAT data (run `fetch_reference.py` or `fetch_gas_reference.py` first).

```bash
python fetch_gas_prices.py                         # full run → data/prices.db
python fetch_gas_prices.py --limit-uats 3          # quick smoke test
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
- [ ] Automated fetching
    - [ ] Make list of relevant products? — fetch those more often?
    - [ ] Only save if updated
- [ ] UI
    - [ ] Monitor price variations
- [ ] Do [carburanți](docs/carburanti/readme.md)

### Questions
- Same network/shop has different prices for different stores?
