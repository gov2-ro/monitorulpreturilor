# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Environment

Activate the project Python environment before running any scripts:
```
source ~/devbox/envs/240826/bin/activate
```

## Running the pipeline

```bash
python fetch_reference.py        # one-shot: fetch networks, UATs, categories, products
python fetch_prices.py           # daily: fetch current prices for all UAT × product batches
```

Verify results:
```bash
sqlite3 prices.db "SELECT s.name, p.price, p.price_date FROM prices p JOIN stores s ON p.store_id=s.id LIMIT 20;"
```

Use `npx playwright` when testing or debugging UI/browser interaction.

## Architecture

Four Python modules, stdlib + `requests` + `sqlite3` only:

| File | Role |
|------|------|
| `db.py` | `init_db(path)` creates all tables; upsert helpers for every table |
| `api.py` | `fetch_xml(url)` with retry/backoff; parsers for each endpoint; `centroid_from_wkt(wkt)` |
| `fetch_reference.py` | Run once (or weekly) to populate slow-changing reference tables |
| `fetch_prices.py` | Run daily; iterates UATs × product batches of 30; sleeps 0.5 s between requests |

### Database (`prices.db`)

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

Reference tables use `INSERT OR REPLACE`; prices use `INSERT OR IGNORE` (unique on `product_id + store_id + price_date`).

## API

- **Base:** `https://monitorulpreturilor.info/pmonsvc/Retail`
- **Format:** XML, namespace `http://schemas.datacontract.org/2004/07/pmonsvc.Models.Protos`
- **Auth:** none (public API)
- **Key endpoint:** `GetStoresForProductsByLatLon?lat=&lon=&buffer=20000&csvprodids=...&OrderBy=price`
  — requires UAT centroid (derived from WKT bounding box average) and comma-separated product IDs

Sample responses for all endpoints are in `docs/reference/sampleResponses/`.

## Project tracking

- **`docs/activity-log.md`** — append a short entry for every session: what was done and any notable findings.
- **`docs/backlog.md`** — running list of todos and known bugs; add items here rather than leaving inline TODOs in code.

### WKT centroid
UAT polygons come as `POLYGON((lon1 lat1, lon2 lat2, ...))`. Centroid = average of min/max lon and lat bounds. A buffer of 20 000 m covers most Romanian cities from their centroid.
