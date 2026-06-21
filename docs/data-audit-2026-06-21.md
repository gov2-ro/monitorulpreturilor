# Data Audit — 2026-06-21

Point-in-time audit of `data/prices.db` (7.3 GB) after ~68 days of scraping
(first run 2026-04-14, latest 2026-06-21). All figures from the live DB on
2026-06-21. Cross-network figures are built from the **current snapshot**
(`prices_current`, latest known price per product×store), restricted to
network-mapped stores. Helper aggregates were materialised once into a scratch
DB; the queries are reproducible from the SQL in the activity-log entry of the
same date.

---

## TL;DR

- **Shops monitored: 5,460** — 4,135 retail stores (10 chains) + 1,325 fuel
  stations (7 networks). 4,094 retail stores have price data.
- **Products: 87,775 catalogued, 75,710 currently priced.** Only **23,200
  (31%) are sold by ≥2 chains** — the rest are chain-exclusive/private-label.
- **Same price across networks (retail): rare and mostly coincidental.** Of the
  23,200 comparable products, **1,916 (8.3%)** share an identical most-common
  price across *every* chain that stocks them — but **93% of those are just
  two-chain coincidences**. Genuine multi-chain price lockstep (≥3 well-stocked
  chains) is only **55 products**, all RRP-driven branded FMCG.
- **The opposite is the norm:** among well-stocked comparable products, only
  **4.1%** are effectively the same price across chains; **~83% differ by >5%**
  and **~70% by >10%**. Cross-network price comparison is worthwhile.
- **Fuel is the real "same price" market:** across all 7 networks, standard
  diesel varies just **1.2%** and LPG **0.9%**; five of seven networks quote
  *identical* standard-petrol prices to the cent.
- **Biggest data-quality gap:** **996 stores (24%) have no network attribution**
  (up from 243 a month ago), removing **~2.9M current prices (~19% of the
  snapshot)** from all cross-network analysis.

---

## 1. Scope & freshness

| | |
|---|---|
| Operational span | 2026-04-14 → 2026-06-21 (68 days) |
| Retail price rows (history) | 22,560,334 |
| Retail current snapshot | 15,536,700 |
| Gas price rows (history) | 123,541 |
| Retail runs / gas runs | 875 / 57 |

The retail snapshot is a **rolling ~30-day window**, not a single-day capture
(the re-entrant cron sweeps the store base over multiple days):

| Last re-checked | Rows | Share |
|---|---|---|
| ≤ 1 day | 4.54M | 29% |
| ≤ 7 days | 6.07M (cum.) | 39% |
| ≤ 14 days | 7.98M (cum.) | 51% |
| ≤ 30 days | 14.10M (cum.) | 91% |
| > 30 days (stale) | 1.43M | 9% |

`price_date` (the date the *retailer* set the price) is sticky — many products
still carry April dates, which is normal for retail. (Note: pre-2026-05-23 rows
store `price_date` as `DD.MM.YYYY` text; the audit uses ISO `last_checked_at`
for freshness, so this does not affect the figures.)

## 2. Shops

**Retail — 4,135 stores across 10 chains** (4,094 priced; 3,139 network-mapped):

| Network | Stores | Network | Stores |
|---|---|---|---|
| PROFI | 1,334 | PENNY | 158 |
| *(unmapped)* | 996 | AUCHAN | 51 |
| MEGA IMAGE | 972 | SUPECO | 26 |
| LIDL | 194 | SELGROS | 23 |
| KAUFLAND | 191 | CORA | 13 |
| CARREFOUR | 177 | | |

**Gas — 1,325 stations across 7 networks:** PETROM 366, LUKOIL 303, MOL 218,
Rompetrol 176, OMV 162, SOCAR 81, Gazprom 19.

**Total monitored shops: 5,460.**

## 3. Products & cross-network coverage

- **87,775** products catalogued; **75,710** have a current price; ~12,065 are
  catalogue-only (discontinued / never fetched).
