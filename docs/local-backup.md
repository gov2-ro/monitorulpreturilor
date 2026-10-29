# Local backup: VPS → Mac external volume

Target: `/Volumes/geana/bckpz/WRK/gov2/monitorulpreturilor/data/` (mirrors the VPS `data/` layout).
Source: `pax@78.46.142.169:g2-dev/monitorulpreturilor/data/`.

> **WARNING (2026-10-29):** the VPS `prices.db` is now a 60-day slim DB. The local
> `.../data/prices.db` is the ONLY full history. `sync_from_vps.sh` therefore skips the DB
> by default (`SKIP_DB=1`); do not pass `SKIP_DB=0` — it would overwrite the full history with
> the slim one. Merging new rows from the VPS into the local full DB is a TODO (docs/backlog.md).
> Never delete or eject-while-reading the local copy. Build big trims by copying rows into a
> fresh DB (`INSERT … SELECT`), not by `DELETE` (7 h vs 30 min on this spinning disk).

## Run

```bash
NTFY_TOPIC=<your-topic> scripts/sync_from_vps.sh   # small files only (default, ~260 MB)
```

What it does: preflight (volume mounted, VPS reachable, no fetch/backup running) →
rsync small files (excl. `prices.db*`, `backups/`) → `rsync --inplace` of `prices.db`
(delta transfer against the existing local copy) → `PRAGMA quick_check` + compare
`max(price_date)` with the VPS → ntfy notification (OK = default priority, any failure = high).

## Rules (learned the hard way)

- **Never rsync `prices.db` while a fetch is writing** — torn copy. The script aborts if
  `fetch_prices.py` / `fetch_gas_prices.py` / `backup_db.sh` is running on the VPS.
  Do not use `sqlite3 .backup` on the VPS: the disk is ~100% full (the DB is 15 GB of 38 GB).
- Don't touch `prices.db` locally (integrity checks, queries) while rsync runs; `--inplace` rewrites it.
- `PRAGMA integrity_check` on 15 GB takes tens of minutes — run it manually when needed, not in the sync.
- `backups/` (gzipped SQL dumps on the VPS) is intentionally not mirrored; the DB itself is the backup.

## Notifications (ntfy)

Pick a long random topic (topics on ntfy.sh are public-by-name), subscribe in the ntfy app,
and export `NTFY_TOPIC` (e.g. in the launchd plist / crontab line). ntfy only reports runs that
happen; to catch a *missed* run (Mac asleep, volume unmounted before launch), also wrap the job
in `scripts/hc_run.sh <healthchecks-uuid>` — same pattern as the VPS jobs.

## Scheduling (optional)

Local cron/launchd, e.g. daily 08:30 after the VPS retail pass. The volume must be mounted;
the script fails loudly via ntfy if it isn't.
