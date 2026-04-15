# UI Views, Charts & Analytics — Plan

## Context

Romanian price monitoring dataset with:
- **Retail**: 9 networks, 3,891 stores, 6,932 products, 143 categories, ~650 brands, 20 UATs, 157k+ price records (growing daily)
- **Gas**: 7 networks, 415 stations, 6 fuel types, 1,720 price records
- **Geography**: Full lat/lon for every store/station, population data, UAT polygons (WKT)
- **Time**: Daily collection (growing)
- **Delivery**: Starts as static GitHub Pages, evolves into standalone app (server-side queries, user features, API)

---

## 1. CONSUMER-FACING VIEWS

### 1A. Cheapest Basket Calculator
Pick a city/UAT, select products (or a preset basket like "basic groceries"), see which network is cheapest and the total cost per network.
- **Chart**: Horizontal bar chart — one bar per network, colored by brand. Total basket cost + savings vs. most expensive.
- **Standalone bonus**: Save personal baskets, share via link, track basket cost over time.

### 1B. Product Price Comparison
Pick a product — see its price at every network, sorted cheapest first. Show store count, min/max/avg per network.
- **Chart**: Table with sparklines showing price range per network. Color-code cells (green=cheap, red=expensive).
- **Standalone bonus**: Full-text search with autocomplete, "similar products" suggestions via fuzzy matching.

### 1C. Gas Price Map
Interactive map showing gas stations colored by price for a selected fuel type. Tap station — see all 6 fuel prices.
- **Chart**: Leaflet map with green→red gradient markers. Filter dropdown for fuel type.
- **Standalone bonus**: Geolocation "near me" — sort stations by distance from user's location.

### 1D. Price Alert / Change Tracker
Products with the biggest price drops or increases in the last 7/30 days.
- **Chart**: Table sorted by % change, up/down arrows. Filterable by category/network.
- **Standalone bonus**: Email/push notifications for user-defined products or thresholds.

### 1E. "Nearest Cheapest" Store Finder (standalone only)
User shares location or enters address — find the cheapest store for their basket within a given radius.
- Combines geolocation + basket pricing + routing distance.
- Answers: "Is it worth driving 5 km to save 15 RON?"

### 1F. Shopping List Optimizer (standalone only)
User builds a shopping list — app recommends the optimal split across 2–3 nearby stores to minimize total cost.
- Multi-store optimization: "Buy bread and milk at Lidl, everything else at Profi."
- Shows savings vs. single-store shopping.

---

## 2. ANALYTICAL / INSIGHT VIEWS

### 2A. Network Price Index ("Who's cheapest overall?")
Normalized price index across all comparable products (same product, same unit). Index = 100 for cheapest network.
- **Chart**: Bar chart or radar/spider chart with networks on each axis. One line per category.
- Exclude SELGROS (B2B wholesale).

### 2B. Price Spread Dashboard
Distribution of cross-network price ratios for the same product.
- **Charts**:
  - Histogram of price ratios (most expensive / cheapest) — long tail past 2x
  - Top 20 products with highest spread (table)
  - Box plot per category showing spread distribution
- Quantifies the value of price shopping.

### 2C. Intra-Network Consistency Map
For a given network, show which stores are more expensive for the same products.
- **Chart**: Heatmap overlay on map — stores colored by avg price deviation from network mean.
- Reveals geographic pricing strategies (rural premium, city competition).

### 2D. Category Price Trends (time series)
Average price per category over time, split by network.
- **Chart**: Multi-line time series, one line per network. Dropdown to select category.
- Track inflation, seasonal patterns, competitive responses.

### 2E. Brand Landscape
Which brands are exclusive to one network (private label) vs. available everywhere?
- **Charts**:
  - Bubble chart: x=network count, y=avg price, size=product count
  - Table: brands sorted by exclusivity
- Identifies private-label products and brand positioning.

### 2F. Promo Analysis (standalone, once promo data is richer)
Track promotion frequency and depth per network/category. Which networks run the most promos? Are "promo" prices actually cheaper than competitors' regular prices?
- **Chart**: Stacked bar (promo vs. regular share by network), scatter (promo price vs. competitor regular price).

### 2G. Price Elasticity Heatmap (standalone, advanced)
When one network changes a price, do competitors follow? Cross-correlate daily price changes between networks for the same product.
- **Chart**: Correlation matrix heatmap (network × network), with drill-down to specific products.
- Reveals competitive dynamics and price leadership.

---

## 3. GEOGRAPHIC / MAP VIEWS

### 3A. Enhanced Store Map (upgrade existing)
Upgrade current `stores_map.html`:
- Toggle layers by network (show/hide)
- Store count + avg price overlay per UAT (choropleth)
- Click store — popup with price stats for that store

### 3B. Price Heatmap by Region
Choropleth of Romania's UATs colored by average price level for a selected basket/category.
- **Chart**: UAT polygons (from WKT) filled with color gradient.
- Shows geographic price inequality.

### 3C. Coverage Map
Store density vs. population density — where we have data vs. gaps.
- **Chart**: Dot density map with population reference layer.
- Internal tool to prioritize data collection expansion.

### 3D. Route Price Planner (standalone only)
Enter a driving route (A → B) — show gas stations along the route with prices, and retail stores near rest stops.
- Leverages gas API's `GetGasItemsByRoute` endpoint (currently unused).
- High consumer value for road trips.

---

## 4. GAS-SPECIFIC VIEWS

### 4A. Fuel Price Leaderboard
Per UAT, rank gas networks by price for each fuel type.
- **Chart**: Sortable table with colored cells. Toggle between fuel types.

