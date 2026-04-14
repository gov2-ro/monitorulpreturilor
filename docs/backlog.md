# Backlog

---

## Retail

### Bugs / Known Issues

- **API returns max 50 stores per request** — city coverage is limited to the 50 nearest stores from the UAT centroid. Large cities (e.g. București) have more stores than this. Multi-point sweeping (e.g. grid or known store-dense coordinates) would improve coverage.
- **Products appear in multiple categories** — when a product is fetched under several `CSVcategids` queries, the last `INSERT OR REPLACE` wins for `categ_id`. Not harmful but the assigned category may be arbitrary.
- **OUG categories (10001–10041) return 0 products** — these are regulatory emergency-ordinance categories; likely not populated in the product catalogue. May be worth skipping in future runs to save time.

### Todo

- [ ] **Compile full UAT list** — `GetUATByName` (no params) returns only the top ~20 UATs. The API supports `GetUATByName?uatname={name}` for search, so a full list can be built by querying each Romanian county name / city name, or by maintaining a static list of all municipality IDs. Until this is done, price fetching only covers the 20 UATs currently in the DB.
- [ ] Investigate multi-point sweeping per UAT for better store coverage
- [ ] Add `fetch_prices.py` progress persistence (checkpoint file) so interrupted runs resume from where they left off instead of skipping already-fetched data
- [ ] **Check price variability before scraping all stores** — with 50 stores per UAT × 20 UATs, it's unclear whether prices actually differ meaningfully between individual stores in the same network, or even across networks. Before committing to a full scrape, analyse existing data: compute price variance per product grouped by (UAT, network) and across networks within the same UAT. If intra-network variance is near zero, scraping one store per network per UAT is sufficient and would cut request volume dramatically.

---

## Gas

### Todo

- [ ] **Compile full UAT list** — same issue as retail: `Gas/GetUATByName` (no params) returns only top UATs. Use `?uatname=` search or a static list to cover all municipalities.
- [ ] Investigate `GetGasItemsByRoute` endpoint — enables price comparison along a driving route (start/end/mid route point IDs), which could be a useful feature for the UI.
- [ ] Add services data (`GetGasServicesFromCatalog`) — currently skipped; would allow filtering stations by amenities (ATM, car wash, restaurant, etc.)

---

## General

### Todo

- [ ] Build UI — monitor price variations for both retail and gas
- [ ] Set up automated daily fetch (cron / launchd) for both pipelines
- [ ] **Plan GitHub Actions workflow** — evaluate using GH Actions (free tier cron) as an alternative to local cron/launchd for daily fetching; consider artifact/DB storage strategy (commit DB to repo vs. upload to object storage vs. keep local only)
- [ ] Analyse price data — cheapest basket per city, price trends over time
- [ ] Add `get_uats(conn)` read helper to `db.py` — both `fetch_prices.py` and `fetch_gas_prices.py` run the same `SELECT id, name, center_lat, center_lon FROM uats` query inline; moving it to `db.py` keeps DB queries in one place
