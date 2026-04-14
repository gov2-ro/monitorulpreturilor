# Plan: Checkpoint/resume + last-checked price tracking

## Context
The gas pipeline is already implemented and working. The next task is:
1. **Checkpoint/resume**: both `fetch_prices.py` and `fetch_gas_prices.py` should save progress to a JSON file so an interrupted run can resume from where it left off. A `--fresh` flag forces a clean run ignoring any checkpoint.
2. **Last-checked tracking**: prices should record `last_checked_at` so we know the price was still valid when last confirmed, even if it didn't change. New prices get inserted; existing prices (same product+store+price_date) get their `last_checked_at` updated via UPSERT.

---

## Changes

### `db.py`

**Table changes** — add `last_checked_at TEXT` column to `prices` and `gas_prices`:
```sql
-- prices table gets:
last_checked_at TEXT
-- gas_prices table gets:
last_checked_at TEXT
```

**Migration for existing DBs** — `init_db` runs `ALTER TABLE` with try/except since SQLite doesn't support `IF NOT EXISTS` for `ADD COLUMN`:
```python
for ddl in [
    "ALTER TABLE prices ADD COLUMN last_checked_at TEXT",
    "ALTER TABLE gas_prices ADD COLUMN last_checked_at TEXT",
]:
    try:
        conn.execute(ddl)
    except sqlite3.OperationalError:
        pass  # column already exists
conn.commit()
```

**`insert_price`** — change from `INSERT OR IGNORE` to UPSERT:
```sql
INSERT INTO prices (product_id, store_id, price, price_date, promo, brand, unit,
                    retail_categ_id, retail_categ_name, fetched_at, last_checked_at)
VALUES (?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(product_id, store_id, price_date)
DO UPDATE SET last_checked_at = excluded.fetched_at
```
(`fetched_at` stays as the original insert time; `last_checked_at` is updated on every re-check)

**`insert_gas_price`** — same pattern:
```sql
INSERT INTO gas_prices (product_id, station_id, price, price_date, fetched_at, last_checked_at)
VALUES (?,?,?,?,?,?)
ON CONFLICT(product_id, station_id, price_date)
DO UPDATE SET last_checked_at = excluded.fetched_at
```

Both helpers get an extra `last_checked_at` parameter (callers pass `fetched_at` for it).

---

### Checkpoint helpers (inline in each script, ~20 lines)

Checkpoint file format:
```json
{"fetched_at": "2026-04-14T10:00:00+00:00", "done": ["1017:0", "1017:1"]}
```
- `fetched_at` is preserved across resume so all rows from one logical run share the same timestamp
- `done` is a set (serialised as list) of completed work-unit keys

Functions to add inside each script:
```python
import json, os

def _load_checkpoint(path):
    if os.path.exists(path):
        with open(path) as f:
            data = json.load(f)
        data["done"] = set(data["done"])
        return data
    return None

def _save_checkpoint(path, fetched_at, done):
    with open(path, "w") as f:
        json.dump({"fetched_at": fetched_at, "done": sorted(done)}, f)

def _clear_checkpoint(path):
    if os.path.exists(path):
        os.remove(path)
```

---

### `fetch_prices.py`

Checkpoint file: `data/retail_checkpoint.json`  
Work-unit key: `f"{uat_id}:{batch_idx}"`

Logic changes:
1. Add `--fresh` flag to argparse
2. At start of `main()`:
   - If `--fresh` or no checkpoint: generate new `fetched_at`, empty `done` set
   - Else: load checkpoint, restore `fetched_at` and `done`, print "Resuming from checkpoint (N done)"
3. In the batch loop: skip if key in `done`; after `conn.commit()`, add key to `done` and `_save_checkpoint()`
4. After all UATs complete: `_clear_checkpoint()`

Skip message when resuming: `tqdm.write(f"  {uat_name} batch {i}: skipped (checkpoint)")`

---

### `fetch_gas_prices.py`

Checkpoint file: `data/gas_checkpoint.json`  
Work-unit key: `f"{uat_id}:{fuel_id}"`

Same logic as retail. After all UATs complete: `_clear_checkpoint()`.

---

## Files to modify
| File | Change |
|------|--------|
| `db.py` | Add `last_checked_at` to tables; add migration; change both insert helpers to UPSERT |
| `fetch_prices.py` | Add checkpoint load/save/clear + `--fresh` flag |
| `fetch_gas_prices.py` | Add checkpoint load/save/clear + `--fresh` flag |

---

## Verification
1. Run `python fetch_gas_prices.py --limit-uats 2` — interrupt partway through (Ctrl-C)
2. Check `data/gas_checkpoint.json` exists and lists completed work units
3. Run again without `--fresh` — should skip already-done units, print "Resuming..."
4. Run to completion — checkpoint file should be deleted
5. Run `python fetch_gas_prices.py --limit-uats 2 --fresh` — fresh `fetched_at`, starts from scratch
6. Query DB: `SELECT product_id, station_id, price_date, fetched_at, last_checked_at FROM gas_prices LIMIT 5;`
   - `last_checked_at` should be populated on re-run even when price didn't change
