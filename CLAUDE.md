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

- When detecting things that need to be addressed later, add to `docs/BACKLOG.md`. Use a checkbox `- [ ]` entry with a clear title and enough context to act on it later.
- After completing any meaningful work, add an entry to `docs/activity-history.md` under a `## YYYY-MM-DD — Short Title` heading. Include what was done, why, and any non-obvious decisions.

When running Python commands, always first activate the following venv `~/devbox/envs/240826/` (/Users/pax/devbox/envs/240826/bin/activate)

### WKT centroid
UAT polygons come as `POLYGON((lon1 lat1, lon2 lat2, ...))`. Centroid = average of min/max lon and lat bounds.

**Buffer limit:** the API silently returns 0 results for `buffer > ~5000` m and caps results at 50 stores per request. Use `buffer=5000`.