### 4B. Diesel vs. Gasoline Spread
Premium/standard spread and diesel/gasoline gap over time per network.
- **Chart**: Grouped bar chart or line chart over time.

### 4C. EV Charging Price Comparison
Separate view for electric charging prices (product ID 41) — fewer stations, different economics.
- **Chart**: Map + table.

### 4D. Gas Price Trend Tracker
Daily fuel price trend per network. "Is diesel going up or down?"
- **Chart**: Line chart per fuel type, one line per network. 7/30/90 day view.

---

## 5. DATA QUALITY / OPERATIONAL DASHBOARDS

### 5A. Pipeline Health Dashboard
Last fetch time, records collected, stores covered, products covered, run duration, error counts.
- **Chart**: KPI cards + run history table from `runs` audit table.

### 5B. Coverage Scorecard
% of stores with prices today by network. % of products with >= 3 network prices.
- **Chart**: Progress bars per network, table of under-covered products.

### 5C. Data Quality Flags
Products with suspicious prices (outliers), missing units, null networks, brand normalization issues.
- **Chart**: Table with severity flags.

---

## 6. REPORTS & EXPORTS

### 6A. Weekly Price Report
Auto-generated: top 10 cheapest products, biggest price changes, cheapest network per category.
- **Format**: HTML page regenerated weekly, archive of past reports. Shareable, SEO-friendly.

### 6B. Inflation Tracker (long-term)
Custom consumer price index based on actual scraped prices vs. official CPI.
- **Chart**: Line chart, our index vs. official stats.
- Newsworthy once months of data accumulate.

### 6C. Open Data API (standalone only)
REST API exposing price data for researchers, journalists, other apps.
- Endpoints: `/api/prices?product=X&network=Y`, `/api/basket?products=...&uat=Z`
- Rate-limited, documented, versioned.

### 6D. Embeddable Widgets (standalone only)
Small chart widgets (price comparison, gas map) that other sites can embed via iframe.
- "Powered by MonitorulPreturilor" attribution.

---

## 7. STANDALONE-APP-ONLY FEATURES

These require server-side logic and can't work as static pages:

| Feature | Why it needs a server |
|---------|----------------------|
| Full-text product search with autocomplete | Real-time querying over 6,932+ products |
| Geolocation "near me" queries | Server-side spatial queries (lat/lon + radius) |
| Personal saved baskets & price alerts | User accounts, persistent state |
| Shopping list optimizer (multi-store) | Server-side optimization algorithm |
| Route price planner | Server-side route geometry + spatial joins |
| Open Data API | REST endpoints serving live data |
| Email/push price notifications | Background jobs, notification delivery |
| Price history for any product (deep drill-down) | On-demand queries, not pre-computed |
| Admin panel for data quality triage | Authenticated CRUD on flagged records |
| Collaborative baskets ("family shopping list") | Multi-user state, real-time sync |

---

## RECOMMENDED PRIORITY ORDER

### Phase 1 — Foundation (static, GitHub Pages compatible)
| View | Rationale |
|------|-----------|
| Pipeline Health Dashboard (5A) | Operational confidence first |
| Enhanced Store Map (3A) | Quick win, upgrade existing asset |
| Network Price Index (2A) | Headline insight: "who's cheapest" |
| Fuel Price Leaderboard (4A) | Simple, high consumer impact |

### Phase 2 — Consumer Value (static or early standalone)
| View | Rationale |
|------|-----------|
| Cheapest Basket Calculator (1A) | #1 consumer question |
| Product Price Comparison (1B) | Product-level lookup |
| Gas Price Map (1C) | High consumer value |
| Price Spread Dashboard (2B) | Quantifies the value proposition |

### Phase 3 — Time Series (needs accumulated data)
| View | Rationale |
|------|-----------|
| Category Price Trends (2D) | Needs weeks+ of history |
| Price Change Tracker (1D) | Needs history for deltas |
| Gas Price Trend Tracker (4D) | Same |
| Weekly Price Report (6A) | Auto-generated, SEO value |

### Phase 4 — Standalone App Features
| View | Rationale |
|------|-----------|
| Full-text search + autocomplete | Core UX upgrade |
| Nearest Cheapest Store Finder (1E) | High consumer value, needs geolocation |
| Shopping List Optimizer (1F) | Differentiating feature |
| Price Alerts / Notifications (1D+) | Engagement & retention |
| Open Data API (6C) | Platform play |
| Route Price Planner (3D) | Unique, uses untapped API endpoint |

### Phase 5 — Advanced Analytics
| View | Rationale |
|------|-----------|
| Inflation Tracker (6B) | Needs months of data |
| Price Elasticity Heatmap (2G) | Advanced, needs dense time series |
| Promo Analysis (2F) | Needs richer promo flag data |
| Brand Landscape (2E) | Niche analytical audience |

---

## TECH APPROACH

### Phase 1–2: Static site (GitHub Pages)
- Python script generates HTML + embedded JSON from SQLite
- Single-page app with tabs, or multi-page with shared nav
- Libraries: Leaflet (maps), Chart.js or Apache ECharts (charts), vanilla JS
- Regenerated in CI after daily fetch
- Target: <2 MB total (pre-computed aggregates, not raw data)

### Phase 3+: Standalone app
- **Backend**: Python (FastAPI or Flask) serving SQLite/PostgreSQL
- **Frontend**: Lightweight framework (Alpine.js, htmx, or Vue) — keep it simple
- **Deployment**: Docker container, single VPS or fly.io
- **Search**: SQLite FTS5 for product search, or pg_trgm if PostgreSQL
- **Background**: Celery or simple cron for alerts, report generation
- **Auth**: Optional — anonymous browse, accounts only for saved baskets/alerts
