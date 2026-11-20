# HDD pull manifest — monitorulpreturilor (VPS → 2 external HDDs)

Written 2026-11-20 15:25 EET by the VPS session (mp-vps-2011). Host: `pax@78.46.142.169`.
Nothing on the VPS is deleted until the owner says yes, after both HDD copies match these checksums.

## Files to pull (14 files, 3.57 GB)

| # | Absolute VPS path | Bytes | sha256 |
|---|---|---:|---|
| 1 | `/mnt/HC_Volume_105444571/pax/g2-dev/monitorulpreturilor/data/backups/prices-2026-11-20.db.sql.gz` | 747628927 | `1eaed4f4305ea75c115958dd32b39c45b8c324972c92187205fdc95e16304234` |
| 2 | `/mnt/HC_Volume_105444571/pax/g2-dev/monitorulpreturilor/data/backups/prices-2026-11-08.db.sql.gz` | 654787613 | `70574e54ecf50c77fc7f0d2e3ccd9d0ed8c212e2ff76ce3159ac13e8e205de79` |
| 3 | `/mnt/HC_Volume_105444571/pax/g2-dev/monitorulpreturilor/data/backups/prices-2026-11-01.db.sql.gz` | 594256077 | `0133bfba750b30b3f879df87eb1d9f3ad0783ae83b3d39ab06c1c893f9902f53` |
| 4 | `/mnt/HC_Volume_105444571/pax/audit-export-2026-10-12/prices-full-2026-10-04.db.sql.gz` | 981091199 | `1e2fed3862533cc6f05d772f41aa5c7f6ba748c0ba4904e73ab88a302c495e4f` |
| 5 | `/mnt/HC_Volume_105444571/pax/audit-export-2026-10-12/prices-slice-2026-09-28_to_2026-10-12.sql.gz` | 186836042 | `c4039594ad4522f29b9c363fb327a727970b4cede018aa65a44968a42faa1ec2` |
| 6 | `/mnt/HC_Volume_105444571/pax/audit-export-2026-10-12/audit-core-2026-10-12.tar.gz` | 62171574 | `c8386e122a386ce243f0d5f81bdab71752d92246d28157e5d3c2e86159c53502` |
| 7 | `/mnt/HC_Volume_105444571/pax/g2-dev/monitorulpreturilor/data/prices_ci.db` | 96849920 | `8d29038b07deba86d44ba048d35bbc1d4fd35242ad655346b3e058b88f5d318c` |

### Retention archives (added 2026-11-20 15:55 EET; 7 files + 2 small, 247,624,198 B)

Written by `retention.py --export-only` (nothing deleted). They hold the 12,561,155 `prices` rows with
price_date < 2026-10-06 that the first `--apply` will delete from the VPS, so they must be on both HDDs
before that happens. Folder: `/mnt/HC_Volume_105444571/pax/g2-dev/monitorulpreturilor/data/archive/`.
The folder's `MANIFEST.sha256` lists the same hashes, so on the Mac run `shasum -a 256 -c MANIFEST.sha256` inside it.

| # | File (in the folder above) | Bytes | Rows | sha256 |
|---|---|---:|---:|---|
| 8 | `prices-2026-04.20261120T1542.sql.gz` | 39235586 | 1,857,545 | `ef06529754f32d3000c4bd9387291b46fa3ca577d2448d0087786732ae51f32a` |
| 9 | `prices-2026-05.20261120T1542.sql.gz` | 19655466 | 955,284 | `0ed45a4aa19d318a4b0b6ed0d9a8bd955f776da909e9336d89c77c6857981835` |
| 10 | `prices-2026-06.20261120T1542.sql.gz` | 10962125 | 552,858 | `ecd160b9cbf3cd6cbd57f4ab586ddba1644016171f921a1bce1b11d85c518cf6` |
| 11 | `prices-2026-07.20261120T1542.sql.gz` | 1541639 | 80,524 | `73555c5b356bf8c652bc2def2929e957528de94ced718f4e5c40e09d1ee0095c` |
| 12 | `prices-2026-08.20261120T1542.sql.gz` | 2049306 | 105,294 | `5abc5f820836feeb9f4f8ec7e1ca627d2dff73e8de854197dfbbc13fb2fba77e` |
| 13 | `prices-2026-09.20261120T1542.sql.gz` | 136158598 | 6,965,892 | `ba3019d22f7152c09ac4861b937bcc5d150b9d29a4079b158fd2fdf1f33d81ce` |
| 14 | `prices-2026-10.20261120T1542.sql.gz` | 38021478 | 2,043,758 | `a8c734605f8ac592c56297a57e3f501b0de5817160f03beec039dffd7968c218` |
| — | `MANIFEST.sha256`, `retention_state.json` | 714 + 88 | | (small metadata; copy too) |

