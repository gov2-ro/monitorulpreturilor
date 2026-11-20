# Brief — prices.db fills the disk; backup skips runs; stale pipeline-check; wrong branch

Status: ready · Owner: Sonnet · Written by: celibidache, 2026-11-20

The hub (`~/devbox/celibidache`) observes this project from the outside. It does not change it. The numbers below are from 2026-11-19 and 2026-11-20. Check them again before you act.

## Problems

1. **`data/prices.db` is 9.8 GB on a 38 GB disk with 6.4 GB free.** It has no free pages (a `VACUUM` gains nothing). `/` loses more than 1 GB per week; this DB is the largest part.
2. **The weekly backup skips runs.** `scripts/backup_db.sh` checks free space on `/`, but it writes to `/mnt/HC_Volume_105444571`. It skipped 4 of 7 weekly runs. The newest backup is `prices-2026-11-08.db.sql.gz` (12 days old; the hub limit is 8).
3. **`data/logs/pipeline-check.log` was last written on 2026-11-06.** `scripts/alert_pipeline_check.py` runs daily and reads this stale file.
4. **The working copy is on branch `phase0-restore-publishing`**, not `main`, with 3 dirty files.
5. **Logs:** `fetch-prices.log.1` is 52 MB and not compressed; `fetch-gas-prices.log` is 37 MB and is not in the rotation.

## Do

1. Fix the free-space test in `backup_db.sh`: test the filesystem of the target folder. Run one backup by hand and confirm the file.
2. Propose a plan for the size of `prices.db`. Options: (a) the owner grows the Hetzner volume and the DB moves there; (b) a retention rule that moves old price rows to compressed archives. State the cost of each. Do not move or delete data without the owner's yes.
3. Make the `/pipeline-check` verdict fresh each day, or stop the alert that reads it.
4. Ask the owner which branch the server must run.
5. Add `fetch-gas-prices.log` to `scripts/logrotate.conf` and compress `.1`.

## Done when

- `celi status` shows `backup:prices.db` green and `pipeline-check` not unknown.
- The owner has a written size plan.

## Timezone change

On 2026-11-20 at 01:20 the system timezone changed from UTC to Europe/Bucharest. Every line of this project has no guard, so each now fires 2 hours earlier in UTC terms (example: `fetch_gas_prices` started at 01:40 UTC on 2026-11-20, 03:40 UTC before). Check that this order still holds: gas fetch 03:40, audit 06:00, alert 06:30, check 07:05, pipeline-check alert 07:15.
