# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Environment

Activate the project Python environment before running any scripts:
```
source .venv/bin/activate
```

**TLS note:** `api.py` automatically merges `certifi`'s CA bundle with `data/extra_certs.pem`
(Sectigo Public Server Authentication CA DV R36 intermediate, added 2026-06-12). If you
recreate the venv, TLS will work without any extra steps — `data/extra_certs.pem` is
committed to git and picked up at import time.

## Running the pipeline

### Retail
```bash
python fetch_reference.py        # one-shot: fetch networks, UATs, categories, products
python fetch_prices.py           # daily: fetch current prices for all stores × product batches
python fetch_prices.py --resume  # re-run after adding new stores; skips already-fetched store×batch keys
python generate_map.py           # regenerate site/stores_map.html from DB (run after store discovery)
```

#### Sentinel sampling (fetch optimisation)

For networks where prices are nationally uniform (Tier A), `fetch_prices.py` can query
just 2–3 representative "sentinel" stores instead of all hundreds, then propagate their
prices to every other store in the network. This reduces API calls to the sentinel stores
only — the rest get prices written via SQL-level copy, no HTTP requests needed.

**Setup (run once, then re-run weekly to refresh):**
```bash
# Analyse 30 days of data and write the sentinel config
python analyze_price_similarity.py --days 30 --export-sentinels data/sentinel_stores.json
```

`fetch_prices.py` auto-loads `data/sentinel_stores.json` on startup if the file exists.
Sentinel mode is **automatically disabled on the ISO-week full scan** (once every 7 days),
so all stores are queried at least weekly and any pricing drift is caught.

**Current Tier-A networks** (>80% products within 1% spread nationally):
PENNY (96%), LIDL (91%), KAUFLAND (87%), SUPECO (90%) — totalling ~1,060 stores reduced to 12 sentinel queries.

**Accuracy caveat:** ~10–15% of products in Tier-A networks vary regionally (mainly fresh
produce). Propagated prices for those products will match the sentinel's region until the
weekly full scan corrects them. This is acceptable for the consumer comparison use case.

**CLAUDE.md sentinel config location:** `data/sentinel_stores.json` (gitignored if you
want VPS-only config, or committed if you want it in sync).

### Gas
```bash
python fetch_gas_reference.py    # one-shot: fetch gas networks + fuel product types
python fetch_gas_prices.py       # daily: fetch current fuel prices per UAT
```

Verify results:
```bash
sqlite3 data/prices.db "SELECT s.name, p.price, p.price_date FROM prices p JOIN stores s ON p.store_id=s.id LIMIT 20;"
sqlite3 data/prices.db "SELECT n.name, pr.name, gp.price FROM gas_prices gp JOIN gas_stations s ON gp.station_id=s.id JOIN gas_networks n ON s.network_id=n.id JOIN gas_products pr ON gp.product_id=pr.id LIMIT 20;"
```

Use `npx playwright` when testing or debugging UI/browser interaction.

## Architecture

Six Python modules, stdlib + `requests` + `sqlite3` + `tqdm` only:

| File | Role |
|------|------|
| `db.py` | `init_db(path)` creates all tables; upsert helpers for retail and gas |
| `api.py` | `fetch_xml(url)` with retry/backoff; all parsers; `centroid_from_wkt(wkt)` |
| `fetch_reference.py` | Retail: run once/weekly — networks, UATs, categories, products |
| `fetch_prices.py` | Retail: run daily — iterates stores × product batches; cluster-based anchor deduplication; sentinel mode |
| `fetch_gas_reference.py` | Gas: run once/weekly — gas networks + fuel product types |
| `fetch_gas_prices.py` | Gas: run daily — one request per UAT covers all 6 fuel types; 0.3 s sleep |
| `analyze_price_similarity.py` | Analysis: per-network × store-type spread distribution + sentinel store selection; `--export-sentinels` writes `data/sentinel_stores.json` |

### Retail database tables (`data/prices.db`)

