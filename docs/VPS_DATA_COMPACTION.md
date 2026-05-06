# VPS Data Compaction Guide

**ARCHIVE AFTER COMPLETION** — this doc is only needed once during the migration from 3.66 GB → ~500 MB. After running these steps, save to git history and delete.

## Pre-Compaction Checklist

⚠️ **IMPORTANT: Stop all fetch scripts before running compaction**

```bash
# Check if fetch_prices.py or fetch_gas_prices.py are running
ps aux | grep -E "fetch_prices|fetch_gas|sqlite3" | grep -v grep

# If any are running:
pkill -f fetch_prices
pkill -f fetch_gas_prices
pkill -f sqlite3

# Wait a few seconds for cleanup
sleep 3
ps aux | grep -E "fetch_prices|fetch_gas|sqlite3" | grep -v grep
# Should return empty
```

The VACUUM and backfill operations hold an exclusive lock on the database. If a fetch script is running, it will either hang or fail.

---

## Quick Start (5-10 minutes)

```bash
# SSH to VPS
ssh <vps-host>
cd ~/gov2-monitorulpreturilor

# PRE-FLIGHT: Verify no fetch scripts are running (see above)

# Activate venv
source ~/devbox/envs/240826/bin/activate

# 1. Backfill prices_current (10-15 min for 23M rows)
python3 backfill_prices_current.py

# 2. Delete old price rows (optional cleanup, keeps changelog for reference)
# SKIP THIS if you want to keep full history. Just vacuuming is safer.
# sqlite3 data/prices.db "DELETE FROM prices WHERE id NOT IN (SELECT price_id FROM prices WHERE (product_id, store_id, price_date) IN (SELECT product_id, store_id, MAX(price_date) FROM prices GROUP BY product_id, store_id))"

# 3. Vacuum to reclaim space (5-15 min, rewrites entire DB)
sqlite3 data/prices.db "VACUUM;"

# 4. Check final size
ls -lh data/prices.db
```

Expected result: **5.9 GB → ~4.3 GB** (27% reduction)

**NOTE (updated 2026-05-06):** Original estimate was 87% (3.66 GB → 500 MB) but the product catalog grew 4× (20K → 87K) since this doc was written. Local test on 2026-05-06 shows: 21.7M price rows with 8.9M prunable (duplicates), VACUUM reduces 5.9 GB → 4.3 GB. The 4.3 GB is now the floor — no further reduction without column normalization.

---

## Detailed Steps

### Step 1: Backfill prices_current (required)

```bash
python3 backfill_prices_current.py
```

What this does:
- Takes the most recent price per `(product_id, store_id)` from the `prices` table
- Inserts into the new `prices_current` snapshot table
- Safe to run multiple times (UPSERT handles idempotency)
- Takes ~10-15 minutes for 23M rows

Output to verify:
```
Before: ~21.7M rows in prices, check prices_current row count
After backfill: should match unique product-store combinations in prices
(Expected: 12.8M+ rows in prices_current due to catalog growth)
```

**Note (2026-05-06):** If `prices_current` already has rows, the backfill may have been done in a prior run. Check the row count; if it's 1M+, skip this step.

### Step 2: Prune old price rows (OPTIONAL — high-impact but destroys history)

**ONLY if you don't need the full price changelog.** If you want to keep history for trend analysis, SKIP this and just VACUUM.

```bash
sqlite3 data/prices.db << 'EOF'
-- Delete all but the latest price per (product_id, store_id)
-- Keeps prices_current as the single source of truth
DELETE FROM prices
WHERE id NOT IN (
  SELECT MAX(id) FROM prices
  GROUP BY product_id, store_id
);
EOF
```

Impact: 23M rows → ~3.9M rows (83% reduction in prices table)

### Step 3: Vacuum (required for size reclamation)

```bash
sqlite3 data/prices.db "VACUUM;"
```