Each archive is self-contained: `CREATE TABLE IF NOT EXISTS prices …; BEGIN; INSERT OR IGNORE …; COMMIT;`.
Restore test on the VPS: #11 → 80,524 rows, 2026-07-01 → 2026-07-31. Later runs add new files
(never rewrite these), so pull the folder again after each `--apply` week.

**Grand total for the pull: 3,571,245,550 bytes (3.57 GB), files #1–#14.**

Notes:
- **#1 is the most important file** among the dumps. The Mac full-history `prices.db` stops at 2026-10-28 04:00. Price rows written after that exist only on the VPS and in dumps #1 and #2.
- #1 is a consistent `.dump` snapshot taken 14:33–15:21 EET on 2026-11-20 (one read transaction). `gzip -t` passed on the VPS, and the dump ends with `COMMIT;`.
- #7 is tracked in git (CI fixture), so it can be rebuilt. It is optional, small, and included for completeness.
- Nothing else in `data/` needs the HDDs. The code, `config/` and `data/reference/` are in git. Checkpoints and logs are transient. `sentinel_stores.json` is rebuilt by `analyze_price_similarity.py`.
- **`data/prices.db` itself (10.6 GB, live, WAL) is not in this list on purpose.** Do not rsync it while fetch slices run (see `docs/local-backup.md`). Dump #1 is its stand-in.

## Pull (Mac)

```bash
# one target per HDD; --partial lets an interrupted transfer resume
V=pax@78.46.142.169:/mnt/HC_Volume_105444571/pax
D=/Volumes/<HDD>/monitorulpreturilor-vps-2026-11-20
mkdir -p "$D"
rsync -a --partial --progress "$V/g2-dev/monitorulpreturilor/data/backups/" "$D/backups/"       # #1-#3
rsync -a --partial --progress "$V/audit-export-2026-10-12" "$D/"                               # #4-#6
rsync -a --partial --progress "$V/g2-dev/monitorulpreturilor/data/prices_ci.db" "$D/"          # #7 (optional)
rsync -a --partial --progress "$V/g2-dev/monitorulpreturilor/data/archive/" "$D/archive/"      # #8-#14 + manifest
(cd "$D/archive" && shasum -a 256 -c MANIFEST.sha256)
```

## Verify (each HDD)

1. Checksums (required). They must match the table:
   `shasum -a 256 /Volumes/<HDD>/monitorulpreturilor-vps-2026-11-20/**/*.gz`
2. Archive integrity (required, about 1 min per file): `gunzip -t <file>.gz && echo OK`.
   Also check that each `.db.sql.gz` ends with `COMMIT;`: `gzip -cd <file> | tail -c 200`.
3. Restore test (once, for #1 only, on the internal SSD; needs about 11 GB free and 30–60 min):
   ```bash
   gzip -cd prices-2026-11-20.db.sql.gz | sqlite3 /tmp/restore-test.db
   sqlite3 /tmp/restore-test.db "PRAGMA quick_check; SELECT count(*), min(price_date), max(price_date) FROM prices; SELECT count(*) FROM prices_current;"
   ```
   Expected: `ok`, about 29.7M rows, `2026-04-17 04:00` … `2026-11-20 04:00`, and about 18.1M `prices_current` rows. These counts were taken on the live DB about 10 min after the dump started, so expect small differences. Delete `/tmp/restore-test.db` afterwards.