```sql
retail_networks (id TEXT PK, name, logo_url)
uats            (id INT PK, name, route_id, wkt, center_lat, center_lon)  -- shared with gas
categories      (id INT PK, name, parent_id, logo_url, source TEXT)
products        (id INT PK, name, categ_id)
stores          (id INT PK, name, addr, lat, lon, uat_id, network_id, zipcode)
prices          (id AUTOINCREMENT PK, product_id, store_id, price, price_date, promo,
                 brand, unit, retail_categ_id, retail_categ_name, fetched_at,
                 UNIQUE(product_id, store_id, price_date))
```

### Gas database tables (`data/prices.db`)

```sql
gas_networks  (id TEXT PK, name, logo_url)
gas_products  (id INTEGER PK, name, logo_url)  -- 6 fuel types
gas_stations  (id INTEGER PK, name, addr, lat, lon, uat_id, network_id, zipcode, update_date)
gas_prices    (id AUTOINCREMENT PK, product_id, station_id, price, price_date, fetched_at,
               UNIQUE(product_id, station_id, price_date))
```

Reference tables use `INSERT OR REPLACE`; price tables use `INSERT OR IGNORE`.

## API

Both APIs share the same XML namespace: `http://schemas.datacontract.org/2004/07/pmonsvc.Models.Protos`

### Retail — base `https://monitorulpreturilor.info/pmonsvc/Retail`
- **Key endpoint:** `GetStoresForProductsByLatLon?lat=&lon=&buffer=5000&csvprodids=...&OrderBy=price`
- Buffer capped at 5000 m (API returns empty above that); results capped at 50 stores per request

### Gas — base `https://monitorulpreturilor.info/pmonsvc/Gas`
- **Key endpoint:** `GetGasItemsByUat?UatId={id}&CSVGasCatalogProductIds={single_id}&OrderBy=dist`
- **One product ID per request** — CSV returns 500; loop over each of the 6 fuel IDs per UAT
- API also returns 500 (not empty) for UATs with no stations for that fuel — skip gracefully

Sample responses: `docs/reference/sampleResponses/` (retail), `docs/carburanti/reference/` (gas).


## Persona
- Act as a senior full-stack developer with deep knowledge.
- When possible run the code in your terminal to verify it works as expected. When possible make the tests short (timewise) - for example, limit the number of events or sources processed while testing. 
- provide relevant output messages and logging.
- generally create a debug mode with verbose logging for complex changes. Debug mode should be a flag in the configuration file.
- use `npx playwright` (Playwright already installed) when needed to test or debug the final results.

## General Coding Principles
- Focus on simplicity, readability, performance, maintainability, testability, and reusability.
- Less code is better; lines of code = debt.
- Make minimal code changes and only modify relevant sections.
- Suggest solutions proactively and treat the user as an expert.
- Write correct, up-to-date, bug-free, secure, performant, and efficient code.
- If unsure, say so instead of guessing


Please keep your answers concise and to the point.
Don’t just agree with me — feel free to challenge my assumptions or offer a different perspective.
Act as a senior full-stack developer with deep knowledge. Suggest improvements, optimizations, or best practices where applicable.
If a question or request is ambiguous or would benefit from clarification, ask follow-up questions before answering or getting to work.

When working with large files (>300 lines) or complex changes always start by creating a detailed plan BEFORE making any edits.
When refactoring large files break work into logical, independently functional chunks, ensure each intermediate state maintains functionality.

## Bug Handling
- If you encounter a bug or suboptimal code, add a TODO comment outlining the problem.

## RATE LIMIT AVOIDANCE
- For very large files, suggest splitting changes across multiple sessions
- Prioritize changes that are logically complete units
- Always provide clear stopping points


## Project tracking

- When detecting things that need to be addressed later, add to `docs/backlog.md` under the relevant section (Retail / Gas / General). Use a checkbox `- [ ]` entry with a clear title and enough context to act on it later.
- After completing any meaningful work, add an entry to `docs/activity-log.md` under the relevant section heading with a `### YYYY-MM-DD — Short Title` entry. Include what was done, why, and any non-obvious decisions.

When running Python commands, always first activate the venv: `source venv/bin/activate`

### WKT centroid
UAT polygons come as `POLYGON((lon1 lat1, lon2 lat2, ...))`. Centroid = average of min/max lon and lat bounds.

**Buffer limit:** the API silently returns 0 results for `buffer > ~5000` m and caps results at 50 stores per request. Use `buffer=5000`.