- Distribution by number of chains carrying a product (mapped networks):

  | Chains | Products | | Chains | Products |
  |---|---|---|---|---|
  | 1 | 51,795 | | 6 | 1,464 |
  | 2 | 10,417 | | 7 | 976 |
  | 3 | 4,652 | | 8 | 561 |
  | 4 | 2,758 | | 9 | 299 |
  | 5 | 1,995 | | 10 | 78 |

- **51,795 (69%) are single-chain** — private label or chain-exclusive
  assortment. By chain: AUCHAN 11,811, PROFI 10,860, CORA 6,148, KAUFLAND
  5,703, MEGA IMAGE 5,021, CARREFOUR 4,276, SELGROS 3,524, LIDL 2,153,
  PENNY 1,685, SUPECO 614. (Note: catalogue breadth, not store count — CORA has
  13 stores but 6,148 unique products.)
- **23,200 products (31%) are sold by ≥2 chains** — the universe where
  cross-network price comparison is meaningful. Only **78 products are carried
  by all 10 chains.**

## 4. Products with the SAME price across networks (retail)

"Same price" = identical **modal** (most common) price in every chain that
stocks the product. Among the 23,200 comparable products:

| Definition | Products | % of comparable |
|---|---|---|
| Identical modal price across all chains | **1,916** | 8.3% |
| …within 1% | 2,302 | 9.9% |
| …within 5% | 4,943 | 21.3% |
| Identical price at *every single store* | 656 | 2.8% |

**Caveat — most "agreement" is thin.** The 1,916 break down by how many chains
actually agree:

| Chains agreeing | Products |
|---|---|
| 2 | 1,777 (93%) |
| 3 | 133 |
| 4 | 5 |
| 5 | 1 |

Filtering to a **robust core** (≥3 chains, each with ≥3 stores quoting the
price) leaves just **55 products**. These are almost entirely
manufacturer-branded FMCG with strong recommended-retail-price adherence —
e.g. Magnum / Milka / La Strada ice cream, L'Or / Nesquik / Santi coffee,
branded wines (Jidvei, Byzantium), Bonne Maman jam, branded protein salads.
No staples (bread, milk), no tobacco in the top set. Prices are realistic
(0.29–2,500 RON), not placeholder artifacts.

**Conclusion:** identical cross-chain pricing is a niche phenomenon driven by
brand RRP, not a general property of the market.

## 5. The counterpoint — prices mostly DIFFER (retail)

Among **19,966 well-stocked comparable products** (≥2 chains, each with ≥3
stores), cross-network spread of the per-chain average price:

| Spread | Products | Share |
|---|---|---|
| < 1% (effectively same) | 823 | 4.1% |
| 1–5% | 2,564 | 12.8% |
| 5–10% | 2,666 | 13.4% |
| 10–25% | 6,112 | 30.6% |
| 25–50% | 4,429 | 22.2% |
| > 50% | 3,372 | 16.9% |

**~83% of comparable products differ by more than 5% across chains; ~70% by
more than 10%.** This validates the whole premise of the service. (The very
large bands >25% likely conflate promo-timing differences and some data noise,
not pure base-price gaps — see caveats. This is consistent with the earlier
`price_variability_analysis.md`, which found 53% of inter-network variance ≥10%
on the full history.)

### 5b. Base-price view — promotional rows stripped

Per the decision to compare base prices only, §4–§5 were recomputed excluding
the 8.7% of rows flagged `promo`. (This drops 1,703 products whose *only*
current prices were promotional, and 903 products from the comparable universe →
22,297 multi-chain products.)

| Metric | With promos | Base price |
|---|---|---|
| Comparable products (≥2 chains) | 23,200 | 22,297 |
| Identical modal price across all chains | 1,916 | **1,935** |
| …within 1% | 2,302 | 2,349 |
| …within 5% | 4,943 | 5,154 |
| Robust core (≥3 chains, ≥3 stores) | 55 | **57** |

