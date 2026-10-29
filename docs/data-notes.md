# Data Notes

Observations about data quality, anomalies, and quirks in the API data.
These are not tasks — see `backlog.md` for actionable items.

---

## Category miscategorizations

Detected automatically by `analyse_products.py` (threshold: 80% brand category dominance).
Full list: `data/category_anomalies.csv` (re-generated each run).

### Confirmed miscategorizations

- **JACOBS** (coffee brand) — 2 products (`Jacobs Cafea Boabe Barista Crema 1kg`,
  `Jacobs Cafea Boabe Barista Espresso 1kg`) appear under **BAUTURI ALCOOLICE** instead of
  **CAFEA, CEAI** (93% of Jacobs products). Likely a data entry error in the API catalogue.

- **LAVAZZA** (coffee brand) — 1 product (`R&B Paine Alba Tava Fl 400g`) appears under
  **PANIFICATIE**. The product name looks like a bread product attributed to LAVAZZA by mistake
  — probably a brand field error in the price response.

- **HEINZ** — `Baza Mancare Legume 132ml Magg` appears under **ALIMENTE DE BAZA**,
  dominant category is **PANIFICATIE** (90%). Likely the `categ_id` fallback picked up the
  wrong category when this product was fetched under multiple category queries.

- **KEINE MARKE** ("no brand" in German — a placeholder) — products span
  COFETARIE, CAFEA, and ALIMENTE DE BAZA. The brand field is being used inconsistently
  as a null/unknown marker.

### Borderline / legitimate multi-category brands

- **BOROMIR** — primarily pastries/desserts (81%) but also makes flour (`Faina 000`) and
  bread products. The flour and bread assignments to ALIMENTE DE BAZA / PANIFICATIE are
  probably correct, not errors.

- **DIAMANT** — primarily ALIMENTE DE BAZA (sugar, etc.) but some products end up in
  COFETARIE (powdered sugar, cinnamon sugar). Borderline — both are reasonable.

- **DR OETKER** — legitimately spans ALIMENTE DE BAZA, COFETARIE, and PANIFICATIE.
  Not flagged as anomaly (below 80% dominance threshold) but worth noting.

---

## Brand field quality

- **858 raw distinct brand values** collapse to ~650 normalized brands after stripping
  case differences and punctuation variations.
- Worst offender: **DR OETKER** has 6 variants (`DR OETKER`, `Dr. Oetker`, `DR.OETKER`,
  `DR. OETKER`, `Dr Oetker`, `Dr.Oetker`) totalling 1584 price rows.
- **M&M'S** has 7 variants including `M&M\`S`, `M&M s`, `MMS`.
- **KEINE MARKE** appears as a brand placeholder (German for "no brand") — these rows
  have no meaningful brand and should be filtered from brand analysis.

---

## Unit field quality

The `unit` field in `prices` is inconsistent across networks:

| Concept | Variants seen |
|---------|--------------|
| kilogram | `Kg`, `K`, `kg`, `1kg` |
| piece | `BUC`, `BUCATA`, `Buc`, `Buc.`, `BU` |
| litre | `Litru`, `L`, `l`, `1l` |

This makes raw price comparison across networks meaningless without normalization.
SELGROS unit sizes are typically bulk/wholesale quantities, incomparable to consumer packs.

---

## Network/store data

- **412 stores have `network_id = NULL`** — their network name is often embedded in
  `stores.name` (e.g. "Kaufland Oradea", "Supeco Craiova"). See backlog for backfill task.
- Network IDs are inconsistent: some are clean strings (`PROFI`, `KAUFLAND`) while others
  are EAN-style codes (`5940475006709` = CARREFOUR).

## Basket coverage before 2026-09-28 (CPI / inflation index caveat)

Known data-quality issue, found 2026-10-29. **Do not read the pre-October CPI month-on-month
figures at face value** (published `our_monthly`: 2026-09 cost 189.24, −42.3% MoM; 2026-10 +70.4%).

- Cause: fetch coverage, not prices. Before the fetch fixes of 2026-09-28 (`0348e41`, `ce32994`: the
  product-tier rule "tiered out 99% of the catalogue"; weekly-tier stores never fetched; frozen
  sentinel `price_date`), only ~20 of the 38 basket products had a price on a typical day;
  from 2026-09-28 it is ~33. The `price_daily_min` rollup shows it: 30–48 product×network rows/day
  up to 27 Sep, 100–200 from 28 Sep.
- Mechanism: `build_cpi.py` takes the **median of the daily basket costs** per month and drops items
  without a price (no imputation). In September the heaviest `camara` item (Pâine Boierească Albă 700g,
  `qty_per_week` 4, ~8–11 lei, ≈139 lei/month) had a price on only 13 of 28 days, so the monthly median
  flipped to the "item missing" version. Other items (salt `1026100`, vinegar `1029251`, flour `1014679`)
  were intermittent too. Aug and earlier were built on the same low coverage.
- Dealing with it when analysing: compare months only on items present in both; or restrict to dates
  ≥ 2026-09-28; check `items_found` per day in `site/data/cpi.json` (`baskets[].series[]`). A
  carry-forward (LOCF ≤14 d) + weighted-coverage ≥80% "comparable" rule in `build_cpi.py` would fix the
  index itself (not done: see `docs/backlog.md`).
- Raw rows before 2026-08-29 are no longer on the VPS (slim DB); the rollup keeps basket history.
  The full raw history is in the local mirror (`docs/local-backup.md`).
