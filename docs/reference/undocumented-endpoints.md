# API Endpoint Discovery Report

Generated: 2026-04-18 | Explorer: `explore_api.py`

## TL;DR

50+ endpoints probed. Key findings:

| Finding | Impact | Status |
|---------|--------|--------|
| `GetCatalogProductsByNameNetwork` (no params) dumps all 87,448 product names | Full-catalog search index | ✅ Works |
| `GetStoresForProductsByUat` confirmed working | UAT-bounded price queries + network filter | ✅ Works |
| `GetProductCategoriesOUG` — alias of NetworkOUG, sorted differently | Negligible | ✅ Works (not new data) |
| `GetGasItemsByRoute` — server-side AutoMapper crash | Route planner blocked | ❌ Bug on server |
| WSDL/MEX/help/swagger — all 404 or 500 | No full contract available | ❌ Not exposed |
| Price history endpoints (all variants) | No historical API exists | ❌ 404 |
| All guessed endpoints (store details, brands, promos, etc.) | None exist | ❌ 404 |

---

## Finding 1 — Full Product Catalog Dump (NEW)

**URL:** `GET https://monitorulpreturilor.info/pmonsvc/Retail/GetCatalogProductsByNameNetwork`
(no parameters, or `?prodname=` empty)

**Response:** 87,448 products, 16.5 MB XML. Same structure as category-filtered response but **category IDs are empty**.

**What we get:** product `Id` + `Name` for every product in the system. No category, no prices.

**vs. our current approach:** We fetch ~6,932 products by iterating over categories. Those come back with category IDs filled in. This full dump gives us 80,516 *additional* product names — likely products from networks that don't carry OUG-monitored items, or products in non-monitored categories.

**Actionable use:** Build a client-side search index. Download all 87k names + IDs weekly, embed as a compact trie/fuse.js index in the site. Users can then type any product name and get a product ID to feed into `GetStoresForProductsByLatLon`.

---

## Finding 2 — `GetStoresForProductsByUat` Confirmed Working

**URL:** `GET https://monitorulpreturilor.info/pmonsvc/Retail/GetStoresForProductsByUat?uatId={id}&csvprodids={ids}&OrderBy=price[&csvnetworkids={ids}]`

**Test:** UAT 179132 (Bucharest) + product 1686 → 200 OK, 40,681 bytes, 50 stores returned.

**Advantage over ByLatLon:** accepts `csvnetworkids` filter — can query a specific network's prices for a UAT without getting all competitors. Also UAT-bounded (administrative unit) rather than radius circle.

**Current use:** 0 — we only use ByLatLon.

**Potential use:** faster UAT-level basket scoring (one request per network per UAT instead of probing from multiple lat/lon anchors).

---

## Finding 3 — `GetProductCategoriesOUG` (Alias)

**URL:** `GET https://monitorulpreturilor.info/pmonsvc/Retail/GetProductCategoriesOUG`

Returns the same 40 OUG category IDs as `GetProductCategoriesNetworkOUG` but with ALL-CAPS names and sorted alphabetically. Not a new data source — same category tree, different presentation. No new product IDs unlocked via this endpoint.

---

## Finding 4 — `GetGasItemsByRoute` Exists But Crashes

**URL:** `GET https://monitorulpreturilor.info/pmonsvc/Gas/GetGasItemsByRoute?startRoutePointId={id}&endRoutePointId={id}&CSVGasCatalogProductIds={id}&OrderBy=dist`

Returns HTTP 500 with AutoMapper error: `WhereSelectEnumerableIterator → List<GasProduct>` mapping failure. This is a **server-side bug** in the API — the route exists, accepted our parameters, hit the database, but crashed during response serialization.

Tested with real UAT `route_id` values from our DB (Bucharest→Brașov: 175957→56601). Same error with `midRoutePointId`.

**Conclusion:** Endpoint is deployed but broken. Not usable until the API owners fix it.

---

## Finding 5 — `OrderBy` Parameter is Largely Decorative

All these values accepted without error on `GetStoresForProductsByLatLon`: `price`, `dist`, `name`, `date`, `network`, `relevance`, `id`.

But comparing top-3 stores across all orderings → **identical order**. The API likely only meaningfully implements `price` and ignores others, falling back to a default (proximity-based) ordering.

---

## Finding 6 — No Price History API

Probed every plausible variant:
- `GetPriceHistory`, `GetHistoricalPrices`, `GetPricesHistory`, `GetProductPriceHistory`, `GetProductPriceHistoryByNetwork`
- Gas: `GetGasPriceHistory`, `GetHistoricalGasPrices`

All return 404. **No historical price API exists.** Our SQLite DB is the only source of price history.

---

## Finding 7 — WCF Metadata Not Exposed

- `?wsdl` / `?singleWsdl` → HTTP 500 "Multiple actions" (internal routing conflict)
- `/mex`, `/help`, `/swagger` → 404

Full service contract not discoverable via metadata.

---

## All Probes

| Status | Endpoint | Notes |
|--------|----------|-------|
| 200 ✓ | `Retail/GetCatalogProductsByNameNetwork` (no params) | 87,448 products, no categ IDs |
| 200 ✓ | `Retail/GetStoresForProductsByUat` | Confirmed working |
| 200 ✓ | `Retail/GetProductCategoriesOUG` | Same data as NetworkOUG, all-caps |
| 200 | `Retail/GetStoresForProductsByLatLon?OrderBy=dist/name/date/network/relevance/id` | All return same ordering as `price` |
| 200 | Root `https://monitorulpreturilor.info/` | Public website (not API) |
| 500 | `Retail?wsdl`, `Retail?singleWsdl`, `Gas?wsdl`, `Gas?singleWsdl` | WCF routing conflict |
| 500 | `Gas/GetGasItemsByRoute` | AutoMapper bug on server |
| 500 | `Gas/GetGasItemsByUat?CSV_productIds=11,12` | Confirmed: CSV → SQL error |
| 403 | `https://monitorulpreturilor.info/pmonsvc/` | Root service listing forbidden |
| 404 | All guessed endpoints | None exist |

**Guessed endpoints tested (all 404):** `GetStoresForProductsByRoute`, `GetStoreDetails`, `GetStoreById`, `GetStore`, `GetStoresByNetwork`, `GetStoresByNetworkId`, `GetPriceHistory` (5 variants), `GetProductsByBrand`, `GetCatalogProductsByBarcode`, `GetProductDetails`, `GetProductById`, `GetRetailServicesCatalog`, `GetServicesCatalog`, `GetRetailServices`, `GetPromos`, `GetPromotions`, `GetActivePromos`, `GetStats`, `GetStatistics`, `GetBrands`, `GetBrandsCatalog`, `GetAllStores`, `GetStores`, `GetStoresByUat`, `GetMonitoredProducts`, `GetOUGProducts`, `GetGasItemsByName`, `GetGasStationDetails`, `GetGasStationById`, `GetGasStation`, `GetGasPriceHistory`, `GetHistoricalGasPrices`, `GetGasStationsByNetwork`, `GetAllGasStations`, `GetGasStations`, `GetGasStationsByUat`, `GetGasServicesByLatLon`, `GetGasStationsWithServices`, `GetRoutePoints`, `GetRoutes`, `GetGasStats`
