# Backlog

## Bugs / Known Issues

- **API returns max 50 stores per request** — city coverage is limited to the 50 nearest stores from the UAT centroid. Large cities (e.g. București) have more stores than this. Multi-point sweeping (e.g. grid or known store-dense coordinates) would improve coverage.
- **Products appear in multiple categories** — when a product is fetched under several `CSVcategids` queries, the last `INSERT OR REPLACE` wins for `categ_id`. Not harmful but the assigned category may be arbitrary.
- **OUG categories (10001–10041) return 0 products** — these are regulatory emergency-ordinance categories; likely not populated in the product catalogue. May be worth skipping in future runs to save time.

## Todo

- [ ] **Compile full UAT list** — `GetUATByName` (no params) returns only the top ~20 UATs. The API supports `GetUATByName?uatname={name}` for search, so a full list can be built by querying each Romanian county name / city name, or by maintaining a static list of all municipality IDs. Until this is done, price fetching only covers the 20 UATs currently in the DB.
- [ ] Investigate multi-point sweeping per UAT for better store coverage
- [ ] Add `fetch_prices.py` progress persistence (checkpoint file) so interrupted runs resume from where they left off instead of skipping already-fetched data
- [ ] Build UI (roadmap item 5)
- [ ] Set up automated daily fetch (cron / launchd)
- [ ] Analyse price data — cheapest basket per city, price trends over time
- [ ] **Check price variability before scraping all stores** — with 50 stores per UAT × 20 UATs, it's unclear whether prices actually differ meaningfully between individual stores in the same network, or even across networks. Before committing to a full scrape, analyse existing data: compute price variance per product grouped by (UAT, network) and across networks within the same UAT. If intra-network variance is near zero, scraping one store per network per UAT is sufficient and would cut request volume dramatically.