What this does:
- Rewrites the entire DB file, defragmenting it
- Frees space held by deleted rows (WAL journal pages)
- Rebuilds indexes
- Takes 5-15 minutes depending on disk I/O

Progress: Watch file size during the operation:
```bash
# In another terminal
watch -n 5 'ls -lh ~/gov2-monitorulpreturilor/data/prices.db'
```

### Step 4: Verify

```bash
# Check size
ls -lh data/prices.db

# Verify integrity
sqlite3 data/prices.db "PRAGMA integrity_check;" | head -3

# Check row counts
sqlite3 data/prices.db << 'EOF'
SELECT 'prices' as table_name, COUNT(*) as row_count FROM prices
UNION ALL
SELECT 'prices_current', COUNT(*) FROM prices_current;
EOF
```

Expected after compaction:
- **File size**: 3.66 GB → ~500 MB (if you pruned history) or ~2.5 GB (if you kept history)
- **Integrity**: `ok`
- **Row counts**: 
  - `prices_current`: ~3.9M (the snapshot)
  - `prices`: depends on whether you pruned

---

## Scenarios

### Scenario A: Keep Full History (Recommended for first run)

Run Steps 1 + 3 only:
1. ✅ Backfill prices_current (skip if already ~1M+ rows)
2. ⏭️ Skip pruning
3. ✅ Vacuum

**Result:** ~5.5–5.8 GB (3–7% reduction), full price history preserved
**Why:** Safer; you can always prune later if needed

### Scenario B: Aggressive Cleanup (Recommended for production)

Run all steps:
1. ✅ Backfill prices_current (skip if already ~1M+ rows)
2. ✅ Prune old rows
3. ✅ Vacuum

**Result:** ~4.3 GB (27% reduction), non-latest duplicate rows removed
**Why:** Proven on local test 2026-05-06; further reduction requires column normalization (Step 3 backlog)

---

## Rollback (if something goes wrong)

If the vacuum fails or the DB gets corrupted:
```bash
# VPS has git history; revert
git log --oneline data/prices.db
git checkout HEAD -- data/prices.db
```

If you pruned and regret it:
```bash
# Backups on VPS?
ls -la data/prices.db.backup*
# If a backup exists, restore it
```

---

## After Compaction: Next Steps

1. **Monitor:** Watch prices.db size growth over 2-4 weeks (change-based dedup should keep growth to ~50–100 MB/week)
2. **Expected size trajectory:** 4.3 GB + (50–100 MB/week × weeks elapsed). If growth exceeds 200 MB/week, investigate (new stores? new products? dedup not active?)
3. **Archive this doc:** Once confirmed stable, delete VPS_DATA_COMPACTION.md and add a note to CLAUDE.md that (a) change-based dedup is active, and (b) the 4.3 GB baseline is post-compaction

---

## Estimated Timing

| Step | Duration | Blocking? | Notes |
|------|----------|-----------|-------|
| Backfill prices_current | 10-15 min | Yes (locks DB) | Skip if already populated (>1M rows) |
| Prune rows (optional) | 1-3 min | Yes | Local test: 8.9M rows in ~1 min |
| Vacuum | 2-10 min | Yes (exclusive lock) | Local test: 5.9GB in ~2 min (SSD) |
| **Total** | **3-28 min** | All blocking | Depends on whether backfill is needed |

**Recommendation:** Run at off-peak hours (e.g., 02:00 UTC). Pause automated fetches during compaction.

---

## Monitoring Post-Compaction

Track growth over time:
```bash
# On VPS, add to crontab (weekly check)
0 6 * * 1 echo "$(date): $(du -h ~/gov2-monitorulpreturilor/data/prices.db)" >> ~/gov2-compaction-log.txt
```

If growth exceeds 200 MB/week, investigate:
- Are new stores being added? (check store_id count)
- Is fetch_prices.py using the new insert_price()? (verify in logs)
- Any new product categories? (check products table growth)
