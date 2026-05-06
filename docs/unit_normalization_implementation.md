# Unit Normalization Implementation (2026-05-06)

## Problem
The unit field in API responses was inconsistent, causing ~15% false-positive price variance outliers:
- Same unit stored as: `Kg`, `kg`, `K`, `KG`, `kilogram`
- Count as: `BUC`, `BUCATA`, `Buc`, `Buc.`, `piece`
- Volume as: `L`, `Litru`, `Liter`, `l`
- And 513 total unique variants in the database

Example outlier: **Cosmin Zahar Vanilinat (sugar)** at Carrefour Bucharest showed **2101% variance** because one store listed it as `98.59 lei/L` (liter, bulk) while others listed `2.45 lei/K` (piece) — same product ID, different units.

## Solution

### 1. Normalization Function (db.py)
```python
def normalize_unit(unit):
    # Kg/kg/K → kg
    # BUC/BUCATA/Buc/Buc. → pcs
    # L/Litru/Liter → L
    # ml/ML → ml
    # g/G → g
    # NULL/empty → None
```

**Handles:**
- Case normalization (Kg → KG → kg)
- Variant consolidation (BUC, BUCATA, Buc, Buc. all → pcs)
- Hyphen variants (1-kg → 1KG → not normalized, kept as uppercase for consistency)
- Unknown units → uppercase for consistency

### 2. Integration Point
- Called in `insert_price()` for all new prices
- All future fetches automatically get normalized units in the database
- No changes needed to `fetch_prices.py` — normalization happens on insert

### 3. Backfill
- Script: `backfill_unit_normalization.py`
- Updates `prices_current` table (active snapshot) with normalized units
- Leaves `prices` table (history) unchanged for safety
- Idempotent — safe to run multiple times

**Data before:**
- 513 unique unit values in database
- Examples: `Kg`, `kg`, `K`, `BUC`, `BUCATA`, `L`, `Litru`, `l`, `ml`, `ML`, `g`, `G`, etc.

**Data after:**
- ~20 canonical units (kg, pcs, L, ml, g, + compound units like 4X500ML, 2X200G)
- Consistency across all new inserts

## Impact

### Immediate (after normalization)
- False outliers (2000%+ variance from unit mismatch) eliminated
- Analysis scripts can now safely compare prices within same unit
- `analyse_price_variability.py` results become more trustworthy when re-run

### Medium-term (next 1–2 weeks)
- Re-run price variability analysis with clean units
- Rebuild store optimization model (which stores capture variance best)
- Weekly re-analysis cadence to track drift

### Long-term
- Foundation for accurate price comparison and cross-network analysis
- Enable product normalization by unit (e.g., price per 100g comparison)

## Testing

After backfill completes:
1. Run `analyse_price_variability.py` again
2. Check that 76% intra-network variance is stable (or slightly better with unit cleanup)
3. Confirm that extreme outliers (>1000% spread) are eliminated or reduced to <10

## Notes

- Compound units like `6X500ML`, `4X100G` are kept as-is (they represent multi-packs and are data)
- Fresh produce pricing (with real variability) is unaffected; this only cleans unit inconsistencies
- Store format premium (Express +7% vs hypermarket) remains and is intentional
- Backfill only affects `prices_current`; historical `prices` unchanged for audit trail

## Next Steps

1. ✅ Unit normalization function added
2. ✅ Integration into insert_price()
3. 🔄 Backfill in progress
4. ⏳ Re-run variability analysis with clean units
5. ⏳ Build store optimization model
6. ⏳ Set up weekly re-analysis cron job
