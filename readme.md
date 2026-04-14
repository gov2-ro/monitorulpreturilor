# monitorulpreturilor.info

Fetch and store food price data from the Romanian government price monitor API.

> Proiectul *Monitorul Prețurilor* produselor alimentare își propune să acorde consumatorilor posibilitatea de a compara prețul aferent coșului de produse a cărui achiziție intenționează să o realizeze.

start with [Retail](https://monitorulpreturilor.info/Home/Retail). See also [Gas](https://monitorulpreturilor.info/Home/Gas)

## Setup

```bash
source ~/devbox/envs/240826/bin/activate
```

## Scripts

### `fetch_reference.py`
Fetches slow-changing reference data: retail networks, UATs, product categories, and products. Run once, or weekly to pick up new products.

```bash
python fetch_reference.py                  # full run → data/prices.db
python fetch_reference.py --limit 5        # first 5 categories only (for testing)
python fetch_reference.py path/to/db.db    # custom DB path
```

### `fetch_prices.py`
Fetches current prices for all UAT × product combinations. Requires reference data — run `fetch_reference.py` first.

```bash
python fetch_prices.py                                      # full run → data/prices.db
python fetch_prices.py --limit-uats 3 --limit-products 90  # quick smoke test
python fetch_prices.py path/to/db.db                        # custom DB path
```

> **Note:** `GetUATByName` (no params) returns only ~20 top UATs. Full city coverage requires searching by name. See backlog.

### `db.py`
Not run directly. Provides `init_db()` and upsert helpers used by both fetch scripts. Default DB path: `data/prices.db`.

### `api.py`
Not run directly. Wraps all HTTP calls (`fetch_xml` with retry/backoff) and XML parsers for each endpoint.

## API

Base: `https://monitorulpreturilor.info/pmonsvc/Retail`  
Format: XML, no auth required.  
⚠ `GetStoresForProductsByLatLon` returns 0 results for `buffer > 5000` and caps at 50 stores per call.

| Endpoint | Description |
|----------|-------------|
| `GET /GetRetailNetworks` | All retail chains (Kaufland, Lidl, etc.) |
| `GET /GetUATByName` | Top UATs (cities/municipalities); add `?uatname=` to search |
| `GET /GetProductCategoriesNetwork` | Product category tree |
| `GET /GetProductCategoriesNetworkOUG` | OUG (emergency ordinance) categories |
| `GET /GetCatalogProductsByNameNetwork?prodname=` | Search products by name |
| `GET /GetCatalogProductsByNameNetwork?CSVcategids=` | Products by category ID(s) |
| `GET /GetCatalogProductsById?csvcatprodids=` | Products by ID(s) |
| `GET /GetStoresForProductsByLatLon?lat=&lon=&buffer=&csvprodids=&OrderBy=price` | Stores + prices near a coordinate |

Sample responses: [`docs/reference/sampleResponses/`](docs/reference/sampleResponses/)

## Roadmap

- [x] Figure out API
- [x] Create fetching scripts
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
