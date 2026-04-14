# Backlog

## Bugs / Known Issues

- **API returns max 50 stores per request** — city coverage is limited to the 50 nearest stores from the UAT centroid. Large cities (e.g. București) have more stores than this. Multi-point sweeping (e.g. grid or known store-dense coordinates) would improve coverage.
- **Products appear in multiple categories** — when a product is fetched under several `CSVcategids` queries, the last `INSERT OR REPLACE` wins for `categ_id`. Not harmful but the assigned category may be arbitrary.
- **OUG categories (10001–10041) return 0 products** — these are regulatory emergency-ordinance categories; likely not populated in the product catalogue. May be worth skipping in future runs to save time.

## Todo

- [ ] Investigate multi-point sweeping per UAT for better store coverage
- [ ] Add `fetch_prices.py` progress persistence (checkpoint file) so interrupted runs resume from where they left off instead of skipping already-fetched data
- [ ] Build UI (roadmap item 5)
- [ ] Set up automated daily fetch (cron / launchd)
- [ ] Analyse price data — cheapest basket per city, price trends over time
