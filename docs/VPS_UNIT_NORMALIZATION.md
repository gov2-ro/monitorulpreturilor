# VPS Unit Normalization Runbook

**Status:** Backfill in progress on dev machine; runbook for VPS deployment ready.

## Background

Unit field inconsistency in retail prices (Kg/kg/K, BUC/BUCATA, L/Litru, etc.) causes ~15% false-positive price variance outliers. Normalization maps 513 variants → ~20 canonical forms (kg, pcs, L, ml, g).

## On Dev Machine (now)
- Backfill script running: `backfill_unit_normalization.py`
- Target: `data/prices.db` (local)
- Updates `prices_current` table with normalized units
- ETA: ~6 hours for 12.8M rows (SQLite is thorough)

## Deployment to VPS

### Step 1: Verify Local Backfill
Once the local backfill completes:
```bash
sqlite3 data/prices.db "SELECT DISTINCT unit FROM prices_current ORDER BY unit;" 
# Should show ~20 canonical units (kg, pcs, L, ml, g, + compounds)
# NOT 513 messy variants
```

### Step 2: Git Commit & Push
```bash
git add db.py backfill_unit_normalization.py docs/unit_normalization_implementation.md docs/VPS_UNIT_NORMALIZATION.md
git commit -m "refactor: normalize unit field (Kg/kg/K → kg, BUC/BUCATA → pcs, L/Litru → L)"
git push origin main
```

### Step 3: Deploy to VPS
SSH to VPS:
```bash
cd ~/g2-dev  # or wherever the repo is
git pull origin main
```

Code changes are now live. All *new* prices will be normalized on insert.

### Step 4: Backfill VPS Database
On VPS, run the backfill against the production database:
```bash
cd ~/g2-dev
source ~/devbox/envs/240826/bin/activate  # or VPS Python env
python backfill_unit_normalization.py
```

**Time estimate:** 6–12 hours (depends on VPS disk I/O and system load)

**Safe to run during:**
- Off-peak hours (not during a fetch cycle)
- Or after the next `fetch_prices.py` + `fetch_gas_prices.py` complete

**Safe to interrupt:** Yes. The script updates `prices_current` which is a snapshot table. If interrupted, just re-run — it's idempotent.

### Step 5: Verify VPS Backfill
On VPS, after backfill completes:
```bash
sqlite3 data/prices.db "SELECT COUNT(*) FROM prices_current; SELECT DISTINCT unit FROM prices_current ORDER BY unit LIMIT 30;"
```

Should show canonical units, not 513 variants.

## Rollback (if needed)

If something goes wrong, the original data is safe:
1. `prices` table (history) was not modified
2. `prices_current` can be regenerated from `prices` if needed (though it would take time)

To re-populate `prices_current` from scratch:
```sql
DELETE FROM prices_current;
INSERT INTO prices_current 
SELECT DISTINCT ON (product_id, store_id) 
  product_id, store_id, price, price_date, promo, brand, unit, 
  retail_categ_id, retail_categ_name, fetched_at, fetched_at
FROM prices 
ORDER BY product_id, store_id, price_date DESC;
```

Then re-run backfill.

## After Normalization

All future fetches will automatically normalize units on insert (code change in `insert_price()`). No more manual intervention needed.

**Next steps:**
1. Weekly re-run of `analyse_price_variability.py` to track variance patterns
2. Build store optimization model (identify minimal store subset needed)
3. Consider scheduling the weekly analysis via cron

---

**Documentation:** See `docs/unit_normalization_implementation.md` for technical details and `docs/price_variability_analysis.md` for the analysis that informed this work.
