# VPS `prices.db` retention (slimming runbook)

Written 2026-10-29 after the VPS root partition (38 GB) hit 100% with a 15.7 GB `prices.db`
(see `docs/activity-log.md`, same date). The crons were paused, and the fix took ~13 h of
fetch downtime, mostly because of a bad method (a 7 h `DELETE`).

## Current design

- **VPS `data/prices.db`**: last 60 days of `prices`, all of `prices_current`, plus the
  `price_daily_min` rollup (basket products, per date × product × network, **all history**).
  `build_cpi.py` reads the rollup, so the inflation index keeps its full history without raw rows.
- **Local Mac** `/Volumes/geana/bckpz/WRK/gov2/monitorulpreturilor/data/prices.db`: the
  **only full history** (through 2026-10-28 04:00 at the time of writing). See `docs/local-backup.md`.
- Rollup schema/backfill query live in `db.py` (`init_db`) and `build_cpi.py`.

## When to run this procedure

Run it when VPS `/` is above ~85% used, or `prices.db` exceeds ~10 GB. Do **not** wait for 100%:
at 100% no in-place VACUUM/DELETE/`.backup` is possible and you need a transfer window.
Growth is ~275k `prices` rows/day (~25 MB/day of table + indexes), `prices_current` is flat.

## Procedure (copy rows, never `DELETE`)

Rules that cost us hours: never `DELETE` a large share of a big table on a spinning disk
(36.6M rows ≈ 7 h and unfinished; copying the kept 17M rows took ~30 min). Never VACUUM or
`.backup` on the VPS when `/` is nearly full. Never `pgrep -f` over SSH without the
`[x]etch_` bracket trick (it matches its own command line). Open copies read-only with
`file:...?immutable=1` (`mode=ro` fails on a WAL DB with no `-wal/-shm`).

1. **Pause writers on the VPS** (comment the `fetch_prices`, `fetch_gas_prices`, `backup_db.sh`
   crons; wait for `pgrep -af "[f]etch_prices"` to be empty). Note the DB size/mtime.
2. **Get a consistent full copy locally** (new full copy or top-up of the existing one):
   `rsync -a --inplace --partial pax@VPS:g2-dev/monitorulpreturilor/data/prices.db <local>/data/prices.db`.
   Do not touch the file while it runs. Then `PRAGMA quick_check` (~15 min on the HDD) and compare
   per-table row counts with the VPS (`prices`, `prices_current`, `stores`, `products`, …).
   Do not eject the disk during checks.
3. **Build the slim DB** on the **internal SSD** (≈7.5 GB; HDD random writes are the bottleneck).
   Dump the schema with `sqlite3 <full> .schema` (drop `sqlite_sequence`), create a new DB from it
   plus `price_daily_min`, then `ATTACH` the full DB (`?immutable=1`) and:
   - copy every small table and `prices_current` whole: `INSERT INTO t SELECT * FROM src.t`;
   - copy only the window: `INSERT INTO prices SELECT * FROM src.prices NOT INDEXED WHERE price_date >= '<cutoff>'`
     (`NOT INDEXED` forces a sequential scan; the index would do random lookups);
   - backfill `price_daily_min` from **all** history using the exact query in `build_cpi.py`
     (this is the ~55 min step; if a previous rollup exists, reuse it and only add new dates);
   - copy `sqlite_sequence`, set `PRAGMA journal_mode=WAL` (the live DB is WAL), `PRAGMA quick_check`.
   Spot-check the rollup against the full DB for a few dates.
4. **Upload and swap**: with `/` nearly full there is no room for both DBs. Free space first
   (old dumps in `data/backups`, old logs), or `chown` the 15 GB `/mnt/HC_Volume_105444571` for
   staging; if neither, delete the old VPS `prices.db` only **after** the local full copy is verified.
   `rsync -az --partial --inplace slim.db VPS:.../data/prices.db.upload`, `quick_check` + row counts on
   the VPS, remove stale `prices.db-wal/-shm`, `mv prices.db.upload prices.db`.
5. **Resume**: re-enable the crons, confirm the next `fetch_prices` slice writes, run `build_cpi.py`
   to a scratch `--out` and compare `our_monthly` with the previous run.

Expected time on a good run: ~1.5 h, with downtime of 1 h (steps 2–5), not 13.

## What to do so this is sustainable

1. **Automate retention on the VPS** (backlog): a weekly job that keeps `price_daily_min` current,
   then deletes `prices` older than 60 days in small batches (e.g. 100k rows per transaction, by
   `price_date`, off-peak), followed by `PRAGMA incremental_vacuum` (needs `auto_vacuum=INCREMENTAL`,
   which requires one full rebuild-by-copy to enable). Small daily deletes are cheap; one huge delete is not.
2. **Alert on disk and DB size** (ntfy/healthchecks): warn at `/` > 85%, page at > 92%. A 100% disk
   was invisible until fetch_prices started failing.
3. **Make the local mirror incremental, not a re-copy** (backlog): periodically attach a VPS
   snapshot and `INSERT OR IGNORE` the new `prices` rows, refresh `prices_current`. Until then the local
   full DB stops growing after 2026-10-28 04:00, and `scripts/sync_from_vps.sh` skips the DB on purpose.
4. **Second copy of the full history** (backlog): it is on one external HFS disk. Add a second disk
   or an off-site copy, and schedule the sync with `hc_run.sh` so a missed run alerts.
5. **Drop or shrink `scripts/backup_db.sh`**: a gzipped `.dump` on the same partition protects only
   against logical errors, needs ≥4 GB free and a lot of time; the local mirror is the real backup.
6. **Larger disk** is the cheapest long-term fix if the 60-day window grows: ~10 GB of window DB per
   year of fields-as-is leaves little headroom on 38 GB; compare with the cost of a bigger volume.
7. **Keep the CPI series data-complete**: September 2026 has a visible gap (basket cost 189 vs ~320, 28 points).
   Fetch holes distort the published index; add a check on points per day for basket products.
