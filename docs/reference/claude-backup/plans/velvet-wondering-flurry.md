# Plan: monitorulpreturilor.info Scraping Pipeline

## Context
Monitorul Prețurilor is a Romanian government food price comparison platform. The API returns XML from `https://monitorulpreturilor.info/pmonsvc/Retail/`. The goal is a two-phase pipeline: fetch reference data (networks, UATs, categories, products) once, then fetch price data per UAT × product batch on a recurring basis, storing everything in SQLite.

## Key findings from sample responses
- All responses: XML with namespace `http://schemas.datacontract.org/2004/07/pmonsvc.Models.Protos`
- No auth required (public API)
- **Price data lives in `GetStoresForProductsByLatLon`** — requires lat/lon + buffer (meters) + CSV product IDs
- UAT WKT polygons give bounding boxes → compute centroid → use as query origin
- Buffer ~20000m covers most Romanian cities from centroid
- Products fetched per category: `GetCatalogProductsByNameNetwork?CSVcategids={id}` (category search, no pagination seen)
- ~20 UATs, ~100 categories, potentially thousands of products → batch products 30 at a time for price queries

## Schema (`prices.db`)

```sql
retail_networks (id TEXT PK, name, logo_url)
uats            (id INT PK, name, route_id, wkt, center_lat, center_lon)
categories      (id INT PK, name, parent_id, logo_url, source TEXT)  -- 'network' or 'oug'
products        (id INT PK, name, categ_id)
stores          (id INT PK, name, addr, lat, lon, uat_id, network_id, zipcode)
prices          (id AUTOINCREMENT PK, product_id, store_id, price, price_date, promo,
                 brand, unit, retail_categ_id, retail_categ_name, fetched_at,
                 UNIQUE(product_id, store_id, price_date))
```

## Files to create

### `db.py`
- `init_db(path)` → creates all tables, returns `conn`
- Insert helpers: `upsert_network`, `upsert_uat`, `upsert_category`, `upsert_product`, `upsert_store`, `insert_price`

### `api.py`
- `BASE = "https://monitorulpreturilor.info/pmonsvc/Retail"`
- `NS = "http://schemas.datacontract.org/2004/07/pmonsvc.Models.Protos"`
- `fetch_xml(url)` → `ET.Element` (with retry + timeout)
- `parse_networks(root)`, `parse_uats(root)` (extracts centroid from WKT POLYGON bounds)
- `parse_categories(root, source)`, `parse_products(root)`
- `parse_stores_and_prices(root, fetched_at)` → `(stores, prices)`
- `centroid_from_wkt(wkt)` → `(lat, lon)` — parse `POLYGON((lon lat,...))` → average bounds

### `fetch_reference.py`
Fetches static/slow-changing data. Run once (or weekly):
1. GET `GetRetailNetworks` → upsert networks
2. GET `GetUATByName` → upsert UATs (compute centroid)
3. GET `GetProductCategoriesNetwork` → upsert categories (source='network')
4. GET `GetProductCategoriesNetworkOUG` → upsert categories (source='oug')
5. For each category ID: GET `GetCatalogProductsByNameNetwork?CSVcategids={id}` → upsert products

### `fetch_prices.py`
Fetches current prices. Run daily:
1. Load all UATs and all product IDs from DB
2. For each UAT (center_lat, center_lon):
   - For each batch of 30 product IDs:
     - GET `GetStoresForProductsByLatLon?lat=&lon=&buffer=20000&csvprodids=...&OrderBy=price`
     - Parse → upsert stores + insert prices (with `fetched_at = now`)
   - Sleep 0.5s between requests (be polite)
3. Log progress to stdout

## WKT centroid parsing
WKT format: `POLYGON((lon1 lat1, lon2 lat2, lon3 lat3, lon4 lat4, lon1 lat1))`
→ extract min/max lon and lat → center = ((min+max)/2 for each)

## Implementation notes
- Use stdlib only: `urllib.request` or `requests`, `xml.etree.ElementTree`, `sqlite3`
- Use `requests` (likely already available in the project env)
- Retry on HTTP errors (3 retries, exponential backoff)
- `INSERT OR IGNORE` / `INSERT OR REPLACE` for reference data upserts
- `INSERT OR IGNORE` for prices (unique on product_id + store_id + price_date)
- CLI: `python fetch_reference.py` then `python fetch_prices.py`

## Verification
1. Run `python fetch_reference.py` → check `prices.db` has rows in all reference tables
2. Run `python fetch_prices.py` → check `prices.db` prices table has rows
3. Query: `SELECT s.name, p.price, p.price_date FROM prices p JOIN stores s ON p.store_id=s.id LIMIT 20;`
4. Spot-check a price against the live website for a known product