**Strict "same price" barely moves** — RRP-fixed branded goods are rarely
promo'd, so promotions weren't the cause of cross-chain price identity.

Base-price spread (well-stocked, ≥2 chains each ≥3 stores; n = 18,966):

| Spread | With promos | Base price |
|---|---|---|
| < 1% (effectively same) | 4.1% | **5.1%** |
| 1–5% | 12.8% | 14.3% |
| 5–10% | 13.4% | 15.2% |
| 10–25% | 30.6% | 33.7% |
| 25–50% | 22.2% | 20.0% |
| > 50% | 16.9% | **11.7%** |

**Stripping promos shrinks the large-spread tail** (>50% band falls 34%,
3,372 → 2,221): a meaningful share of apparent cross-chain "differences" was one
chain running a promotion. Even so, on base prices **~81% of comparable products
still differ by >5% and ~65% by >10%** — the comparison premise holds.

## 6. Fuel — the genuine "same price" market

Fuel is near-uniform across all 7 networks (current per-network average, RON/L):

| Fuel | Networks | Cheapest | Dearest | Spread |
|---|---|---|---|---|
| Motorină standard (diesel) | 7 | 9.03 | 9.14 | **1.2%** |
| GPL (LPG) | 5 | 4.49 | 4.53 | **0.9%** |
| Motorină premium | 7 | 9.80 | 10.20 | 4.1% |
| Benzină standard (petrol) | 7 | 8.47 | 8.99 | 6.1% |
| Benzină premium | 7 | 8.95 | 9.85 | 10.0% |

For standard petrol, **five of seven networks (MOL, LUKOIL, OMV, Rompetrol,
SOCAR) quote an identical 8.53 RON/L** average; PETROM undercuts at 8.47,
Gazprom sits high at 8.99 — textbook oligopoly price-matching with one
discounter and one premium outlier.

## 7. Data-quality findings (action items)

1. **Unmapped stores grew 243 → 996** (`network_id IS NULL`; backfill last
   reported 243 on 2026-05-23). 964 of them are priced, holding **~2.9M current
   prices (~19% of the snapshot)** that are invisible to every cross-network
   comparison. Names are bare addresses/banners ("MARKET …", "STR. …"). Likely
   cause: resumed `discover_stores.py` adding stores faster than the name-pattern
   backfill tags them. → backlog item filed.
2. **Rolling-window snapshot**, not single-day — 9% of current rows are >30 days
   stale. Fine for sticky retail prices, but cross-network comparisons mix
   prices captured up to ~4 weeks apart, which can create artificial spread when
   a price changed in between.
3. **SELGROS (B2B wholesale, 23 stores) is included** in the cross-network
   figures. Bulk packs/pricing can distort *spread* (not *sameness*); the
   existing `v_cross_network_spread` view already excludes it. Consider
   excluding it from consumer-facing comparisons.
4. **`migrate_price_dates.py` still unrun on VPS** — old rows remain
   `DD.MM.YYYY` text. Not blocking this audit but a footgun for any
   `price_date`-range query (sorts lexically).

---

## Open questions for the user

1. **Definition of "same price across networks"** — this audit used the modal
   price per chain. Prefer a different basis (median, or strict every-store
   identity)? The 55-product robust core is the most defensible "truly fixed"
   set.
2. **Should the 996 unmapped stores be force-tagged** (e.g. ATAC → its parent,
   or via reverse-geocode/UAT) so their ~2.9M prices enter the comparison, or
   left out as genuinely-unknown?
3. **Promo vs base price** — the snapshot mixes promotional and regular prices.
   Want a follow-up that strips `promo` rows before computing cross-network
   spread, for a cleaner base-price comparison?
4. **Is fuel the headline product?** Its near-uniform pricing is a strong,
   clean, daily-moving signal — arguably a better flagship than the noisy
   long-tail retail catalogue.
