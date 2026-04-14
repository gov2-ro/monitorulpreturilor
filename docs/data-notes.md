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
