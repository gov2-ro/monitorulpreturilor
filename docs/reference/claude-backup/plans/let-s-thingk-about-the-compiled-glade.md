# Monitorul Prețurilor+ — Retail UI Roadmap

## Context

We have **2.25M retail price records** across 6,932 products, 4,011 stores, 10 networks, and 366 UATs sitting in `data/prices.db`. The current site (`docs/`) ships a competent foundation — KPIs, network price index, per-product trends, compare, store map, internal pipeline page — but mostly *displays* the data; it doesn't yet *unlock* it for the people who would benefit most: shoppers deciding where to spend their money, journalists telling regional stories, and researchers needing a clean handle on a dataset the state itself doesn't surface this well.

This spec is a prioritized roadmap to turn the existing dataset into a **public-good civic instrument** for retail (gas tracked separately, deferred). The bar: every feature must answer "so what?" for at least one of the three audiences in one sentence.

**Constraints inherited from the project:**
- Static GitHub Pages output via `generate_site.py`. Build step OK where it pays for itself.
- Time depth is currently shallow for retail (~7 days); architect for monthly/weekly aggregation, not daily noise.
- Compare only within normalized units; flag/exclude SELGROS (B2B) from consumer-facing comparisons.
- Disclaimer required: not an official gov project; data sourced from monitorulpreturilor.info.

**Design principle:** every widget should be **insight-first, chart-second** — lead with the number that changes a decision, then the visualisation that proves it.

---

## Tier 1 — Flagship features (build first)

### 1. Coșul Cetățeanului — *Citizen's Basket*
The killer feature. Curated baskets answer: *"For who I am and where I live, where do I shop to save the most?"*

**Baskets (curated, versioned in repo):**
- Familie cu copii (4 pers., ~30 SKUs: lapte, pâine, ouă, ulei, paste, mălai, carne tocată, fructe, legume…)
- Pensionar (frugal, ~15 SKUs)
- Student (snack/budget, ~12 SKUs)
- Coș Sărbătoare (Crăciun / Paște — seasonal)

**For each basket × UAT × network**, compute:
- Total cost today (cheapest available substitute when SKU missing)
- Δ vs cheapest network in same UAT (lei/săptămână, lei/lună, lei/an)
- 4-week rolling change once history accumulates
- Coverage flag: "X of N items not stocked here" (data-honest)

**UI:** picker for basket + UAT → KPI card ("Plătești 247 lei/lună mai mult la X față de Y") + ranked network table + per-item drill-down. Geolocation auto-fills UAT.

**Data path:** new `scripts/build_baskets.py` → emits `docs/data/baskets/{basket_id}_{uat_id}.json` (small, ~5kB each, lazy-loaded). Reuses unit-normalisation work from backlog.

**Audiences:** citizens (direct savings); journalists ("the family-of-4 in Vaslui pays X more than in Cluj").

---

### 2. Harta Scumpetei — *Choropleth Map of Costliness*
A single map answering: *"Where in Romania is daily life most expensive?"*

**Layers (toggle):**
- Median basket cost per UAT (uses #1)
- Number of networks present per UAT (food-desert detection — surfaces UATs with 1 or 0 retailers)
- Price spread within UAT (high spread = competitive; low spread = monopoly risk)
- Coverage staleness (how recently we have data)

**UI:** MapLibre GL JS + vector UAT polygons (build them once from WKT in `uats.wkt` via `shapely` → `docs/data/uats.geojson`, ~1–2 MB gzipped, cacheable). Click UAT → side panel with stores, basket KPIs, networks present.

**Stack switch:** Leaflet stays for the existing store-marker map; MapLibre is materially better for choropleths and vector polygons. Two libraries is fine — they don't compete on the same page.

**Audiences:** journalists (regional inequality stories); researchers (downloadable choropleth as PNG/SVG/GeoJSON); citizens ("am I in a food desert?").

---

### 3. Anomalii & Promoții — *Live Anomaly Feed*
A scrolling feed of *"same product, very different price"*. We already half-have this in `cross_network_spread.csv` (with some 317× spreads waiting to be surfaced).

**Feeds:**
- **Top spreads today** — same product, ratio max/min ≥ N (configurable), normalized by unit, SELGROS excluded
- **Active promotions** — `prices.promo = true` rows, ranked by % discount
- **New low** — products at their all-time-low price (once history matures)
- **Stockouts** — products tracked but no longer found at a network

**UI:** card list with product, two networks, two prices, ratio, "save X lei" CTA → links to compare page. Filters by category, network, UAT. Daily snapshot pinned + live scroll.

**Audiences:** citizens (immediate savings); journalists (story leads, every day fresh).

---

## Tier 2 — Power features

### 4. Indice de Inflație Civică — *Civic Inflation Index*
A citizen-built CPI alternative for groceries. Laspeyres index per category, basket-weighted. Compare with INS published numbers side-by-side; methodology fully open (code, weights, sources). Will become powerful as retail history deepens beyond 7 days; in the meantime, render with low-confidence caveats and use gas (12 mo history) as a methodology-proving sibling page.

**Stack:** Observable Plot (cleaner small-multiples & confidence bands than Chart.js).

**Audience:** journalists, researchers, civic-tech.

### 5. Brand vs Marcă Privată — *Private-Label Premium Tracker*
Per category: "How much do you pay for the brand?" Uses existing `private_labels.csv`. Output: `[Lapte] Brand premium median = +47%`. Per-network breakdown (Carrefour's own brand vs Kaufland's own brand vs branded equivalent). Drives behaviour change without telling people what to buy.

### 6. Explorator de Categorii — *Category Drill-Down*
Tree explorer: ALIMENTE → LACTATE → IAURT → product list, with per-leaf leaderboard, spread, and recent changes. Becomes the discovery surface that the current product-dropdown can't be. Reuses `categories.parent_id`.

### 7. Aproape de tine — *Geolocation Store Finder*
User opts in to geolocation → nearest stores per network, with the basket cost at each, distance, and (optional) drive-time. Pure browser geolocation; no server. Pairs naturally with #1.

---

## Tier 3 — Storytelling & ecosystem

### 8. Povești cu Date — *Auto-Generated Stories*
Weekly auto-narratives: "Biggest spike this week", "Regional gap of the week", "Best time to buy ulei". Each story renders as a shareable Open Graph card (HTML→PNG in CI via Playwright). Opens a civic-data-journalism lane and drives organic traffic without needing an editor.

### 9. Hub de Date Deschise — *Open Data Hub*
Downloadable CSV/JSON/Parquet of every dataset, documented schema, freshness badge, recommended citations, example notebooks. Position the project as **open data infrastructure**, not just a website.

### 10. Încredere & Metodologie — *Trust & Methodology Page*
Public-facing companion to `pipeline.html`: data freshness map, known gaps, methodology notes, change log. Honest about limitations (50-store API cap, sparse UATs, etc.). Trust > polish.

---

## Tier 4 — Cross-cutting foundations

These unlock several features above; do them early or in parallel:

- **Unit normalisation** (already in backlog) — prerequisite for #1, #3, #5
- **Network short-name config** (backlog) — UI-wide readability win
- **Romanian copy polish** — current site mixes RO and EN
- **Mobile-first responsive audit** — most Romanian users will arrive via mobile
- **Client-side search** (Lunr.js index) — by product / store / locality
- **Shareable card generator** — Open Graph PNGs per chart via Playwright in CI
- **Disclaimer banner** (backlog) — dismissable header notice

---

## Implementation order (proposed)

| Phase | Build | Why this order |
|-------|-------|----------------|
| **A** | Foundations (unit norm, network short names) → **#1 Basket** → **#3 Anomalies** | Shipping a clear citizen win first; both reuse normalised units |
| **B** | **#2 Choropleth Map** → **#6 Category Explorer** → **#5 Private Label** | Layers the journalist/researcher angle on top of #1 data |
| **C** | **#4 Civic CPI** → **#8 Stories** → **#9 Open Data Hub** → **#10 Trust** | Storytelling + ecosystem; needs ~1 month of accumulated history |
| **D** | **#7 Geolocation** → Search → Shareable cards → polish | UX layer on top of mature data surface |

---

## Stack decisions, per feature

| Feature | Stack | Rationale |
|---------|-------|-----------|
| Basket, Anomalies, Private Label, Category Explorer, Stories | Vanilla JS + Chart.js + JSON | Static, fast, fits current site |
| Choropleth Map (#2) | MapLibre GL JS + GeoJSON | Vector polygons, choropleth-native; coexists with Leaflet for store markers |
| Civic CPI (#4) | Observable Plot | Time-series + small multiples + confidence bands without ceremony |
| Search | Lunr.js (client-side) | No server; index built at site-gen time (~few hundred KB) |
| Story cards | Playwright in CI → static PNG | Already a project dependency; no runtime cost |
| Geolocation finder | Plain browser API + PWA shell | No backend; works offline once cached |

No SPA framework needed — keep `generate_site.py` as the source of truth, emitting per-page HTML. A SPA buys nothing here and would hurt SEO/shareability for a public-good site.

---

## Critical files

**Existing — to extend:**
- `generate_site.py` — main static generator; new page emitters per feature
- `db.py` — add basket / spread / index helpers
- `analyse_products.py` — extend with brand/private-label/category-tree analysis
- `docs/data/cross_network_spread.csv` — feeds #3
- `docs/data/private_labels.csv` — feeds #5
- `docs/data/products/*.csv` — already powering trends/compare; feeds #1
- `docs/backlog.md`, `docs/activity-log.md` — track work as per project rules

**New — to add:**
- `scripts/build_baskets.py` → `docs/data/baskets/*.json`
- `scripts/build_uat_geojson.py` → `docs/data/uats.geojson` (one-off, regenerate when UAT set changes)
- `scripts/build_civic_cpi.py` → `docs/data/civic_cpi.json`
- `scripts/build_anomalies.py` → `docs/data/anomalies_today.json` (daily)
- `scripts/build_search_index.py` → `docs/data/search.json` (Lunr index)
- `scripts/render_story_cards.py` (Playwright) → `docs/og/*.png`
- `docs/cos.html`, `docs/harta.html`, `docs/anomalii.html`, `docs/inflatie.html`, `docs/marca-privata.html`, `docs/categorii.html`, `docs/aproape.html`, `docs/povesti.html`, `docs/date-deschise.html`, `docs/metodologie.html`

---

## Verification (per-feature, end-to-end)

For each feature shipped:
1. **Data sanity** — SQL spot-check: e.g. for #1, manually reproduce basket cost for one (basket × UAT × network) tuple from `prices.db` and assert match with the rendered JSON.
2. **UI smoke test** — Chrome DevTools MCP: load page, verify KPIs render, take screenshot, check console for errors.
3. **Edge cases** — empty UAT (no stores), single-network UAT (food desert), product missing from network (substitution path), SELGROS exclusion verified.
4. **Mobile** — Chrome DevTools MCP `resize_page` to 375×812; check tap targets and chart legibility.
5. **Citation/share** — Open Graph card renders with correct headline; URL is permalinked and stable.
6. **Activity log entry** — `docs/activity-log.md` updated per project rules.

---

## What this isn't

- **Not a marketplace** — we don't link to "buy now"; we inform.
- **Not a real-time price tracker** — daily refresh is the contract.
- **Not personalised beyond UAT + basket choice** — no accounts, no tracking, no PII.
- **Not gas** — gas is its own roadmap, deferred to a later phase.
