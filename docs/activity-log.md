# Activity Log

---

## General

### 2026-09-28 — Fixed weekly-tier store starvation (store_freshness ~72% RED) and a product-tier rule that dropped 99% of the catalogue

**Why:** `/pipeline-check` showed store_freshness RED at 72% "starved": ~2,940 weekly-tier stores (all of PROFI/MEGA/CARREFOUR/AUCHAN/SUPECO/SELGROS weekly) had a fresh `last_checked_at` but zero price rows in 7d. Root cause in `fetch_prices.py`, four compounding bugs:

1. **Full scan fired daily, then flipped off.** `full_scan = cp.iso_week != today`, but the daily reset sets `cp = None`, so every night's first slice was a "full scan" (all products × all stores ≈ 438k batches — days of work; it got through ~3 anchors before `--max-runtime 1700`). Slice 2 resumed with `iso_week == today` → not a full scan → tiering back on, and the batch list changed under the done-keys.
2. **Weekly-tier stores were only fetched in that full scan** → never. And since the tier is "no change in 7d", not fetching them kept them weekly forever.
3. **Tier-skip bumped `last_checked_at`** without an API call, so never-fetched stores looked polled (and `--order stale` never prioritised them).
4. **Product tier used "any row older than 30d"** (`DISTINCT … WHERE`) instead of `MAX(last_changed_at)`: stale rows from the never-fetched stores tiered out 74,903/75,777 products. The daily run was fetching ~870 real products plus ~11.2k ghosts (ghost filter was also off every day, per bug 1).

**Changes (`fetch_prices.py`):**
- `_decide_full_scan()` / `_iso_week_key()`: full scan decided once per session and persisted in the checkpoint (`full_scan`, `full_scan_week`); `full_scan_week` is carried across the daily reset and `--fresh`, so it fires once per ISO week. Full scan now only disables ghost filter + sentinel + canary — **not** product/store tiering (infeasible volume; that is why it never completed).
- `_weekly_rotation_skip()`: weekly-tier stores rotate, `store_id % 7 == day_ordinal % 7` is due today (fetched with the daily product set). The `weekly_store_ids` checkpoint key now holds the *not-due* set.
- Tier-skip no longer calls `propagate_last_checked`.
- `_build_weekly_product_tier`: `GROUP BY product_id HAVING MAX(last_changed_at) < now-30d` (57,296 products instead of 74,903).
- Also fixed: `anchor_failures` skiplist was lost on every daily reset (only carried on `--fresh`, contrary to its comment).
- Tests: `tests/test_fetch_tiering.py` (7 tests). Smoke-tested against a 40-store scratch DB built from prod PROFI stores with real API calls: first-of-week run → full scan + rotation (5/40 due, 23 anchors tier-skipped); next-day run → no full scan, ghost filter on; resumed slice → mode preserved.

**Expected effect / capacity:** the first run after deploy (no `full_scan_week` in the prod checkpoint yet) will be a full scan. The daily product set grows from ~12k (mostly ghosts) to ~18.5k real products, and weekly stores that start changing drift back to the daily tier, so the nightly run will get longer (est. 1.5–2.5×). Watch that it still completes before the 06:02 audit. store_freshness should fall over ~7–10 days as the rotation cycles.

### 2026-09-27 — Root-caused the LIDL/SUPECO frozen `price_date` bug; hardened sentinel propagation

**Why:** `/pipeline-check` flagged `frozen_price_dates` and `coverage_gaps` RED for LIDL/SUPECO (backlog item filed 2026-09-03, root cause then unknown). Traced it: LIDL's 3 sentinel stores (11708, 3218, 7974) have had a fresh `last_checked_at` every day but a `price_date` frozen at 2026-07-16 for 73+ days; `propagate_network_prices()` copies that frozen date to all ~400 non-sentinel LIDL stores verbatim, which is invisible to every check that only looks at network-level `MAX()`. SUPECO's 3 sentinels froze the same week (2026-07-12/13), independently.

- **Regenerated `data/sentinel_stores.json`** via `analyze_price_similarity.py --days 30 --export-sentinels` — last run was 2026-06-23, three months stale, despite CLAUDE.md documenting a weekly cadence nobody had automated. New sentinel IDs are fully different from the old set. Verified after: KAUFLAND and PENNY's new sentinels are fresh (≤3d); SUPECO's are ~20d old (matches the coverage_gaps age, likely a genuine slow-moving catalog rather than a bug); **2 of LIDL's 3 new sentinels (11895, 11906) are still frozen at the exact same 2026-07-16 date** — the candidate pool (`fetch_sentinel_stores`, ranked by `products_covered` in the `prices` table) is itself contaminated, because propagation had been writing rows into `prices` for non-sentinel stores too, so "high coverage" no longer implies "actually fetched recently." This means the freeze is broader than 3 unlucky store IDs — worth a follow-up look at whether it's an upstream LIDL API issue or a parsing gap on our side.
- **Hardened `fetch_prices.py`** (`SENTINEL_FROZEN_DATE_DAYS = 14`, propagation call site ~line 1022): before propagating a sentinel's prices, check the sentinel's own `MAX(price_date)` age; skip propagation and log `Sentinel FROZEN: ...` instead of fanning out a stale date if it's beyond the threshold. This fails safe regardless of whether sentinel regeneration picks a good store — a still-frozen sentinel now produces `starved` (honest, no new data) instead of `frozen_dates` (fresh-looking but wrong) for its network's stores.
- **Added a weekly sentinel-refresh cron line to `scripts/crontab.template`** (Sunday 04:00, `analyze_price_similarity.py --export-sentinels`) so this doesn't silently go stale again. Not yet installed into the live crontab — needs a healthchecks.io UUID and an operator decision, left as `REPLACE_WITH_SENTINEL_REFRESH_HC_UUID`.
- Left `docs/backlog.md`'s LIDL/SUPECO item open (not resolved) — the guard stops the silent corruption, but the underlying upstream freeze for LIDL is still there and will now surface as `store_freshness` starvation instead.

### 2026-09-04 — Site published to gh-pages; `idx_prices_date` added; build profiled

**Why:** finishing Phase 0 (the site had to actually reach the internet) and answering the open question of why a full build took over an hour.

- **Published.** `scripts/publish_site.sh` now force-pushes 146 files to `origin/gh-pages`. Two problems surfaced on the first real run:
  - *Push denied.* `~/.ssh/config` names `IdentityFile ~/.ssh/id_monitpret` for the `github.com-monitpret` alias, but without `IdentitiesOnly` ssh-agent offered `id_ed25519_fb-fomo-gobbler` first; GitHub authenticated that key (a deploy key for a different repo) and refused. The deploy key itself is fine — a forced-identity dry-run pushed cleanly. Fixed repo-locally with `git config core.sshCommand 'ssh -i ~/.ssh/id_monitpret -o IdentitiesOnly=yes'`, which fixes every git operation from this box, not just the publish.
  - *Silent skip.* The script's "no changes to publish" short-circuit compared the staged tree against the **local** `gh-pages` branch, so after the failed push it saw a matching local commit and exited 0 without ever pushing. Now it also compares against `git ls-remote origin gh-pages` and republishes when the remote is missing or behind.
- **`CREATE INDEX idx_prices_date ON prices(price_date)`** — built in 1m35s, exit 0, ~770 MB. `MAX(price_date)` went from a ~2m24s full scan to **6 ms**, and both hot query shapes now report `SEARCH prices USING COVERING INDEX`. Full build: **1:10:48 → 53:30**.
- **`generate_site.py --debug`** — new per-stage timer (`_timed`). The build is I/O-bound and takes tens of minutes, so per-stage timing was the only practical way to find the hot spot rather than guessing. First profile:

  | Stage | Time |
  |---|---:|
  | `load_analytics_data` | 1146.6 s |
  | `load_price_index` | 916.9 s |
  | `load_price_index_by_category` | 671.7 s |
  | `load_network_trends` | 244.8 s |
  | `load_category_trends` | 151.2 s |
  | `build_compare_data_files` | 134.8 s |
  | `load_popular_products` | 88.9 s |
  | `load_metodologie_stats` | 62.0 s |
  | everything else (13 stages) | < 12 s combined |
  | **page rendering + write** | **0.1 s** |

  The cost is entirely data loading; rendering is free.

- **The important finding is a correctness bug, not a performance one.** `load_price_index` and `load_price_index_by_category` average `prices` with **no `price_date` filter** — so the network price index leading the homepage is a mean over all 183 collected days presented as a current comparison, and because networks entered the panel at different dates (the 2026-06-21 attribution fix moved 996 stores) the per-network means aren't even over comparable windows. That is the number the headline quotes, so correcting it moves a public figure and needs a `metodologie.html` note; filed to `docs/backlog.md` rather than changed here. The planned `mart_spread` table is the intended replacement.
- Refreshed `official_cpi` from Eurostat — now current through **2026-07** (August isn't published yet). `config/ins_ipc.json` is human-maintained and still at 2026-05.

**Still manual:** GitHub → Settings → Pages → source `gh-pages`, folder `/ (root)`, then re-add the custom domain.

### 2026-09-03 — Phase 0/0.5: restored the dead site's publishing path, and fixed the freshness check that was reporting GREEN over a starving pipeline

**Why:** five months of collection had produced 36.5M price rows that nobody could see. `monitorulpreturilor.gov2.ro` served GitHub Pages' "Site not found" (HTTP 404, generic `*.github.io` cert), `git ls-files site` returned 0 files, every page on disk was stamped 2026-07-07, and the daily audit reported `0/4066 stores stale — GREEN` throughout. Both problems traced to the same class of error: a check that measures the wrong thing succeeds silently.

**Phase 0 — publishing (the site is buildable and deployable again):**

- Recovered `app.css` (731 lines), `charts.js` and `logo.svg` from `git show 5f5e478:docs/assets/…`; they were deleted by `59f306a` ("delete site from docs") and never re-created under `site/`, so `page_shell`'s `<link href="assets/app.css">` had been dangling for eight weeks. Placed them at repo root `assets/` as tracked **source** rather than build output, so they cannot be lost with the output directory again.
- `generate_site.py`: added `copy_assets()`, which copies `assets/` into `<out>/assets` and re-emits `CNAME` on every build. It **raises** on a missing required asset rather than warning — a silent miss is precisely how this broke the first time, and an unstyled site is worse than a failed build.
- `generate_site.py`: added a `use_charts` flag to `page_shell()` and removed the six per-page Chart.js CDN tags. The shell now emits Chart.js followed by `assets/charts.js` in the head. Order is load-bearing: `charts.js` mutates `Chart.defaults`, and those only apply at construction time, so both must run before any page script calls `new Chart()`.
- `scripts/publish_site.sh` (new): builds the aggregator chain in the order `readme.md` documents, then force-pushes `site/` to an orphan `gh-pages` branch through a git worktree at `.worktrees/gh-pages`. GitHub Pages can only serve a branch root or that branch's `/docs`, never an arbitrary `/site`, so committing the output to `main` cannot work; this keeps `.gitignore`'s `/site` intact and `main` free of build output. Single-commit force-push rather than accumulating history — ~3 MB of derived output daily would add ~1 GB/year of git objects for no audit value, since `main` is the audit trail. Refuses to publish if any of `index.html`, the three assets or `CNAME` is missing, if fewer than 20 files are present, or if the staged file count doesn't match what was copied (a gitignore rule silently eating output would otherwise ship a broken site over a working one).
- `scripts/crontab.template`: daily rebuild + publish at 06:30, wrapped in `hc_run.sh` like every other job — placed after the 06:00 audit so `pipeline-health.html` carries fresh numbers, and away from the 30-minute `fetch_prices` ticks.
- **Verified:** clean build emits 19 pages (3,027 KB) plus assets and CNAME; Playwright over a local server confirms `body` background `rgb(250,248,242)`, IBM Plex loaded, `window.MP_PALETTE` present, `Chart.defaults.font.family` already themed at chart-construction time, and zero console errors on `index`, `inflatie` and `tablou`.
- **Still manual (no `gh` CLI on this box):** set the repo's Pages source to branch `gh-pages`, folder `/ (root)`, and re-add the custom domain.

**Phase 0.5 — the false GREEN:**

- Root cause: both freshness checks measured `MAX(prices_current.last_checked_at)`, which `db.py:420` bumps on **every visit even when the price is unchanged**. It answers "are we polling?", never "are we receiving?" — so a store polled perfectly that has returned nothing for ten weeks reported zero days stale. Measured across the live DB, `last_checked_at` finds **0 stale stores in all 11 networks**.
- Rewrote `load_store_freshness()` to measure from `last_changed_at`, with budgets taken from the store's `fetch_tier` (daily 3d / weekly 10d), and to classify each store by *failure mode* rather than a single boolean: `unpolled` (not visited), `starved` (visited on schedule, no new data), `frozen_dates` (data flowing, but the retailer's `price_date` is stuck >14d), `ok`. `last_checked_at` is still read — but only to tell `unpolled` apart from `starved`, which is the one question it can actually answer.
- `audit_pipeline.py`: `check_store_freshness` now reports the per-status and per-network breakdown; new `check_frozen_price_dates` check; `check_coverage_gaps` switched off `last_checked_at` too. Both freshness checks share one `load_store_freshness()` call — the per-store `GROUP BY` over 16.4M rows takes ~6 min and must not run twice.
- `generate_pipeline_report.py`: the health page gained a "Freshness by network — where the data loss is" table, and the per-store table now shows tier, days since new data, days unpolled and price_date age side by side, so the two failure modes are distinguishable at a glance.
- Coverage: `tests/test_store_freshness.py`, 9 tests, each built from a state actually observed in the live DB on 2026-09-03 — including the LIDL case (fresh writes, 49-day-old `price_date`, invisible to a `last_changed_at`-only check) and the PROFI case (polled today, 70 days without new data).
- Also repaired `tests/test_pipeline_report.py`, whose fixture used positional `INSERT`s into `prices_current` and `runs`. Both tables had since gained a column, so **all 7 tests in that file had been erroring at setup** — the freshness tests it contained were not testing anything. Columns are now named explicitly.

**What the fix surfaced (filed to `docs/backlog.md`, not fixed here):** 2,211 weekly-tier stores — 54% of the estate — have produced no new price data for a median of 70 days (PROFI 1,585 stores at p50 70d, AUCHAN 63d, MEGA IMAGE 27d, CARREFOUR 18d), all while reporting fresh `last_checked_at`. There is a feedback loop underneath: `update_store_tiers` demotes a store to weekly based on `last_changed_at`, and `fetch_prices.py` then skips weekly stores except on the ISO-week full scan — so a store that stops returning data gets fetched *less*. The networks that still look healthy (PENNY, KAUFLAND, LIDL, SUPECO) are exactly the four sentinel-propagation networks, where prices are SQL-copied rather than fetched; the suspected root cause is the retail API's 50-stores-per-request cap within a 5 km buffer starving the densest networks. Separately, 401 LIDL stores are receiving fresh writes carrying a `price_date` frozen at 2026-07-16.

**Consequence to expect:** the daily audit will now go RED on `store_freshness` and `frozen_price_dates`, because it is finally measuring something real. That is the correct reading, not a regression — the thermometer was broken, not the patient.

### 2026-08-10 — `/pipeline-check`: root-caused `run_history` RED, designed auto-clear for gas's non-session run pattern

**Why:** today's audit was RED on `run_history` alone (all other checks clean). Rather than just acknowledging the flagged run, traced it to a real gap in the existing auto-suppress logic.

- Root cause: `fetch_gas_prices` run `#1162` started 2026-08-07 03:40 and hung ~24h without reaching a terminal status; the next day's cron reaped it via `abandon_stale_runs()` and, in the same invocation, started + completed `#1166` a fraction of a second later. `check_run_history` (`audit_pipeline.py:59`) already auto-suppresses exactly this shape for `fetch_prices`, which deliberately reuses `started_at` as a session id across resumed cron ticks — but `fetch_gas_prices` gets a fresh `started_at` every invocation, so the existing exact-match `NOT EXISTS` clause never catches gas's version of "reaped and immediately replaced," leaving it flagged for the full 7-day `ABANDONED_DAYS` window.
- Brainstormed a fix (via `superpowers:brainstorming`, user chose each option): extend the same `NOT EXISTS` exclusion with an OR'd time-window clause — a `completed` run of the same script within a flat 60-minute window after the bad run's `finished_at` also counts as recovered. Kept query-only, no `acknowledged_at` write, so `ack_run.py`'s human-review audit trail stays unambiguous. Also proposed adding a `suppressed` list to the check's JSON output so it's visible *what* got auto-excluded and *why*.
- **Implementation complete (2026-08-10):** Added constant `RUN_RECOVERY_WINDOW_MINUTES = 60` to thresholds block. Rewrote `check_run_history()` to evaluate both recovery rules (exact-match session-id + time-window), returning a new `suppressed` list showing auto-excluded runs and their `recovered_by` id. Summary field now surfaces suppressed count for observability (`"N unrecovered...(M auto-suppressed)"`). Query-only — no `acknowledged_at` writes. Coverage: 10 tests including boundary conditions, cross-script isolation guard, and NULL `finished_at` robustness; verified against real `data/prices.db` (run #1162 correctly suppressed with `recovered_by: 1166`). Spec written to `docs/superpowers/specs/2026-08-10-run-history-auto-clear-design.md`.
- Filed the underlying diagnostic gap — `abandon_stale_runs()` never records *why* a run hung — to `docs/backlog.md` (Gas / Bugs).

### 2026-07-26 — `alert_red.py`: annotate paging with RED-streak length

**Why:** `/pipeline-check` history showed overall verdict RED in 7/10 of the last 10 runs, but `scripts/alert_red.py` pages the same way whether it's a fresh one-day blip or a persistent multi-day failure — on-call has no way to tell from the alert alone whether this is new or already-known-and-ignored.

- **`scripts/alert_red.py`**: added `red_streak(today)`, which walks backwards from today through `data/logs/audit-{date}.json`, counting consecutive days with `overall == "RED"` until it hits a missing file or a non-RED day. The page message now reads `RED pipeline audit {date} — N check(s) failing (RED for 1st day)` or `(RED for K consecutive days)`. Exit-code semantics (0/1, i.e. whether it pages at all) are unchanged — this is purely additive context on an alert that already fires, chosen over suppressing single-day RED or escalating severity at a threshold.
- Verified via a scratch-dir simulation (synthetic `audit-*.json` files, not the real `data/logs/`): a 3-day RED streak reports "RED for 3 consecutive days" exit 1; an isolated RED day with a GREEN day before it reports "RED for 1st day" exit 1; today's real GREEN audit still exits 0 unchanged.

### 2026-07-07 — Root-cause + drain legacy `DD.MM.YYYY` price_date rows (collision-safe migration)

**Why:** the backlog claimed `api.py:_parse_date` was still emitting legacy `DD.MM.YYYY` on every fetch, re-adding ~7M old-format rows. That was a **misdiagnosis**. `_parse_date` has emitted ISO since 2026-05-23 (`b8e900f`) and every direct API fetch is ISO. The real chain:

- `price_date` holds the retailer's *Pricedate* (the date a price was set), not the fetch date — so a product whose shelf price hasn't changed keeps its old-format value in `prices_current` until it's re-fetched and re-parsed.
- **Sentinel propagation** (`propagate_network_prices`) SQL-copies `prices_current.price_date` verbatim from a sentinel to all its non-sentinels. With fresh `fetched_at` but a legacy `price_date` string, this manufactured "recent" legacy rows — all in the four Tier-A sentinel networks (KAUFLAND, LIDL, PENNY, SUPECO), which matched the data exactly.
- The one-time `migrate_price_dates.py` **had never drained them**: ~1.24M legacy rows in `prices` have an ISO twin (same product+store+date fetched both before and after the 05-23 fix), so a plain `UPDATE` to ISO hit `UNIQUE(product_id,store_id,price_date)` and aborted the batch.

**Fix — `migrate_price_dates.py`:** switched both `_migrate_batched` and `_migrate_single` from `UPDATE` to `UPDATE OR REPLACE`. On a collision the redundant ISO twin (an identical observation under the UNIQUE key) is dropped and the legacy row is migrated in its place. `prices_current`/`gas_stations` key on product+store only, so they never collide (no-op there). Verified the collision behavior on an in-memory reproduction before running. No triggers/FKs on `prices`, so the REPLACE-deletes don't cascade.

**Run (local, concurrent with the live fetch cron):** 1279s. Migrated `prices` 8,189,040 · `prices_current` 13,186,339 · `gas_prices` 66,899 (these were `DD/MM/YYYY` slash format, missed by a dot-only `LIKE`) · `gas_stations.update_date` 41. Lock-retry survived one 7-step backoff mid-run with no data loss. Once `prices_current` is all-ISO, propagation only ever copies ISO, so the "recurring" legacy rows stop at the source.

**Follow-up (backlog):** re-check for a small residue (concurrent propagation during the run can re-seed a few) and run the same migration on the **VPS** (separate DB).

### 2026-07-07 — Checkpoint durability + write-lock retries; refresh_stores catch-up export

**Why:** checkpoint files (`data/retail_checkpoint.json`, `data/gas_checkpoint.json`) were written with a plain `open(path, "w") + json.dump`, so a hard kill mid-write (OOM, reboot, `kill -9`) truncates the file and the next run can't load it. `migrate_price_dates.py` also had no retry on `database is locked`, unlike `fetch_gas_prices.py`/`db.py`, even though it runs concurrently with the daily fetch.

- **`fetch_prices.py` / `fetch_gas_prices.py`**: added `_atomic_write_json()` — write to `.tmp`, `fsync`, `os.replace()` (atomic on POSIX), keeping the prior good file as `.bak`. `_load_checkpoint()` now falls back to `.bak` if the primary is corrupt/unreadable, logging the recovery instead of crashing at startup.
- **`fetch_prices.py`**: also catch `xml.etree.ElementTree.ParseError` and `ValueError` (not just `requests.exceptions.RequestException`) around `fetch_xml()` so a malformed API response skips the batch with a warning instead of killing the run.
- **`migrate_price_dates.py`**: added `_execute_write()` with the same exponential-backoff retry on `database is locked` used elsewhere (`_DB_LOCK_RETRIES=8`, 1s→128s), plus `PRAGMA busy_timeout=60000` — the daily fetch runs ~28/30 min so there's effectively no idle window to run migrations in otherwise. Batches are already idempotent (`WHERE` filters already-ISO rows), so retrying is safe.
- **`refresh_stores.py`**: after each run, exports store IDs with no `prices_current` row or `last_checked_at` older than 2 days to `data/catch_up_store_ids.txt`, printing the follow-up `fetch_prices.py --store-ids-file ... --fresh` command. (This closes the gap noted in the 2026-06-24 entry below — that work had been drafted but never actually committed.)
- **`.claude/commands/pipeline-check.md`**: dropped `database is locked` and `abandoned` from the tracked error patterns/upgrade-suggestion rules (both resolved by `busy_timeout` + this retry work); added `error:timeout` and `error:oom` suggestions in their place.
- Verified: all four scripts `ast.parse` clean and `--help` runs under `venv`. Not exercised against an actual corrupt/truncated checkpoint file — logic reviewed but no live crash-recovery test performed.

### 2026-07-07 — Inflație page: guard the nowcast against noisy partial months + ship

**Why:** the current-month nowcast was reading +4.7% MoM off only 5 early-July data points. On a page whose whole point is *credibility*, a spurious partial-month spike undercuts the message more than a missing number would.

- **`generate_site.py`** (`renderVsOfficial`): added `NOWCAST_MIN_POINTS = 10`. When the provisional (in-progress) month has fewer days than that, the KPI tile shows `—` ("se acumulează date, N zile") and the callout switches to an "Estimare în curs" message instead of a precise-looking percentage. Once ≥10 days accumulate, the real nowcast renders. Completed months are unaffected (they always have ~30 points).
- Regenerated the full static site (`site/*.html`) from the rebuilt `cpi.json` (169 dates; `official` HICP through 2026-05, `ins_headline`, `our_monthly` 4 months, `nowcast`). `site/` is gitignored build output — this run was to eyeball `inflatie.html`; the commit carries only the source changes.
- Follow-up noted in backlog: `prices` held 25.9M ISO + ~8.2M legacy `DD.MM` dates whose lexical `ORDER BY` interleaves the two formats. (Root-caused and drained later the same day — see the "Root-cause + drain legacy `DD.MM.YYYY` price_date rows" entry above; the cause was **not** `_parse_date`, which has emitted ISO since 05-23.)

### 2026-07-06 — Civic inflation vs official (Eurostat HICP + INS) on the Inflație page

**Why:** the `inflatie.html` civic index tracked our basket cost but had nothing to anchor against, so it read as an isolated prototype. Anchoring it to the official figure — and, crucially, *nowcasting* the current month before the official number is published (~2 weeks after month-end) — turns it into a credibility feature.

- **`fetch_official_cpi.py`** (new): pulls Romania's HICP from Eurostat `prc_hicp_minr` (ECOICOP v2) — `TOTAL`→`CP00` (all-items) and `CP01` (food) — storing `I25` index + Eurostat-computed `RCH_M`/`RCH_A` into a new `official_cpi` table. Generic JSON-stat linear-index parser (no hard-coded dimension positions). Monthly cron; no auth. The dimension is `coicop18` (not `coicop`) and all-items is `TOTAL` (not `CP00`) — codes verified against the live API.
- **`db.py`**: added `official_cpi` table via a standalone `ensure_official_cpi_table()` (kept out of the heavy `init_db()` migration path so the fetcher can guarantee the table without triggering unrelated ALTER/UPDATE writes) + `upsert_official_cpi()`.
- **`config/ins_ipc.json`** (new): human-maintained national INS IPC headline (the recognizable number). Seeded with May 2026 real figures — 10.9% annual national IPC vs 9.7% harmonized (HICP), which cross-confirmed the Eurostat fetch.
- **`build_cpi.py`**: emits monthly median cost + MoM % for the `camara` (food) basket, reads `official_cpi` (resilient to a missing table) + `ins_ipc.json`, and merges `official` / `ins_headline` / `our_monthly` / `nowcast` into `cpi.json`. Added `month_key()` handling both ISO and legacy `DD.MM.YYYY` dates (robust to the price_date migration).
- **`generate_site.py`** (`gen_inflatie`): new "Inflația noastră vs. cea oficială" card — MoM overlay (our food basket vs HICP food, dashed), KPI tiles, a nowcast callout, source citations; expanded methodology (HICP vs our basket, rate-of-change only, nowcast caveat). All render paths degrade gracefully if official keys are absent. Also fixed the daily-chart date labels for ISO dates and removed a pre-existing literal `{n}` placeholder.
- **Verified:** fetcher against a temp DB and live `prices.db` (34 rows, values match Eurostat); `build_cpi` helpers unit-tested (month grouping, MoM, nowcast, missing-table resilience); generated inline JS passes `node --check` with all DOM hooks present. Comparison is deliberately **rate-of-change only** — the HICP basket and weights differ from ours.

### 2026-07-06 — fix: propagate_last_checked missing from API fetch path

**Problem identified via pipeline-check:** `store_freshness` has been RED for 6+ consecutive days, worsening from 14% → 21% stale. Drill-down showed PROFI with 855 stale stores, oldest 72 days — matching when the weekly store tier was introduced.

**Root cause:** `propagate_last_checked` was called in the tier-skip, sentinel-skip, and canary-skip paths but NOT after an actual API fetch. PROFI weekly-tier stores that end up in mixed clusters (alongside daily-tier stores from other networks) bypass all skip paths and go through the API fetch. Their products are excluded by the product-level weekly tier, so the API returns 0 PROFI prices. With no upserts fired, their `last_checked_at` never updates and they accumulate as stale indefinitely.

**Fix (`fetch_prices.py` ~line 1019):** Added `propagate_last_checked(conn, anchor_store_ids, fetched_at)` immediately after `total_prices += store_prices` — mirrors what the tier-skip and sentinel-skip paths already do. Now ALL stores in a cluster get their `last_checked_at` freshened after each anchor is processed, whether or not prices were returned.

### 2026-06-24 — refresh_stores.py: post-activation price catch-up export

Added stale-store export to `refresh_stores.py`. After each run, queries `prices_current` for all seen stores with no prices or `last_checked_at` older than 2 days, writes their IDs to `data/catch_up_store_ids.txt`, and prints the follow-up command (`fetch_prices.py --store-ids-file ...  --fresh`). This fixes the recurring `store_freshness` RED that appears after `refresh_stores.py` activates NULL-network stores — they now have an immediate actionable path to get prices rather than waiting days for the weekly tier to reach them.

Also generated `data/catch_up_store_ids.txt` manually for the 557 stores currently stale.

### 2026-06-23 — site: add most popular products section to index + tablou

Added `load_popular_products()` to `generate_site.py` — queries `prices_current` joined with stores/networks/products/categories, returns top N products by store coverage with coverage %, avg price, network count. Surfaces on:
- `index.html`: compact 10-row ranked section between stats and stories
- `tablou.html`: full 20-row card in the dashboard grid
- `analytics.html`: "Produse populare" tab now uses richer data (coverage %, avg price, network count) instead of the basic `v_product_popularity` view (blended rank / record count)

Top result: Ciocolata Asortata Merci (98.0%), Jacobs Kronung Cafea (97.7%) — both in all 10 networks.

### 2026-06-23 — product_stats.py — most common products by store coverage

New standalone script `product_stats.py`. Queries `prices_current` joined with stores, retail_networks, products, and categories to compute per-product: distinct store count, network count, avg/min/max price, and % of all stores. Outputs a terminal table (top 50 globally + top 3 per category by default) and `data/product_stats.csv`. Supports `--top N`, `--by-category N`, `--category NAME`, `--db`.

Top findings: Merci Ciocolata + Jacobs Kronung lead at ~97% coverage (all 10 networks, 4,019/4,009 stores). Data quality issues spotted: Raffaello max price 1,804.90 RON (likely erroneous) and Redis L-Carnitin Ins miscategorized as BRANZETURI — both added to backlog. Also fixed CLAUDE.md venv path (`venv` → `.venv`).

### 2026-06-23 — Sentinel sampling mode wired into fetch_prices.py

Implemented full sentinel sampling pipeline across three files:

**`analyze_price_similarity.py`** — added `--export-sentinels PATH` flag. Writes `data/sentinel_stores.json` keyed by `network_id` (matching `stores.network_id`). Only includes networks where ALL store types are Tier A — mixed-type networks (e.g. AUCHAN with Hypermarket=A and S&D=B) are excluded so we don't propagate wrong prices across incompatible formats. Currently qualifies: PENNY, LIDL, KAUFLAND, SUPECO.

**`db.py`** — added `propagate_network_prices(conn, source_store_id, target_store_ids, fetched_at)`. Copies `prices_current` from one store to many targets using SQL-level `INSERT … SELECT` (one statement per target store for efficiency). Writes to both `prices` history (INSERT OR IGNORE — skip if same price_date already recorded) and `prices_current` snapshot (upsert with `last_changed_at` guard — only bumped if price/promo actually differs).

**`fetch_prices.py`** — sentinel mode loads `data/sentinel_stores.json` at startup (auto-disabled on ISO-week full-scan). In the anchor loop, anchors whose entire cluster is non-sentinel stores in a Tier-A network are skipped (`propagate_last_checked` to maintain freshness, no API call). After each sentinel anchor successfully fetches prices, `propagate_network_prices` fires once per network per run to copy the sentinel's prices to all non-sentinel stores. New `sentinel_skipped=N` counter in all SUMMARY log lines.

**Expected impact** once `sentinel_stores.json` is generated:
- KAUFLAND 198 stores → 3 sentinel queries (~655 anchors saved)
- PENNY 439 → 3 (~145 anchors saved)
- LIDL 396 → 3 (~130 anchors saved)
- SUPECO 26 → 3 (~8 anchors saved)

Weekly full-scan resets sentinel mode, so no store misses a direct query for more than 7 days. Accuracy caveat: ~10–15% of products in Tier-A networks vary regionally; propagated prices for those products use the sentinel's regional price until the weekly full-scan corrects them.

**To activate on VPS:**
```bash
python analyze_price_similarity.py --days 30 --export-sentinels data/sentinel_stores.json
```

### 2026-06-23 — Geo-diverse sentinel store selection in analyze_price_similarity.py

Updated sentinel store selection to combine product coverage with geographic spread. Instead of simply taking the top-N stores by product count (which clusters in Bucharest), the script now fetches top-20 by coverage then applies a greedy farthest-point algorithm: start with the highest-coverage store, each next pick is whichever remaining candidate is furthest (haversine) from all already-selected stores. Result: 3 stores that triangulate the country while still covering the broadest product catalog. Terminal output now shows km spread between sentinels and coordinates.

### 2026-06-22 — Within-network price similarity analysis

Built `analyze_price_similarity.py` — read-only analysis of how uniform prices are within each retail network × store type, to guide store sampling strategy. Uses `fetched_at`-bounded queries (handles the mixed `DD.MM.YYYY` / ISO `price_date` formats by querying on `fetched_at` instead). Produces terminal table + markdown report.

**Key findings (30-day window, ≥3 stores per product×date):**

| Tier | Networks | Criterion |
|---:|---|---|
| **A** | PENNY, LIDL, KAUFLAND, SUPECO, AUCHAN-Hypermarket | >80% products within 1% spread |
| **B** | CARREFOUR-Hypermarket, MEGA IMAGE, AUCHAN-S&D, PROFI (both subtypes) | 40–75% within 1%, tail mostly within 10% |
| **C** | CORA, SELGROS, PROFI-Unknown | >65% of products have >10% spread |

Sentinel stores identified per Tier-A network (stores with broadest product coverage = best proxies for chain-wide pricing). Output: `docs/price-similarity-2026-06-22.md`.

Notable: CARREFOUR Express (S&D) and CARREFOUR Hypermarket price very differently (42% vs 73% within 1%) — store type is a better predictor than network alone for Carrefour. CORA's 79% spread >10% is surprising given its limited store count; likely reflects genuine regional/format differentiation.

Next step: wire Tier-A sentinel IDs into fetch sampling to reduce API calls for uniform chains.

### 2026-06-22 — Fix SQLite lock contention between gas and retail fetchers

Resolved recurring `sqlite3.OperationalError: database is locked` on `fetch_gas_prices`
(6/10 history hits, run #937 most recent). Two changes:

- **`db.py`**: increased `busy_timeout` from 30 000 ms → 60 000 ms (WAL mode was already
  set; 30 s was too short when retail held a write lock during a large batch commit).
- **Crontab + `scripts/crontab.template`**: moved gas cron from `5 3 * * *` → `40 3 * * *`.
  The old 3:05 slot started gas 5 min into the 3:00 retail slice, which runs until ~3:28.
  The new 3:40 slot also falls within a retail window (3:30–3:58) but WAL+busy_timeout
  handles brief per-batch lock windows. Crucially, 3:40 avoids the Monday 3:00 reference
  fetch collision that stacked two write-heavy jobs simultaneously.

### 2026-06-21 — refresh_stores.py: drain NULL-network stores (996 → 26)

Built `refresh_stores.py`, a one-off that re-surfaces every store cheaply to
repopulate `network_id`/`logo_url`/`type_*` from the store element (the fix in
`799d328`), writing **stores only — no prices**. It clusters active stores into
anchors (reusing `fetch_prices._cluster_anchors`) and queries a small basket of
near-universal products (dynamic top-10 by store coverage) at each anchor, so
one ~5-min pass makes ~all stores appear and get re-upserted. Works because
`network_id` now comes from the store-level `<Retailnetwork>`, present whenever a
store appears regardless of product/price.

Result: 1011 anchors, 4044 stores seen, 0 errors, 3 cap-hits. **NULL-network
990 → 26.** Logo backfill tagged 0 (the store-element path does all the work, as
predicted — store logos are format-specific and don't exact-match network logos).

**Material finding — the NULL bug was a network-attribution _bias_, not random.**
The 964 recovered stores were disproportionately discounters; per-network counts
corrected sharply: **PENNY 158→439, LIDL 194→396, CARREFOUR 177→380, PROFI
1334→1613.** These match real-world chain sizes far better than the old
undercounts. Consequence: the 2026-06-21 cross-network audit (which excluded the
then-996 unknown stores) under-represented exactly these chains — its
comparison-universe figures (audit §3–§6) should be re-run on the corrected data.

Residual 26 NULLs: 25 have prices, 15 active. They carry none of the basket or
sat at a dense-cluster 50-cap edge; they will drain via the normal daily
`fetch_prices` (which now parses the store element too). Not worth a second pass.

### 2026-06-21 — Store enrichment: logo_url, type, and network detection from API

Added three new columns to `stores` (`logo_url`, `type_id`, `type_name`) and improved `network_id` reliability:

- **Parser fix** (`api.py`): `parse_stores_and_prices` now reads `network_id` from `Retailnetwork > Id` on the store element (always present), instead of relying solely on product-level `Networkid` fields (which were silently skipped when all products had price=0 — root cause of the 996 NULL `network_id` stores). Falls back to product-level value if the store element has none. Logs a WARNING if both are present and disagree.
- **New fields**: `logo_url` from `Logo > Logouri` (per-format brand logo, e.g. `CarrefourMarket.png`); `type_id`/`type_name` from `Type` (e.g. "Supermarket & Discounter"). `upsert_store` uses `COALESCE(excluded.X, stores.X)` for all three + `network_id` so a non-null is never overwritten with null.
- **Logo-based backfill** (`db.py`): `backfill_store_network_from_logo` matches `stores.logo_url = retail_networks.logo_url` to infer `network_id` for any remaining NULLs after fetch. More reliable than name matching.
- **Conflict detection** (`db.py`): `check_store_network_conflicts` reports stores where `logo_url` implies a different network than `network_id` — run at fetch startup for data-quality auditing.
- The 996 existing NULL `network_id` stores will be resolved on the next `fetch_prices.py` run.

### 2026-06-21 — Full data audit (shops, products, same-price-across-networks)

Thorough point-in-time audit of `data/prices.db` after 68 days of scraping.
Full write-up in [`docs/data-audit-2026-06-21.md`](data-audit-2026-06-21.md).
Headlines: **5,460 shops** (4,135 retail + 1,325 fuel); **87,775 products,
75,710 priced, 23,200 sold by ≥2 chains**. **Same price across networks is
rare for retail** — 1,916/23,200 (8.3%) share an identical modal price, but 93%
are 2-chain coincidences; the robust core (≥3 well-stocked chains) is only **55
products**, all RRP-driven branded FMCG. Conversely ~83% of well-stocked
comparable products differ by >5%. **Fuel is the real same-price market**:
standard diesel varies 1.2% across all 7 networks, and 5 of 7 quote identical
standard-petrol prices.

Method: built per-(product, network) aggregates incl. modal price from
`prices_current` (mapped networks only) into a scratch DB
(`/tmp/audit.db`), then summarised. Reproduce with:
```sql
CREATE TABLE pnp AS
  SELECT pc.product_id pid, s.network_id net, pc.price price, COUNT(*) c
  FROM prices_current pc JOIN stores s ON pc.store_id=s.id
  WHERE s.network_id<>'' AND pc.price>0   -- excludes NULL-network stores
  GROUP BY 1,2,3;
-- modal price per (pid,net) via ROW_NUMBER() OVER (PARTITION BY pid,net ORDER BY c DESC);
-- then COUNT(DISTINCT mode_price)=1 over pid with n_networks>=2  => "same price".
```
Non-obvious findings: (1) the 996 unmapped stores carry `network_id IS NULL`
(not `''`), so `<>''` correctly excludes them — but that drops ~2.9M current
prices (~19%) from cross-network analysis; (2) unmapped-store count grew
243→996 since 2026-05-23 (likely a resumed `discover_stores.py`) — backlog item
filed; (3) the snapshot is a rolling ~30-day window (9% of rows >30d stale).

### 2026-06-21 — Implement pipeline-check upgrade suggestions

Four suggestions from the 2026-06-21 pipeline-check history analysis (check:store_freshness:10/10, check:run_history:10/10, error:database is locked:6/10, error:abandoned:6/10):

1. **WAL mode** — already at `db.py:45`; no change needed.
2. **Robust `finish_run` in exception handlers** — wrapped `finish_run` calls in `try/except` in all exception handlers in `fetch_prices.py` and `fetch_gas_prices.py`. Previously, if the DB was locked during cleanup, `finish_run` could throw and the run row would stay in `running` state, causing the next invocation's `abandon_stale_runs` to flag it as "abandoned". Now cleanup failures are silenced (the lock file and checkpoint are cleaned up separately and reliably).
3. **fetched_at freshness guard** — already auto-corrected in `fetch_prices.py:494-503`; no change needed.
4. **Pipeline-check RED alerting** — created `scripts/alert_pipeline_check.py` (mirrors `alert_red.py` but reads the last entry in `data/logs/pipeline-check.log`; exits 1 if the most recent verdict within 25h is RED). Added cron at 07:15 in `scripts/crontab.template`; placeholder UUID `REPLACE_WITH_PIPELINE_CHECK_HC_UUID` must be replaced before the HC.io alert fires. Crontab installed.

### 2026-06-15 — Pipeline check remediation: acknowledge pre-fix gas errors, fix cron drift

Two actions to clear RED pipeline state:

1. **Acknowledged 4 historical gas error runs** (IDs 692, 717, 742, 767 — June 9–12): these failed with `SSLCertVerificationError` before the TLS fix landed. Set `acknowledged_at = now()` via SQL UPDATE so `check_run_history` no longer flags them. `run_history` audit check is now GREEN. Today's gas run (03:05) completed with 5245 records — fix is confirmed working.
2. **Fixed cron drift**: reinstalled crontab from `scripts/crontab.template` to add the `alert_red.py` line (06:02) in its correct position. HC ping will silently no-op until `REPLACE_WITH_HC_UUID` is replaced with a real UUID, but `alert_red.py` runs and logs to `data/logs/alert-red.log` from today.

### 2026-06-14 — fetch_gas_prices hardening: SIGTERM, stale-date, SSL resilience

Implemented three fixes in `fetch_gas_prices.py` based on pipeline-check history analysis:

1. **SIGTERM handler**: added `signal.signal(SIGTERM, lambda *_: sys.exit(0))` at the top of `main()`. Previously SIGTERM left the run in `running` state (marked `abandoned` at next startup). Now it goes through the `finally` → `finish_run()` path like Ctrl-C.
2. **Stale checkpoint date**: if an in-progress checkpoint's `fetched_at` is from a prior day, `fetched_at` is now refreshed to today before the run starts. Previously gas prices could be stamped with yesterday's date on a resumed interrupted run.
3. **Per-request SSL/connection resilience**: added `except (SSLError, ConnectionError)` around `fetch_xml()` calls. A transient TLS or network failure on one UAT×fuel combo now logs a warning and continues rather than aborting the entire run. The key is not added to `done` so it gets retried on the next run.

**SSL error investigation**: Runs #692–767 (2026-06-09 to 2026-06-12) failed on the first request because certifi lacked the Sectigo intermediate — same root cause as the 2026-06-12 TLS fix. It wasn't UatId=1017 specifically; that's just the first UAT. The TLS fix (extra_certs.pem) resolved it; these errors will age out of the 7-day audit window by 2026-06-19.

**Alert cron**: still needs a real HC.io UUID before the `alert_red.py` line can be installed from crontab.template. Cron drift expected until then.

### 2026-06-13 — Implement pipeline-check upgrade suggestions

Three suggestions from the 2026-06-13 pipeline-check history analysis:

1. **WAL mode** — already present in `db.py:45`; no change needed.
2. **SIGTERM/abandoned fix** (`fetch_prices.py:939`): changed `except KeyboardInterrupt:` to `except (KeyboardInterrupt, SystemExit):`. The SIGTERM handler converts SIGTERM→`SystemExit`, which previously bypassed `save_cp()` and `finish_run()`, leaving runs as "abandoned" in the DB. Now SIGTERM triggers the same clean-shutdown path as Ctrl-C.
3. **RED alert** (`scripts/alert_red.py` + `scripts/crontab.template`): new script exits 1 when today's audit JSON is RED, 0 otherwise. Wired into cron at 06:02 via `hc_run.sh` with a placeholder HC.io UUID — user must create the HC.io check (period=1d, grace=30m) and replace `REPLACE_WITH_HC_UUID` in the crontab template and live crontab.

### 2026-06-12 — Fix TLS breakage: append missing Sectigo intermediate to certifi bundle

- **Root cause**: server renewed its TLS cert on 2026-05-26 with issuer "Sectigo Public Server Authentication CA DV R36" but kept sending the old "Sectigo RSA Domain Validation Secure Server CA" intermediate in the TLS handshake — a mismatched/broken chain. Browsers work because they auto-fetch missing intermediates via AIA; Python `requests`/`certifi` does not.
- **Fix**: downloaded the missing intermediate from the AIA URL (`http://crt.sectigo.com/SectigoPublicServerAuthenticationCADVR36.crt`), converted DER→PEM, appended to `venv/lib/python3.12/site-packages/certifi/cacert.pem`.
- **Durable fix** (same session): saved intermediate as `data/extra_certs.pem` (committed); `api.py` now calls `_build_ca_bundle()` at import time — merges certifi roots + `extra_certs.pem` into a per-process tempfile and passes it as `verify=` to all `requests.get` calls. Venv rebuilds no longer break TLS.

### 2026-06-08 — fetch_prices SIGTERM handler; CLAUDE.md venv path fix

- **SIGTERM handler** (`fetch_prices.py`): added `signal.signal(SIGTERM, lambda *_: sys.exit(0))` immediately after the lock file is written. Converts SIGTERM → SystemExit so the existing `finally` block removes the lock on cron kill. WAL mode and lock `finally` were already in place; this closes the one remaining gap.
- **CLAUDE.md**: replaced two stale `~/devbox/envs/240826/` references with `source venv/bin/activate` (the local venv that actually exists).
- Pipeline-check skill already used `venv/bin/activate` correctly — no change needed.

### 2026-06-08 — pipeline-check: history pattern analysis + upgrade suggestions

Enhanced `/pipeline-check` to scan its own log history and surface recurring issues as actionable upgrade suggestions.

- **New step 8** parses the last 10 `pipeline-check.log` entries, counts how often each audit check went RED, which error strings recurred, and overall verdict distribution. Emits structured tokens (`check:run_history:6/10`, `error:database is locked:4/10`, etc.) gated at ≥3/10 to avoid noise.
- **New "Upgrade suggestions" section** appears in the report only when patterns hit threshold. Each suggestion names a concrete target (file:line, PRAGMA, cron time). Seeded from actual log history: `database is locked` recurrence → WAL mode; `abandoned` runs → trap/lock cleanup; `store_freshness` cycling → `fetched_at` freshness guard; morning-only `anomaly_drift` RED → shift audit cron to 10:00.
- Clean pipelines with GREEN history stay quiet — the section is fully gated.

### 2026-06-03 — Operational durability: DB backup + log rotation; retention investigated

The pipeline had no safety net for its 7.2 GB dataset. Added two safe, additive guards and investigated (but deferred) retention.

- **DB backup** — `scripts/backup_db.sh`: streamed `sqlite3 .dump | gzip` (consistent snapshot, no full-size temp on a 14 GB-free partition), `gzip -t` verify, ≥4 GB free guard, keeps last 2. Cron Sun 10:00 (after the ~08:41 daily-pass completion). Same-partition only — protects against logical corruption / accidental delete / botched VACUUM, not disk failure; commented `rsync` line for off-box. First backup run + verified.
- **Log rotation** — `scripts/logrotate.conf` (size 50M, keep 4, compress, `copytruncate` to keep fetch_prices' long-lived fd valid), cron daily 10:30 with a local state file (no root). Validated with `logrotate -d`: correctly flags the 68 MB `fetch-prices.log`, skips the rest.
- **Crontab** — added both lines to `scripts/crontab.template` and applied to the live crontab (backed up first).
- **Retention (deferred)** — runway ~12–15 months (~30–100k rows/day, ~20–35 MB/day), so not urgent. Key gotcha logged in backlog: `prices.price_date` is `DD.MM.YYYY` text → lexical sort, so any retention DELETE must parse the date or use `fetched_at` (ISO). First backup must exist before any delete.

### 2026-06-03 — Committed + verified the throughput fix; declined the run-row rewrite

**Committed** the 2026-06-02 working-tree changes (per-anchor checkpoint, `inflight_prod_ids`, `SLEEP_BETWEEN` 0.05, gas cron 03:00→03:05) as `e3a324c`, with the `gas_checkpoint.json` fixture refresh split into `c11e8f2` to keep the perf diff reviewable.

**Verified the throughput fix worked (was the deferred 06-03/04 backlog item):** full pass #583 completed same-day (`fetched_at` 00:33 → completed 08:41, ~8h wall vs ~51h before); `store_freshness` 100% → 41.57% → **0.61%**; audit GREEN 0/4; no real HTTP 429 after the sleep cut (the "429" log hits are the substring in "Skipping 429 …" + numeric counts); resume-list path exercised 45× with no errored runs since #459 (05-28, pre-`busy_timeout`). Ticked the three verify items in `backlog.md`.

**Investigated the run-row "bloat" (#2) and decided against a rewrite.** The idea was one `runs` row per logical daily pass instead of ~18 interrupted rows/day. Declined because `abandon_stale_runs()` (every startup, before `start_run`) would abandon a reused `'running'` row, the change touches all three monitors (`status.py`/`check_runs.py`/audit) for a cosmetic gain, and `status.py` already colors `interrupted` yellow + the audit ack prevents false RED. The per-slice interrupted row is load-bearing. Full rationale logged in `backlog.md`. Net code change this session: none beyond the commits above — the right call was to ship the verified win and not add risk for cosmetics.

### 2026-06-02 — Retail throughput: per-anchor checkpoint, in-flight product list, lower sleep

**Context:** `/pipeline-check` came back RED — `store_freshness` 41.57% stale (1693/4073 >2d), regressing day-over-day (23.6% → 41.57%), and `check_runs` reporting the last *full* `fetch_prices` completion 51.7h ago. Other checks green (`run_history` now ok, confirming the 2026-06-01 ack fix works).

**Diagnosis (root cause):** a full anchor×batch pass takes ~51h but the freshness window is 2 days → **zero margin**, so stores age out faster than the sliced backfill refreshes them. Scale: 87,775 products, ~675 anchors, ~150 batches/anchor avg ≈ ~100k HTTP requests/pass. Effective rate ~1.8s/batch vs ~0.3s theoretical (sleep+HTTP) → ~6× overhead. Need ~2× throughput.

Three fixes implemented in `fetch_prices.py` (chosen as the low-risk, local subset; bigger structural options — single long-lived worker, parallel workers, clustering cache, SLO right-sizing — logged in `backlog.md` under "Pipeline health → 2026-06-02 throughput diagnosis"):

1. **Per-anchor checkpoint instead of per-batch.** `_save_checkpoint` was called after *every* batch, re-serialising the entire growing `done` set ~100k times/pass — O(n²) in `len(done)`. Replaced all call sites with a single `save_cp()` closure (captures live state by reference) invoked once per anchor and on every exit path (timelimit, skip paths, `KeyboardInterrupt`/`Exception`). `done.add(key)` stays in memory per batch; on a hard kill mid-anchor we re-fetch that anchor's batches next run (idempotent via `INSERT OR IGNORE`). The intra-anchor timelimit break — the common interruption — calls `save_cp()`, so normal slicing loses nothing.

2. **Persist the in-flight anchor's filtered product list.** This also fixes a **latent data-gap bug**: when an anchor was interrupted mid-processing, resume fell back to `global_batches` (the full product list) "for stable batch indices". But `_products_for_anchor` has no `ORDER BY` (SQLite `DISTINCT` order isn't stable) and the anchor's own writes mutate `prices_current` mid-pass — so the global fallback could mark batches done while *never fetching the anchor's real products*. New `inflight_prod_ids` checkpoint field stores the exact filtered list (str anchor-id key → int product ids) for the in-flight anchor; resume rebuilds the identical batch list. Stays tiny (fetching is serial → ≤1 anchor in-flight); popped on completion/skip; empty dict omitted from the JSON. Legacy checkpoints without the field fall back to the old global path.

3. **`SLEEP_BETWEEN` 0.15 → 0.05.** ~0.15s × ~100k batches ≈ 4h of pure sleep/pass; cutting it saves ~2.5h. Dated comment in the constant; watch for HTTP 429 (backlog item to monitor).

**Verification:** `py_compile` clean; grep confirms the only `_save_checkpoint` caller is `save_cp()` and all exit paths use it. Checkpoint round-trip test (int-key ↔ on-disk str-key restore; empty-dict omission). Isolated end-to-end on a throwaway DB with mocked network exercising **fresh → mid-anchor interrupt → resume**: PASS1 interrupted, persisted `inflight=['1']` + 2 done batches; PASS2 resumed, consumed the in-flight list, completed all 8 batches with `inflight` omitted and `status=completed`. Live checkpoint/DB untouched (temp dir). Not yet validated under real traffic — see backlog verification items dated 2026-06-03/04.

### 2026-06-01 — Pipeline optimization: stale-first ordering, dead anchor skiplist, intra-anchor timelimit, run_history acknowledgment

**Problem:** Pipeline has been RED since 2026-05-27. Root causes: (1) rural PROFI/Unknown/MEGA IMAGE anchors at the tail of population-ordered cycles were reaching 35–41 day staleness; (2) PROFI VICOVU DE JOS (dead anchor) burned ~46 min/cycle with 0 prices; (3) pre-fix abandoned runs (#363, #388, #413, #438) kept `run_history` RED with no silence path.

**`fetch_prices.py`:**
- `--order stale` (new default): queries `prices_current.last_checked_at` per store, aggregates to per-anchor max-staleness, sorts descending. New helpers: `_load_stale_map()`, `_stale_age()`. Recomputed at each run start including resumes.
- Dead anchor skiplist: checkpoint key `anchor_failures`; after `DEAD_ANCHOR_THRESHOLD=3` consecutive all-fail runs (0 prices + all batches `RequestException`), anchor skipped for `DEAD_ANCHOR_SKIP_DAYS=7` days. Survives `--fresh`. Clear with `--reset-skiplist`.
- Intra-anchor timelimit: check added at top of inner batch loop; caps over-run to < 1 batch (~93s) vs prior ≤ 46 min.

**`db.py`:** Migration adds `acknowledged_at TEXT` to `runs`.

**`audit_pipeline.py`:** `check_run_history` adds `AND acknowledged_at IS NULL`; samples gain `acknowledged` field.

**`ack_run.py` (new):** `python ack_run.py --list | <IDs> | --before <date>` to acknowledge bad runs.

**Immediate action:** Acknowledged pre-fix runs #363, #388, #413, #438.

### 2026-05-31 — Fix `database is locked` (gas) — Python timeout, not PRAGMA

**Root cause (revisited):** The 2026-05-28 fix added `PRAGMA busy_timeout=30000` but gas kept failing with `database is locked` at `db.py:182` for 3 consecutive days (2026-05-29–31). Root cause: Python's `sqlite3` module uses its own `timeout` parameter from `sqlite3.connect()` (default 5 s) to enforce the busy wait — `PRAGMA busy_timeout` sets the SQLite C-layer handler, which the Python module does not use. With the default 5 s timeout and retail holding a write lock at `init_db` time, gas would exhaust the timeout before retail released.

**Fix:** Changed `sqlite3.connect(path)` → `sqlite3.connect(path, timeout=30)` in `db.py:init_db`. The PRAGMA is retained for any non-Python readers of the DB.

**Belt-and-suspenders:** Staggered gas cron from `0 3` → `5 3` (03:05). By 03:05 retail's `init_db` write (ALTER TABLE / UPDATE backfill) is long finished; only brief per-batch commits remain, which release in milliseconds.

**Backlog:** Added "Split retail and gas into separate SQLite databases" entry — the permanent architectural fix, deferred since the timeout+stagger should hold.

### 2026-05-28 — Fix `database is locked` errors; add SQLite busy_timeout

**Root cause:** `fetch_gas_prices` (cron `0 3 * * *`) and `fetch_prices` (cron `*/30`) both start at 03:00, writing to the same SQLite file simultaneously. With no `busy_timeout` set, the second writer immediately raised `database is locked` instead of retrying (runs #363, #438, #459).

**Fix (`db.py:init_db`):** Added `PRAGMA busy_timeout=30000` (30 s) after `journal_mode=WAL`. SQLite will now retry writes for up to 30 s before erroring, absorbing the brief 03:00 contention window. One-line change; affects all scripts sharing the DB.

**Also:** Identified that the `--max-runtime 1700` crontab setting was already correct. Per-store overshoot to ~1999 s is benign — the PID-guarded lock file prevents any true concurrent retail run.

**Recovery:** Launched a manual `--resume` backfill in a detached `screen` session to clear 9.8 days of stale stores (91.57% stale at time of fix).

### 2026-05-27 — Auto-refresh `fetched_at` on stale checkpoint resume

**Root cause:** `*/30` cron slices were resuming an `in_progress` checkpoint and inheriting its original `fetched_at` (set when the session first started, potentially days ago). Every price inserted during those slices was stamped with the old date, making the audit see 100% stale stores even though real fetches were happening continuously.

**Fix (`fetch_prices.py:_main_body`):** When loading an `in_progress` checkpoint whose `fetched_at` predates today (UTC), reset `fetched_at = datetime.now(UTC)` before the anchor loop. The `done` key set is fully preserved — no work is re-fetched; only the timestamp advances. Log line: `fetched_at refreshed: YYYY-MM-DD → YYYY-MM-DD (Nd stale, auto-corrected)`. This makes the fix transparent in the log so it's immediately visible on the next cron firing.

**Also confirmed:** SQLite WAL mode (`PRAGMA journal_mode=WAL`) was already active in `db.py:init_db` — no change needed. The two "database is locked" runs (#438, #363) pre-date or are edge-race cases; WAL serializes concurrent writes gracefully going forward.

**Impact:** Removes the need for manual `--fresh` runs after any multi-day backfill. On next cron fire, prices will be stamped today and `store_freshness` audit will begin recovering immediately.

### 2026-05-23 — Store-level tiering + unknown network backfill

**Store-level tiering (`db.py`, `fetch_prices.py`)**

Added `fetch_tier TEXT DEFAULT 'daily'` to `stores`. `update_store_tiers(conn, days=7)` promotes stores with no price change in 7 days to `'weekly'` and re-demotes on any detected change — called at `fetch_prices.py` startup. In the anchor loop, any anchor whose entire covered cluster is on the weekly tier skips the API call entirely (`propagate_last_checked` + checkpoint advance), same pattern as canary. Full-scan ISO-week disables the skip so no store is missed for >7d. Checkpoint persists `weekly_store_ids` for consistent resume. SUMMARY line adds `tier_skipped=N weekly_store_tier=N`. Cold-start: benefits accrue after 7 days of `last_changed_at` data accumulating (column added 2026-05-23).

**Unknown network store backfill (`db.py`, `fetch_prices.py`)**

Audited 681 `network_id IS NULL` stores. The active fetch run had already filled most via `upsert_store`. Added `backfill_store_network_ids(conn)` with 12 name-pattern rules covering Mega Image (SG/MI prefix), Carrefour (Express prefix), Selgros (Seglros typo variant), and all other named networks. Ran on live DB: 85 more stores tagged. Called at `fetch_prices.py` startup so newly discovered stores are tagged immediately. 243 stores remain genuinely unidentifiable (city+street-only names, MARKET/SUPER prefix without confirmed network match).

### 2026-05-23 — price_date ISO normalization

Fixed `price_date` format in `api.py` so all new inserts land in ISO `YYYY-MM-DD HH:MM` instead of the API's `DD.MM.YYYY HH:MM` (retail) and `DD/MM/YYYY HH:MM` (gas). Added `_parse_date(s)` helper; applied at both parse sites (`parse_stores_and_prices`, `parse_gas_items`/`update_date`). Passes through strings already in ISO form — safe on resume after a partial migration.

One-time backfill script `migrate_price_dates.py` handles ~35M existing rows (20M `prices`, 15M `prices_current`, 67K `gas_prices`, 1.3K `gas_stations.update_date`). Batched 500K rows at a time by rowid to avoid locking the DB; includes dry-run mode. Run on VPS: `python migrate_price_dates.py`.

After migration, SQLite `date()`, `strftime()`, and `<`/`>` comparisons on `price_date` work without wrappers.

### 2026-05-23 — Fetch pipeline optimisations: dead-store pruning + canary + product tiering

Implemented three complementary optimisations to reduce the ~40–57 h full-sweep cycle:

**1. Dead-store pruning (`db.py`, `fetch_prices.py`, `generate_pipeline_report.py`)**
- Added `is_active INTEGER DEFAULT 1` column to `stores`; index `idx_prices_current_store` on `prices_current(store_id)`.
- `deactivate_stale_stores(conn, days=21)`: marks stores with no `prices_current` activity in 21d as `is_active=0`; called at `fetch_prices.py` startup.
- `upsert_store` sets `is_active=1` on any API sighting (re-activates if store reappears).
- `load_store_freshness` now excludes `is_active=0` stores from the freshness denominator — dead stores no longer drag the audit metric.

**2. Canary skip for KAUFLAND / LIDL / PENNY (`fetch_prices.py`)**
- `CANARY_THRESHOLDS = {KAUFLAND:36, LIDL:72, PENNY:82}` (≈20% of each chain's store count).
- Tracks `canary_seen` (store IDs seen per network) and `canary_changed` (networks with a detected price change) during the run.
- Once threshold reached with no change: pure-uniform anchors call `propagate_last_checked` + skip API call entirely; mixed anchors filter product batch to non-uniform stores only (reducing batch count).
- State persisted in checkpoint (`canary_seen`, `canary_changed`) for correct resume behaviour.
- Full-scan week (ISO-week-start, same as ghost filter) disables canary entirely so no price is missed for >7d.
- `insert_price()` now returns `True` (changed) / `False` (unchanged) — used to populate canary change tracking.

**3. Product-level tiering (`db.py`, `fetch_prices.py`)**
- Added `last_changed_at TEXT` to `prices_current`; set only on actual price/promo change (not every check). `last_checked_at` still updated every fetch.
- `_build_weekly_product_tier(conn)`: returns product IDs where `last_changed_at < 30 days ago`; these are excluded from daily anchor batches.
- Tier computed once per ISO-week, cached in checkpoint as `weekly_tier_ids`.
- Cold-start: benefits accrue after ~30d of `last_changed_at` data (column added today).

**Shared SUMMARY line** now includes `canary_skipped=N weekly_tier=N` fields.

**Backfill note:** `prices_current.last_changed_at` backfill (`= last_checked_at` for existing rows) failed on first `init_db` call due to the live fetch holding the write lock. Backfill will succeed on next clean startup. In the interim, `last_changed_at IS NULL` for existing rows — no functional impact (product tier returns empty set, canary tracks changes going forward).

### 2026-05-22 — PROFI regional (intra-UAT) uniformity analysis

Follow-up to the national-pricing analysis. Ran intra-UAT price variance query for PROFI: for each (product, UAT) pair with ≥2 PROFI stores, is the price uniform across all stores in that UAT?

**Results (7-day price window):**
- Overall intra-UAT uniformity: **59.9%** (vs 18.7% national)
- Per-UAT uniformity distribution:

| Uniformity bucket | UAT count | Avg % uniform | Avg stores/UAT |
|-------------------|-----------|---------------|----------------|
| 90–100% | 19 | 96.0% | 2.5 |
| 75–89% | 4 | 82.6% | 2.8 |
| 50–74% | 38 | 63.6% | 4.4 |
| 25–49% | 21 | 37.5% | 18.2 |
| 0–24% | 5 | 19.9% | 2.0 |

**Key finding: regional canary does NOT work for PROFI.** The high-uniformity UATs (90–100%) have on average only 2.5 stores — "uniform" just means two stores happen to agree, not a reliable pattern. The large cities that hold the bulk of PROFI stores (București 86 stores → 44.8%, Timișoara 55 → 32.8%, Iași 26 → 37.7%, Bacău 25 → 32.0%) are the *least* uniform. The 25–49% bucket with avg 18.2 stores/UAT captures all major cities. PROFI prices are genuinely store-level, not regional.

**Conclusion:** canary strategy (national or regional) is not viable for PROFI. The correct optimization path for PROFI is the **tiered fetch frequency** approach (daily/weekly tiers based on per-store volatility). Backlog item updated accordingly.

### 2026-05-22 — National-pricing uniformity analysis

Ran a per-network intra-store price variance query to validate the "national pricing canary" hypothesis (fetch 5–10 representative stores per chain; propagate prices to remaining stores).

**Results (products with ≥3 stores, % with zero price variance across all stores in chain):**

| Network | Stores | % Uniform |
|---------|--------|-----------|
| KAUFLAND | 172 | 91.6% |
| LIDL | 357 | 86.8% |
| PENNY | 409 | 82.6% |
| SUPECO | 24 | 67.9% |
| MEGA IMAGE | 922 | 54.9% |
| AUCHAN | 40 | 33.1% |
| CARREFOUR | 357 | 28.2% |
| PROFI | 1222 | 18.7% |

**Key finding:** The canary strategy is viable for LIDL/PENNY/KAUFLAND (87–92% uniform) and saves ~908 active store fetches combined (~27% total reduction). However, PROFI — the largest network by store count (1222 stores, 30% of all active stores) and the primary driver of stale metrics — has the *lowest* uniformity at 18.7%. Prices vary substantially across PROFI stores; a national canary would miss 81% of price variance.

**Next investigation:** check whether PROFI pricing is uniform within a UAT/region (regional canaries) or truly store-level. If per-UAT uniformity is high, one canary per county (~40 canaries) cuts PROFI fetches by 97%.

**Backlog updated:** added "National-pricing canary fetch strategy", "Tiered fetch frequency by store price volatility", "Audit and prune Unknown network stores", "Fix cron-interruption churn", "Parallel anchor fetching" items.

### 2026-05-20 — Stale checkpoint root cause diagnosis + pipeline-check backfill progress + logging improvements

Ran `/pipeline-check` and observed YELLOW (store_freshness 78.72% → 48.95%, recovering). Diagnosed why recovery is so slow despite 226 daily cron slices.

**Root cause:** checkpoint `fetched_at = 2026-05-18`, `status = in_progress`. The date-guard in `_main_body` only fires for `status == "completed"` runs — an `in_progress` checkpoint resumes unconditionally regardless of how old it is. All 226 cron slices were resuming the May-18 session and stamping every inserted price with `fetched_at = 2026-05-18`. Because `prices_current.last_checked_at` reflects that timestamp, even stores fetched yesterday show as >2d stale in the audit. Fix: `--fresh` to start a new session timestamped today; the May-18 checkpoint's 33 751 done keys are useless since the prices they inserted are all stale-dated.

**`/pipeline-check` enhanced** (`.claude/commands/pipeline-check.md`): step 2 now always runs a "Backfill progress" block when a lock is held. Reads `data/prices_checkpoint.json` for done-key count, `status`, and `fetched_at`; flags when `fetched_at` predates today (stale-date warning — signals a `--fresh` run is needed). Tails the last `SUMMARY` line from `data/logs/fetch-prices-backfill.log` (falling back to `fetch-prices.log`). Output format added to the report template.

**`fetch_prices.py` logging improved:** start banner now prefixed `[HH:MM:SS]` UTC and also shows `~N total batches` estimate alongside anchor/product counts. Per-store completion line now timestamped and shows `(K/Total)` anchor counter — makes log files readable instead of bare tqdm escape sequences.

### 2026-05-19 — Pipeline-check report logging

Added step 8 to `.claude/commands/pipeline-check.md`: after composing the final report, appends it verbatim to `data/logs/pipeline-check.log` (creates directory if missing). Enables `tail -f` monitoring of check runs and keeps a history of verdicts without any schema or DB changes.

### 2026-05-15 — Adaptive cluster split + cap-hit logging in `fetch_prices.py`

Closed the cluster-overflow / 50-store cap coverage gap diagnosed yesterday.

**Change 1 — Adaptive cluster split.** Extracted the body of `_cluster_anchors` into a `_greedy_set_cover` helper. The new `_cluster_anchors` wrapper runs set-cover at `BUFFER_M = 5000`, then recursively re-clusters any cluster with >`MAX_STORES_PER_CLUSTER` (50) members at half the radius, bottoming out at `MIN_CLUSTER_RADIUS_M = 1250`. Returns a third value `anchor_radius: Dict[int, int]` so each anchor's effective buffer is plumbed to the URL builder. Real-DB measurement: 681 → 1002 anchors total (+47%); 294 sub-anchors from adaptive split; only **3 anchors remain oversize in all of Romania** (at the 1250m floor). p95 cluster size lands at 35; max 54.

**Change 2 — Cap-hit logging.** Per-anchor counter increments when `len(result_stores) >= 50 and len(anchor_covers[sid]) > 50`. One aggregated `CAP-HIT anchor=… cluster=… radius=… batches_capped=…` line emitted at end of each per-anchor sweep (not per batch — would be too noisy). Lands in `data/logs/fetch-prices.log` via existing cron redirect.

**Checkpoint compat — lenient.** No version gate. Old `done` entries whose `sid` survives as an anchor still help; new sub-anchor sids fetch fresh on first encounter. One-time partial re-fetch on upgrade absorbed by the re-entrant `*/30` cron.

**Verification.** Synthetic 60-store dense cluster + rural-20 + empty input + real-DB-4099 cases all pass clustering invariants (every input store covered, no oversize cluster above `MIN_CLUSTER_RADIUS_M`). API probe confirmed cap fires by buffer density, not by `OrderBy` (50 returned at 5km / 2.5km; 40 at 1.25km from central București). The 06:30 UTC cron slice that was already in-flight when the change landed continues on the old code path (Python imported pre-edit); next cron firing at 07:00 picks up the new code. Tomorrow's `/pipeline-check` should show `store_freshness` drop and no longer surface București as the dominant stale cluster.

**Follow-ups left in backlog.** `OrderBy=price → dist` (matters only if cap-hits persist at the 1250m floor), tightening `MIN_CLUSTER_RADIUS_M` if 3-anchor floor proves too generous, and resuming `discover_stores.py` (cap-hits below 50 known stores would imply the API knows stores our table doesn't).

### 2026-05-14 — `/pipeline-check` trend + drill-down; București cluster-overflow root cause

Enhanced `.claude/commands/pipeline-check.md`:

- **Trend** — loads today / yesterday / 7d-ago `audit-YYYY-MM-DD.json` and prints day-over-day deltas per check. Today: `store_freshness 71.07% → 21.78%` (active recovery).
- **Drill-down on RED** — runs only when overall=RED. For `store_freshness`, single SQL returns top-5 networks by stale-count using date-only diff (matches audit predicate but evaluates *now*; labels output "live vs audit-snapshot at HH:MM" so the gap reads as recovery, not contradiction).
- **Next-slice ETA** for the `*/30` retail cron, plus a narrow trend-aware RED→YELLOW downgrade when `store_freshness` is the only red check and stale_pct improves by >20 pp day-over-day.

Then drilled into the MEGA IMAGE / PROFI București gap from yesterday's backlog stub. **Root cause confirmed**: each București 5 km cluster covers 230–324 retail stores (measured) vs the API's 50-store-per-response cap. Stale cohorts share exact-microsecond `last_checked_at` timestamps, proving each cohort was covered by one anchor call and never again — anchor rotation happens to leave certain edge stores outside the 50-nearest set indefinitely. Backlog entry rewritten with three ranked fixes (adaptive cluster split / cap-hit logging / stale-rescue pass); no code changes this pass — design tradeoff (slice runtime vs coverage) needs review first.

### 2026-05-14 — `/pipeline-check` command + freshness drill-down

Added `.claude/commands/pipeline-check.md`: read-only health check that runs `status.py`, inspects in-flight processes + lock, reads today's audit JSON, tails the four log files, diffs `crontab -l` against `scripts/crontab.template`, checks per-cron heartbeat, and emits a single GREEN/YELLOW/RED verdict block. Synthesises rather than dumping raw output.

Triggered by drilling into the 2026-05-14 06:02 RED audit (860/3948 stores stale, 21.78%). Findings:

- **Three distinct problems were lumped under one signal.** Orphan stores (827 with `network_id IS NULL`, 150 never priced) are a permanent ~4% floor. București MEGA IMAGE 44/319 (14%) and PROFI 12/110 (11%) are an actual urban-density coverage gap from the 5 km anchor radius + 50-store API cap. ~75 stores last seen 1–4 weeks ago are likely closed and should be marked inactive.
- **The 860 was inflated by audit timing.** At 06:02, run #146 was still in-flight; by 22:49 the live count had dropped to ~261. Real "structurally stale (audit logic)" count is closer to 275, ≈7% — under the 10% RED threshold once orphans and lifecycle stragglers are excluded.
- **Three backlog items filed** under General → Pipeline health: split the freshness signal by cause, investigate the București MEGA IMAGE/PROFI gap, and mark long-stale stores inactive. None of `audit_pipeline.py` or the schema changed in this pass — those are the backlog work.

### 2026-05-12 — Re-entrant retail cron (Option A in pipeline-cadence)

Replaced the daily 04:00 retail cron with `*/30 * * * *`, `--max-runtime 1700`. Lock + checkpoint already supported this; no script changes needed. Kill recovery drops from 24 h to ~30 min.

- Health is checked separately at 07:05 via `check_runs --max-age-hours 36` wrapped in `hc_run.sh` (UUID `48b9dd2d-…` reused). Keeps the work cron quiet — otherwise we'd ping healthchecks.io 48×/day, most as no-ops, which would mask real failures.
- `hc_run.sh` is intentionally **off** the work cron. The cron line just runs fetch_prices and appends to the log.
- Slice budget is 1700 s = 28 m 20 s, leaving ~1 min margin inside the 30-min interval. If a slice goes over (max-runtime check only fires between anchors), the next firing's lock check sees a live PID and no-ops — worst-case lost window is one cron tick.
- `check_runs --max-age-hours 36` (up from 25): a fresh session that just started yesterday at 23:00 could legitimately finish 25+ hours later under variable API latency; 36 h gives margin without going lax.
- Template updated in `scripts/crontab.template`; deploy with `crontab scripts/crontab.template`. Gas / audit / reference lines unchanged.

### 2026-05-12 — Retail cadence investigation + audit `run_history` fix

Followed up on `status.py` showing "5d 17h" durations and a perma-RED audit. Wrote findings to [`docs/pipeline-cadence.md`](pipeline-cadence.md).

- **`audit_pipeline.py:check_run_history`** now ignores `abandoned`/`error` rows that share `(script, started_at)` with a later `completed` row. The audit was reporting the same 11-row noise that `status.py` shows; with the fix, audit flips to `ok`. Real failures (no `completed` for the session) still surface.
- **Measured the actual work:** 29 760 batches × 1.70 s/call (live test) ≈ 15.3 h for a full sweep. That fits inside the 23 h `--max-runtime`. The 3–7 day calendar cost is **external kills + 20 h waits for the next 04:00 cron**, not slow code.
- **Confirmed kill mechanisms:** `last reboot` shows host rebooted 2026-05-12 08:04 (mid-session); `journalctl --user` has `oom-kill` on `app.slice` at 11:30 the same day. Previously the backlog only called out unattended-upgrades — reboots and OOM are additional vectors.
- **`runs.started_at` is really `fetched_at`** — reused across firings via the checkpoint (`fetch_prices.py:316` → `:404`). That's why all 7 May-7 rows share `2026-05-07T04:00:02`. `status.py` duration math is misleading because of this; flagged in the doc, schema fix already on backlog.
- Did **not** change `status.py`, the schema, or add a supervisor — those are separate. The doc ranks the fixes.

### 2026-05-12 — `status.py` CLI digest

Single-shot CLI summary of pipeline state — three sections: last N runs (default 10), per-script summary over last 7d (`--days N`), and the latest data-quality audit verdict (read directly from `data/logs/audit-*.json`, never recomputed). No new deps, stdlib + sqlite only, read-only DB connection, always exits 0. ANSI colors auto-disable when piping or with `--no-color`.

- Reuses `check_runs.parse_iso` but wraps it locally because Python 3.11's `fromisoformat` parses `"YYYY-MM-DD HH:MM:SS"` as naive (the path's `replace+fromisoformat` succeeds and skips the explicit `tzinfo=utc` branch). That's a latent bug in `check_runs.parse_iso` — only doesn't bite there because it only subtracts against `now`. Worth fixing at the source if `parse_iso` gets another caller.
- Duration is suppressed (`—`) for `abandoned`/`error` rows because their `started_at` is anchored to a stale checkpoint timestamp (see `check_runs.py` header comment), making the computed delta meaningless.
- `STALE` marker on per-script line uses the same 25h threshold as `check_runs.py` default.

### 2026-05-12 — Monitoring layer: check_runs, audit_pipeline, hc_run wrapper

Added a small monitoring layer on top of the existing `runs` table and healthchecks.io integration. Three new files, plus crontab + log-path changes.

- **`scripts/hc_run.sh`**: wraps any command with healthchecks.io `/start` and conditional success/`/fail` pings. Previously the cron pinged the healthcheck unconditionally via `;`, so failed runs still showed green.
- **`check_runs.py`**: fast post-fetch check. Reads the most recent `status='completed'` row from `runs` for a given `--script`; fails if stale (>25 h), missing, or wrote zero records. Honours the per-script lock file so a long resume (e.g. fetch_prices spanning multiple days) doesn't trip a false fail. The lock check verifies the PID is still alive.
- **`audit_pipeline.py`**: daily 06:00 data-quality audit. Uses a read-only SQLite connection (so it never contends with the live fetcher's write lock) and reuses signal loaders from `generate_pipeline_report.py`. Writes both `audit-YYYY-MM-DD.txt` and `.json` to `data/logs/`. Red thresholds: store freshness >10%, any abandoned/error run in last 7d, any retail network with no fresh prices in 7d, today's `price_flags` count >3× the 30-day median.
- **Logs moved to `data/logs/`** for everything wired through the new cron lines. Historical logs in `~/g2-dev/logs/` are left in place; `fetch-prices.log` is 121 MB and worth pruning manually.
- **Crontab** now wraps retail and reference with `hc_run.sh` using the pre-existing UUIDs (`48b9dd2d-…`, `09407697-…`). Gas + audit lines are documented in `scripts/crontab.template` and need new UUIDs from the healthchecks.io UI before they can be enabled — the user installs those two lines manually after creating the UUIDs.
- **Audit findings against current DB**: 100 % of stores show stale (>2 d) because the May 7 sweep is still resuming; 11 abandoned runs flagged (the ones cleaned up earlier today). Both are expected and confirm the audit is reading the right signals.

### 2026-05-12 — Pipeline audit + fixes (zombie runs, gas decoupled from retail)

- **Root cause:** product catalog grew from 6,932 → 87,617 items (April 28 reference run), increasing batches/anchor from 35 → 437 (12×). A full sweep now takes ~7–9 cron days. The cron's 23 h `--max-runtime` means May 7 checkpoint has been resuming across multiple days.
- **Zombie run cleanup:** added `abandon_stale_runs(conn, script)` to `db.py`; called at startup in both `fetch_prices.py` and `fetch_gas_prices.py`. Marks any leftover `status='running'` rows (from SIGKILL/unattended-upgrades) as `'abandoned'` before inserting a fresh row. Manually back-filled 11 existing zombies.
- **Gas decoupled from retail in cron:** old line used `&&` so gas only ran if retail exited cleanly (~03:00 next morning). Gas now has its own daily `0 3 * * *` cron line; retail pings healthcheck independently with `;`.
- **Today's cron kill:** unattended-upgrade-shutdown killed the 04:00 cron at 08:04; manual `--resume` restarted at 08:20. May 12 price_date data is being collected in the current run.

### 2026-05-06 — Scenario B data compaction (prune + vacuum)

- **Skipped backfill:** `prices_current` already had 13.5M rows from a prior run.
- **Pruned** `prices` table: 23.6M → 13.5M rows (kept only `MAX(id)` per `product_id, store_id`). Now perfectly mirrors `prices_current`.
- **Vacuumed** DB: 6.3 GB → 4.4 GB (30% reduction). `PRAGMA integrity_check` returned `ok`.
- Change-based dedup is already active in `fetch_prices.py` so future growth should stay bounded (~50–100 MB/week per doc estimates).

---

## Retail

### 2026-05-06 — Store discovery & grid-probe strategy clarification

- **Discovery:** `discover_stores.py` (smart grid-probe) ran April 15 but stalled at 1,367/1,400 completed probes (97% done). Based on 3,180 populated localities CSV, 4km dedup. Checkpoint shows "in_progress" status.
- **Current coverage:** 3,092 stores across 601 UATs (from all discovery methods combined).
- **Strategy clarification:** Three complementary discovery approaches identified:
  1. **Resume smart grid-probe** (30 min runtime) — finish the stalled discover_stores.py, should yield ~4,000–4,500 total stores and ~100 additional UATs. Quick validation of population-based coverage.
  2. **Brute-force rectangular grid** (4+ hours) — full ~30K-point sweep of Romania's bounding box at 5km intervals. Catches stores in unpopulated areas (highways, isolated villages). Run after smart probe to measure coverage delta.
  3. **UAT compilation by name search** (1 min–1 hour depending on scope) — lowest priority; query by county (43 queries) or city (~3K queries) to build complete UAT reference. Valuable for completeness but may be unnecessary if grid-probe provides sufficient coverage for price fetching.
- **Decision:** Start with resuming discover_stores.py, then reassess before committing to brute-force or UAT compilation.

### 2026-05-06 — Unit field normalization + VPS deployment plan

- **Problem identified:** Unit field contains 513 inconsistent variants (Kg/kg/K, BUC/BUCATA, L/Litru, ml/ML, etc.), causing ~15% false-positive price variance. Example: sugar listed as 98.59 lei/L at one store but 2.45 lei/K at others → fake 2101% variance.
- **Solution:** Added `normalize_unit()` function to `db.py`. Maps 513 variants → ~20 canonical forms (kg, pcs, L, ml, g). Integrated into `insert_price()` so all future fetches automatically normalize on insert.
- **Backfill:** Created `backfill_unit_normalization.py` one-off script. Running locally on dev machine (started ~11:27 UTC, processing 12.8M rows in `prices_current`, ETA ~6 hours). Updates existing snapshot table only (not history).
- **Deployment:** Created `docs/VPS_UNIT_NORMALIZATION.md` runbook (like compaction documentation). Process: (1) verify local backfill; (2) git commit + push code; (3) git pull on VPS; (4) run backfill script on VPS (6–12h); (5) verify canonical units in place.
- **Going forward:** All new prices automatically normalized. No manual intervention needed after VPS backfill completes.
- **Impact:** Eliminates false outliers; foundation for accurate price comparison and store optimization modeling.
- **Added to backlog:** "Weekly variability re-analysis" recurring task to track variance patterns and inform store subset optimization.

### 2026-05-06 — Price variability analysis

- **Research question:** Does scraping need to cover 50 stores per UAT per network, or can we optimize? Are prices actually different between stores?
- **Key findings:**
  - **Intra-network variance (same store network, same city):** 76% of products have identical prices across stores; 16% have 5%+ variance
  - **Inter-network variance:** 53% of products have 10%+ price spread across different networks (Kaufland vs Lidl vs Profi) — justifies cross-network comparison
  - **Network-wide variance:** 58% of products are priced nationally identical; 30% have 10%+ regional variance (Bucharest ≠ rural areas)
- **Outlier root cause:** 16% intra-network variance caused by mix of (1) legitimate store format tiering (Express/Market/Hypermarket with +7% premium), (2) fresh produce regional supply variation, and (3) **unit field contamination** (same product ID with mismatched units like "L" vs "K" → 2000%+ false spreads)
- **Implication:** Current approach validated. Could optimize to 2–3 stores per network per UAT (saves 68% of stores, reducing requests 4–8×) without losing insight, but current spatial clustering already provides good efficiency.
- **Action items:** Normalize unit field (Kg/kg/K → canonical); fresh produce regional pricing is real and worth tracking; store format premium is feature data not a bug.
- **Output:** `docs/price_variability_analysis.md` with detailed statistics, outlier examples, and recommendations.

### 2026-05-06 — Ghost filter + per-anchor product filtering

- **Ghost filter:** on every run except the first of each ISO week, products never seen in `prices_current` are skipped (~12,372 / 17% of catalogue). First run of the week still scans all products for new-product discovery. Reduces batch count proportionally for all anchors.
- **Per-anchor product filtering:** each new (not-yet-started) anchor queries `prices_current WHERE store_id IN (nearby stores)` instead of using the full product list. Single-network rural anchors drop from ~375 batches to ~90; urban multi-network anchors see smaller savings. Expected overall speedup: 2–4×.
- `_cluster_anchors` now also returns `anchor_covers` (anchor store_id → all store IDs within 5 km) to support the per-anchor query.
- **Checkpoint compatibility:** `iso_week`, `product_ids` (ghost-filtered global list), and `anchor_batch_counts {store_id: count}` added to checkpoint. Started anchors (present in `done` set) always use the global list for stable resume; only fresh anchors get per-anchor filtering. Old checkpoints handled gracefully: missing `iso_week` triggers full product scan (safe default).

### 2026-05-05 — Pipeline health & price flags quality layer (Phase 1+2+3)

- **Phase 1:** Added `generate_pipeline_report.py` — reads DB post-fetch and writes `docs/pipeline-health.html` with traffic-light indicators for store freshness, run completion, outlier rates, price change velocity, and promo depth sanity.
- **Phase 2:** Added `price_flags` table to `db.py` (`init_db()` idempotent) with `upsert_price_flag()` helper. Added `build_price_flags.py` with three flag types: `outlier_price` (median+MAD modified z-score, threshold 3.0), `price_spike` (>50% day-over-day change), `promo_too_deep` (promo < 20% of product regular avg). First run: 518K `outlier_price` flags out of 21.7M prices (2.4%) — under the 5% investigate threshold; no spike/promo flags (single-date history).
- **Phase 3:** Extended `export_analytics.py` to output `price_flags_summary.csv` and exclude flagged prices from clean-data analytics CSVs. Extended `generate_site.py` with network price comparison, price change tracker, promo effectiveness, and store price index sections.
- Added `build_price_flags.py` to CI workflow (`.github/workflows/`) — runs before `generate_pipeline_report.py` after each daily fetch.
- Decision: used median+MAD (modified z-score) instead of mean+stddev — the standard approach is susceptible to masking when a single extreme outlier skews the distribution.

### 2026-05-02 — Diagnosed 75-hour fetch cycle; fixed cron schedule

**Problem:** healthchecks.io reported "last ping 3 days 7 hours ago". `fetch_prices.py` (PID 152866) had been running since May 1 04:00 UTC — 32+ hours — with ETA of 34 more hours.

**Root cause:** `fetch_reference.py` ran Monday 2026-04-28 and grew the product catalog from ~20K → 86,994 items. This raised batches/anchor from ~100 → 435 (200 products/batch, API hard-limits at 200). At 1.3 s/batch × 480 anchor stores = **~75 hours per full cycle**. The API returns 404 for any request with >200 product IDs in the CSV.

**Secondary cause:** The May 1 run resumed the unfinished April 27 checkpoint (status=in_progress, 192,760 done keys). It used `fetched_at=2026-04-27` metadata for all inserted prices, but `price_date` comes from the API so actual price dates are correct. No data integrity issue.

**Current state at diagnosis:** 93% complete (193K / 208K keys); run finished ~May 2 17:00 UTC. DB is 5.6 GB (backfill populated `prices_current` with 12.3M rows; VACUUM not yet run; 0 freelist pages so VACUUM won't reduce size without deleting rows).

**Fix applied:** Changed cron from `0 4 */2 * *` (every 2 days) to `0 4 * * *` (daily) and added `--max-runtime 82800` (23 hours). The script already has max-runtime support: it saves the checkpoint and exits 0 on timeout, so `&&` proceeds to `fetch_gas_prices.py` and the healthchecks.io ping fires every day. A full 480-anchor cycle now spreads across ~3–4 daily runs; the checkpoint/resume mechanism handles continuity.

**Backlog added:** Per-anchor network-aware product filtering (estimated 2–4× speedup) and ghost product cleanup (12,372 products never return prices — ~17% of catalog).

### 2026-04-28 — DB size optimization: change-based deduplication + price analysis

**Problem:** prices.db grew to 3.66GB in 15 days of VPN-based fetching with ~23M rows. Root cause: the API's `price_date` field (retailer's last update timestamp) increments daily even when prices don't change. The current schema (`UNIQUE(product_id, store_id, price_date)`) creates a new row for every date tick, even for unchanged prices.

**Solution:** Implemented change-based deduplication pattern (Step 2 of 4):
- Added `prices_current` table (UNIQUE on `product_id, store_id`) to hold the current snapshot
- Modified `insert_price()` to check if price+promo have actually changed:
  - If unchanged: only UPDATE `last_checked_at` in prices_current (no changelog row)
  - If changed: INSERT to prices (changelog) + UPSERT prices_current
- Expected row reduction: 5-7× if prices are stable 5+ days/week
- The `prices` table becomes a true changelog; `prices_current` is the denormalized snapshot for fast lookup

**Supporting changes:**
- New `analyze_prices.py` script to analyze price uniformity per (product, network, date) group with ≥3 stores
- Identifies % of groups with uniform pricing vs. variance distribution
- Output: summary stats + `docs/price_uniformity.csv` for drill-down

**Next steps:**
- Backfill `prices_current` from existing prices (one-time migration, deferred)
- Update `fetch_prices.py` to use new insert_price() logic (already compatible)
- Monitor DB size on next fetch cycles; expect sub-1GB for 30+ days if dedup works as planned
- Step 3 (optional): normalize high-cardinality text columns (brand, unit, retail_categ) if size still an issue after Step 2

**Technical notes:**
- DB corruption (101 integrity errors in B-tree) discovered during analysis; recovery attempted but full migration deferred due to SQLite .dump format complexities
- The recovered DB (319-809MB clean vs. 3.4GB corrupted) suggests bloat from transaction journals and invalid index pages
- Corruption does not prevent write operations going forward; insert_price() changes are safe for new data

---

## General

### 2026-04-19 — Phase 1 UI Redesign (editorial homepage + design system)

Complete redesign of the static site toward an editorial data-journalism aesthetic (Datawrapper / old FiveThirtyEight / Pudding ethos). All 18 existing pages preserved; 1 new page added (`tablou.html`).

**What changed:**
- `docs/assets/app.css` — new ~500-line design system: CSS custom property tokens (paper/ink palette, rust accent, 6-hue chart palette), fluid type scale (`clamp()`), Fraunces + IBM Plex Sans + IBM Plex Mono font stacks, spacing grid, and components: `.masthead`, `.nav`, `.lede` (drop cap), `.section-title` (auto-numbered §01–§N), `.stats`/`.stat`, `.chart-block`, `.spread-chart`, `.story-grid`/`.story`, `.tool-grid`/`.tool`, `.strip`, `.disclaimer`, `.footer`.
- `docs/assets/charts.js` — Chart.js 4 defaults: paper palette, no animations, tabular numerals in tooltips, horizontal grid only, legend at bottom.
- `docs/assets/logo.svg` — rust circle + Fraunces wordmark + v2 badge.
- `generate_site.py`: new `NAV_ITEMS` (13 items, 2 separators), `nav_html()`, `page_shell()` (external CSS, skip link, masthead, disclaimer, footer), `_masthead()`, `_disclaimer()`, `_footer()`, `date_ro()` helpers, `FONTS_HEAD` (Google Fonts preconnect). Old `gen_index` renamed to `gen_tablou` (→ `tablou.html`). New `gen_index` produces "Buletinul prețurilor" editorial homepage: lede with spread-chart (no canvas), 4 stat tiles, 3 story cards, 6 tool cards, compact strip.

**Decisions:**
- Stack A kept (Python → static HTML, no bundler), editorial-led positioning chosen. Options doc saved in `docs/design-notes/2026-04-19-ui-redesign-options.md`.
- Google Fonts CDN used for Phase 1 speed; self-hosting deferred to Phase 5.
- Hero chart = CSS-only spread-chart (no Chart.js), so it renders with JS disabled.
- All old URLs preserved; old `gen_index` body lives on as `gen_tablou`.

### 2026-04-18 — API Endpoint Discovery

Wrote `explore_api.py` and probed 50+ candidate endpoints systematically. Strategy: WCF metadata first (WSDL/MEX), then pattern-based candidates, then known-endpoint variations.

Key findings:
- **`GetCatalogProductsByNameNetwork` (no params)** returns 87,448 product names (16.5 MB) — a full catalog dump. No category IDs, but names + IDs enable a client-side search index. Our current pipeline only indexes 6,932 products (monitored categories only).
- **`GetStoresForProductsByUat`** confirmed working — tested Bucharest UAT, returns 50 stores. Supports `csvnetworkids` filter not available on the ByLatLon variant. Currently unused in our pipeline.
- **`GetGasItemsByRoute`** endpoint exists but crashes server-side (AutoMapper bug). Tested with real UAT `route_id` values; the server accepts params but fails during response mapping. Not usable until API owners fix it.
- No price history API exists anywhere. Our SQLite DB is the only historical record.
- WSDL/MEX not exposed; no swagger/help. Manual probing is the only discovery path.
- All other guessed endpoints (store details, brands, promos, history variants) return 404.

Results documented in `docs/reference/undocumented-endpoints.md`. Backlog updated.

### 2026-04-18 — Phase D: Aproape de tine — Geolocation Store Finder

- Added `build_stores_index.py`: emits `docs/data/stores_index.json` (2,624 consumer-network stores with coordinates, compact array format, 319 KB). Joins basket camara per-UAT cheapest cost (national fallback 302.61 lei/lună where UAT not scored). B2B (SELGROS) excluded.
- Added `gen_aproape()` + `aproape.html`: Leaflet map + store card grid. Browser GPS button + **manual lat/lon inputs** (for users outside Romania or with GPS disabled — default pre-filled to Bucharest centre 44.4268, 26.1025). Radius slider (1–50 km), network filter dropdown populated from data. Store cards show distance, network color badge, address, basket cheapest cost for the store's UAT. Map markers colored by network. Cap at 200 displayed results; shows total count above.
- Added "Aproape" to nav. Wired `build_stores_index.py` into CI daily run (runs after basket build).
- **Verified:** 175 stores found within 5 km of Bucharest centre, map + cards render correctly, network color coding works, status message shows manual coordinates.

### 2026-04-18 — Phase C: CPI prototype, Stories, Open Data Hub, Methodology

- **#10 Metodologie & Transparență** (`metodologie.html`): live snapshot grid (products, stores, networks, price rows, dates, gas stats), API endpoint table with limits, known-gaps warning cards (fresh produce absent, 1367 stores with NULL network, 723 products without today's price, 7-day retail history), methodology explanations for each calculator (basket, anomalies, categories, choropleth, price index), code/license card. Data pulled at site-gen time via `load_metodologie_stats()`.
- **#9 Date Deschise** (`date-deschise.html`): 9 downloadable datasets with format badge, file size, freshness, schema description, and direct download link. CC BY 4.0 license. Covers anomalies JSON, 4 basket JSONs, UAT GeoJSON, category index, 3 analytics CSVs.
- **#4 Indice de Inflație Civică — prototype** (`inflatie.html` + `build_cpi.py`): tracks national cheapest-network basket cost per available price_date (7 days); Chart.js multi-line trend per basket; product change table (first vs last date, sorted by abs % change). Heavy "PROTOTIP" labeling + yellow caveat banner + methodology card. Day-to-day swings (e.g. cămară 335→267 lei) reflect coverage variation as much as price changes — caveated explicitly. Wired into CI via `build_cpi.py --db ... --out docs/data/cpi.json`.
- **#8 Povești cu Date — prototype** (`povesti.html`): 5 auto-generated story cards built from today's anomaly + basket + category data (no historical trends needed). Stories: biggest spread today, network cheapest most often (Profi: 77% of compared products), basket savings opportunity (+59.70 lei/lună if choosing wrong network), category with most total spread (Cofetărie: 1,277 lei), products with ≥3× ratio (127 products, 1,738 lei combined savings). All link to relevant pages. Fully client-side, updates daily with data.
- All 4 pages added to nav; `build_cpi.py` added to CI daily run.

### 2026-04-18 — Harta Costurilor — choropleth map (Phase B #2)

- Added `config/geo/ro-uats.topojson` (source file, 706 KB — Romania UAT polygons, 3175 features, SIRUTA join key). 365/366 DB UATs matched by SIRUTA code; all 835 store-UATs matched.
- Added `build_uat_geojson.py`: decodes TopoJSON manually (arc stitching from spec — the `topojson` Python library fails on null-geometry features), joins DB stats (store count, consumer network count — queried directly from stores, not via the uats table which only covers 366/835 UATs), joins basket cheapest/priciest monthly cost from `docs/data/baskets/camara.json` per-UAT data (national fallback for 674/834 UATs not yet scored at local level). Outputs `docs/data/uats.geojson` (834 features, 475 KB, only store-UATs).
- Added `gen_harta()` + `harta.html`: MapLibre GL JS v4 choropleth over CARTO Positron basemap. Three layer toggles: networks present (food-desert detection — red=1 network, green=5+), basket monthly cost (green=cheap, red=expensive), store count (blue scale). Hover highlight + click → side panel with UAT name, network count, store count, basket min/max, cheapest network. Responsive (420px height on mobile).
- KPIs: 398 localities with identified networks, 218 single-network "food deserts", cheapest basket locality = Municipiul Baia Mare 122.26 lei/lună.
- **Verified.** Layer toggle (networks → basket cost) works. Click on "Valea Doftanei": panel shows 1 store, 0 identified networks, national basket fallback 302.61–362.31 lei/lună, Profi cheapest.
- Wired `build_uat_geojson.py` into CI daily run (runs after basket build so baskets data is ready).

### 2026-04-18 — Category Explorer (Phase B #6)

- Added `build_categories.py`: for the latest price_date, groups products by their category (level-2 of the 143-node tree — all 6932 products attach directly there), computes per-category spread rankings using the same outlier filter and B2B exclusion as the anomaly feed, emits `docs/data/categories/index.json` + one JSON per category (up to 200 products each ranked by ratio desc). 7 categories have meaningful multi-network price data today; the other 136 categories have no prices (API tracks shelf-stable goods only — meat, dairy, fresh produce categories are empty).
- Added `gen_categorii()` + `categorii.html` page: category tabs with product counts, KPI summary (comparables count, top spread, total potential savings), network leaderboard (how many products each network prices cheapest in the category — Profi wins 146/200 in Panificatie), product card grid with search + sort (ratio/lei/pct) + min-networks filter, paginated at 24. Each card links to compare.html?pid=X.
- Added "Categorii" to nav (4th position). Wired `build_categories.py` into CI daily run.
- **Verified.** Tab switch (Panificatie → Cafea), search "lavazza" → 9 products, all correctly filtered. Mobile (375px): tabs wrap to 4 lines, KPI cards stack single-column — all readable.

### 2026-04-18 — CI: wire baskets + anomalies builders into daily run

- Added `--db` and `--out` CLI args to `build_baskets.py` and `build_anomalies.py` (both previously hard-coded to `data/prices.db`).
- Added two CI steps after analytics export: `build_baskets.py` and `build_anomalies.py`, both pointed at `data/prices_ci.db`. Daily refresh of `docs/data/baskets/*.json` and `docs/data/anomalies_today.json` is now automatic.
- Updated commit step to git-add the new HTML pages (`cos.html`, `anomalii.html`) and the new JSON outputs. Also added the previously-missing `compare.html`, `analytics.html`, and `docs/data/products/*.csv` to the add list — they were being regenerated in CI but not committed (`git add` was incomplete). Confirmed by running both builders locally on the live DB after the refactor.

### 2026-04-18 — Anomalii de preț — daily cross-network spread feed

- Added `build_anomalies.py`: for the latest `price_date`, computes per-network min price for each product, drops outliers ([0.30, 3.0]× of cross-network median per product — same filter as baskets), keeps products with ratio ≥ 1.5, ranks by ratio desc, writes top 300 to `docs/data/anomalies_today.json` (101 KB). SELGROS excluded (B2B).
- Added `gen_anomalii()` + `anomalii.html` page: KPI summary (count, biggest spread, top-10 potential savings), filters (search, category, cheapest-at network, min ratio threshold), card list with cheapest→priciest flow, savings callout, ratio chip, expandable per-network chip row, link to compare.html?pid=… for each product. Pagination at 30 cards/page.
- Added "Anomalii" to nav, third position after Coșul.
- **Verified.** Top anomaly: Lavazza Qualita Rossa 250g, Kaufland 5.75 lei vs Mega 39.09 lei = 6.8× = +33.34 lei savings. SQL spot-check confirms: Kaufland has the SKU at 5.75 across 11 stores (deep promo), Mega at 39.09 across 256 stores (full price). Real, useful signal — exactly the kind of leak the feed should catch. Outlier filter passed in this case because the median across networks (≈16.67) keeps the 0.30× threshold at 5.0 — promos survive, true data errors don't.
- Mobile (375×812): cards stack, savings line wraps below product info, filters go full-width. Filters tested: search "cafea" → 25 results; min-ratio 3× → 127 results.

### 2026-04-18 — Coșul de Cămară (Phase A: foundations + basket calculator)

- **Foundations.** Added `units.py` (normaliser for messy `prices.unit` strings → 'kg'|'L'|'buc'|None, 99.6% coverage of 2.25M rows) and `networks.py` + `config/networks.json` (short display names + B2B flag for the 10 retail / 7 gas networks; SELGROS flagged B2B and excluded from consumer comparisons). Network IDs in the API are inconsistent (some are slugs like `PROFI`, others are barcodes like `5940475006709` for Carrefour) — the JSON config + `short()` / `is_b2b()` helpers give the rest of the codebase one place to look.
- **Curated baskets.** `config/baskets.json` defines 4 baskets (Cămară 11 items, Student 8, Copt 8, Sărbători 9 — 38 distinct SKUs). Each item lists 1+ substitute `product_ids` so the builder can pick the cheapest available at a given network/UAT. SKUs were filtered to those carried by ≥7 networks today. Honest framing: API only tracks shelf-stable goods, so these are *pantry* baskets (no fresh dairy, meat, produce) — copy and disclaimer say so.
- **Builder.** `build_baskets.py` scores each basket nationally and per UAT: cheapest substitute per item per network → `weekly_cost`, `monthly_cost = weekly × 52/12`. `comparable` flag requires ≥50% items found at the network (protects ranking from missing-data networks). Outlier filter drops prices outside [0.30, 3.0]× cross-network median per product — surfaced after Cora artificially won Cămară due to 3 stores selling 1L Floriol oil at 0.50 lei (data-source error). After filter, PROFI is genuine #1 nationally at 302.61 lei/lună. Outputs `docs/data/baskets/index.json` + 4 per-basket files (~70 KB each, all UATs in one payload, lazy-loaded by tab).
- **UI.** New `cos.html` page with tabbed basket switcher, UAT picker (national or per-locality), hero KPI ("how much extra you'd pay at the priciest network vs the cheapest"), ranked network table with bars and items-found counts, and per-product drill table showing the cheapest price per network with the chosen substitute highlighted. Added "Coșul" as second nav item across the site.
- **Verified.** SQL spot-check reproduced Profi/Cluj-Napoca/Cămară at 160.82 lei/lună exactly (10/11 items found — bread missing in Cluj's PROFI feed). Mobile (375×812) renders cleanly. Tab switch (Cămară → Student) and UAT switch (national → Cluj-Napoca) both work in browser.

### 2026-04-16 — Gas price spread analysis + dashboard fuel trends

- Confirmed gas price variation: premiums vary ~1 RON across networks, benzine ~0.62 RON, GPL only 0.14 RON (smallest). Electric charging has the widest spread (26+ RON). The earlier screenshot showing GPL variation was misleading — based on only 20 UATs.
- Fixed `load_fuel_trends()`: was grouping by raw API timestamp (each station's individual `Updatedate`), now groups by calendar day (`SUBSTR(price_date, 1, 10)`). Trend charts now show one point per day per network.
- Added fuel trend chart to `index.html` dashboard: full-width card below the existing KPIs, fuel type tabs, one line per network, updates daily with CI data.
- Added "Diferență" (spread) column to `fuel.html` table: shows max−min per network inline.
- Added `discover_gas_stations.py`: probes `GetGasItemsByLatLon` from 1842 populated locality centroids (same strategy as `discover_stores.py`). Upserts discovered stations + their UAT IDs; `fetch_gas_prices.py` picks up new UATs automatically. Full run ~73 min; checkpoint/resume. Added `ensure_uat()` helper to `db.py` (`INSERT OR IGNORE`) to avoid clobbering existing UAT data.
- Added cross-link "Hartă Carburanți" to `stores_map.html` top-bar.

### 2026-04-16 — Gas station map + gas pipeline in CI

- Added `gen_gas_map()` and `load_gas_map_data()` to `generate_site.py`: generates `docs/gas_map.html` — Leaflet map of 413 gas stations, markers coloured by network, popup with full price table (all available fuel types + date), network filter legend. Reuses `GAS_COLORS` and `net_color()` already defined.
- Added "Hartă Carburanți" to `NAV_ITEMS` — appears in nav across all generated pages.
- Added gas steps to `.github/workflows/ci_prices.yml`: daily `fetch_gas_prices.py --max-runtime 900` (runs after retail fetch, well within 2h CI limit); weekly `fetch_gas_reference.py` on Mondays. Gas checkpoint and `gas_map.html` added to commit step.
- Added backlog item: highway/road gas station discovery via lat/lon grid probe (city-based coverage now handled by `discover_gas_stations.py`).
- Note: gas coverage limited to 20 UATs from initial setup. Run `python discover_gas_stations.py` (~73 min, checkpoint/resume) to expand to all city-area stations; `fetch_gas_prices.py` picks up newly added UATs automatically. Highway stations require a grid probe (see backlog).

### 2026-04-16 — Analytics page + SQLite views + product CSVs for compare tab

- Added 7 analytical views to `db.py` (created by `init_db()`, idempotent): `v_price_variability`, `v_cross_network_spread`, `v_product_popularity`, `v_private_label_candidates`, `v_stores_per_network`, `v_price_freshness`, `v_products_no_prices`.
- Added `export_analytics.py`: dumps all views to `docs/data/*.csv` (stdlib only); wired into CI after `generate_site.py`; CSVs committed to repo.
- Added `analytics.html`: 7-tab page (one tab per view), client-side sortable columns, row count, per-tab description, CSV download link per tab. Added to nav between Carburanți and Pipeline.
- Compare tab (`compare.html`) loads per-product CSVs from `docs/data/products/{id}.csv` rather than embedding all data as JSON; CSVs generated by `export_analytics.py`. Fixed `.gitignore` — `docs/data/*` was blocking `docs/data/products/*.csv` (subdirectory not un-ignored by `!docs/data/*.csv`); added explicit negation for the subdirectory.
- Added `--products-order` flag to `fetch_prices.py` (`db` | `stale`; default `db`). Stale mode sorts products by oldest `MAX(fetched_at)` ASC, never-fetched first — fills coverage gaps before re-fetching fresh products. Checkpoint saves ordered product IDs in stale mode for stable mid-run resume.

### 2026-04-16 — Trend & Comparison Dashboard (Phase 2)

- Added `trends.html` — time-series line charts: Network Price Index over time (one line per network), category average prices over time (tab per category), fuel placeholder (auto-shows when gas data lands in CI). Graceful degradation when <2 dates available.
- Added `compare.html` — product-level cross-network comparison: dropdown (grouped by category), bar chart of latest prices, trend line chart per network, ranked table with avg/min/max. All data embedded as JSON (224 KB in CI, ~3.7 MB from full DB).
- Both pages added to nav; stores_map hardcoded nav also updated.
- No schema changes — DB already accumulates rows by date (UNIQUE on product+store+date). `git-history` not needed; `prices_ci.db` itself is the history.

### 2026-04-16 — Static GitHub Pages UI (Phase 1)

- Created `generate_site.py`: generates 5 static HTML pages into `docs/` from `data/prices.db`:
  - **index.html** — Dashboard with KPI cards (stores, products, prices, gas stations), Network Price Index bar chart, cheapest fuel summary, latest dates.
  - **price-index.html** — Network Price Index: overall ranking + per-category breakdown with tab selector. Normalized to 100 = cheapest network, computed on products available in 3+ networks.
  - **fuel.html** — Fuel Price Leaderboard: per-fuel-type tabs, horizontal bar chart + sortable table (avg/min/max/stations per network).
  - **pipeline.html** — Pipeline health: KPI cards, coverage-by-network table with % bars, run history from `runs` audit table.
  - **stores_map.html** — Enhanced store map: network filter checkboxes (show/hide per network), visible count display, floating nav bar. Replaces old `generate_map.py` output.
- Design: clean card-based responsive layout, Chart.js for charts, Leaflet + MarkerCluster for map, all data embedded as JSON (~600 KB total, under 2 MB target).
- Supersedes `generate_map.py` (still functional but `generate_site.py` produces the enhanced version).
- Fixed: category query used `parent_id IS NULL` but top-level categories have `parent_id = 1` (virtual root).

### 2026-04-15 — Optimise fetch_prices.py: spatial clustering + larger batches

- Added greedy set-cover spatial clustering to `fetch_prices.py`: groups stores within 5 km and picks one anchor per cluster. Reduces 3,813 stores → 681 anchors (82% fewer API calls).
- Raised `BATCH_SIZE` from 50 to 200 products/request (API-tested; 500 hits URL-length 404). Cuts batches from 139 to 35 per anchor.
- Combined effect: 530k → ~24k requests, ~22h → ~1h (95% reduction).
- Added `--no-cluster` flag as escape hatch to revert to per-store querying.
- Clustering runs in <1s on 3,813 stores (O(n²) with lat/lon pre-filter).
- `INSERT OR IGNORE` on prices means overlapping coverage from neighboring anchors is harmless — no data loss or duplication.

### 2026-04-15 — GitHub Actions CI pipeline + SQL queries

- Added `.github/workflows/ci_prices.yml`: daily cron (05:00 UTC) + manual dispatch; shallow checkout; weekly reference refresh on Mondays; commits `data/prices_ci.db` back to repo.
- Created `build_ci_subset.py`: generates `data/ci_stores.txt` and `data/ci_products.txt` from the DB. Store selection = top 10 per network by population ∪ 50 middle-pop stores spread by Z-order (Romania grid). Product selection = top 50 overall ∪ top 20 per category, both ranked by blended store-coverage + record-count score.
- Extended `fetch_prices.py` with `--store-ids-file` and `--product-ids-file` flags: load a newline-separated ID list and filter stores/products accordingly; mutually exclusive with `--limit-stores`/`--limit-products`.
- Extended `docs/queries.md` with new sections: product popularity (top N overall, top N per category), CI store selection (top-per-network, middle-pop geo batch), data quality checks (store coverage, products with no prices, records per fetch date, stores fetched today).
- Updated `.gitignore` to allow committing `data/prices_ci.db`, `data/ci_stores.txt`, `data/ci_products.txt`.
- Decision: DB committed to repo (not GitHub Artifacts) for simplicity; CI DB is separate from local `data/prices.db` to avoid conflicts.

## Retail

### 2026-04-15 — fetch_prices: --resume flag + generate_map.py

- Added `--resume` flag to `fetch_prices.py`: bypasses the "already completed today" guard while keeping the existing checkpoint's `done` set, so only newly added stores are fetched and old store×batch keys are skipped.
- Added `generate_map.py`: regenerates `docs/stores_map.html` from `data/prices.db` (stores + network JOIN); assigns colors per network; updates legend counts. Run with `python generate_map.py` after any store discovery run.

### 2026-04-15 — per-store price fetching pipeline + stores map

- Rewrote `fetch_prices.py` to iterate individual stores instead of UATs; each store is queried from its own lat/lon, guaranteeing it always appears in results.
- Two ordering modes: `--order population` (surrounding_population DESC, default) and `--order geographic` (Z-order grid ~50 km cells, snake traversal for national spread).
- Added `surrounding_population REAL` column to `stores` table (migration in `db.py`).
- Fixed `upsert_store` in `db.py` to use explicit column names (`INSERT … ON CONFLICT DO UPDATE`) so new columns aren't clobbered on store updates.
- New `update_store_populations.py`: sums locality populations within 10 km radius for each store using `populatie romania siruta coords.csv`; runs in ~4s for 2,773 stores.
- Preserved old UAT-based script as `fetch_prices_by_uat.py`.
- Switched `discover_stores.py` locality source from GeoNames Excel to `populatie romania siruta coords.csv` (3,180 localities, all with coords, zero missing); default `--min-pop` lowered to 2,500 → 1,842 probe points.
- Added static Leaflet map (`docs/stores_map.html`) + CSV export (`docs/stores.csv`) for all discovered stores; markers coloured by network, clustered, popup with name/address.

### 2026-04-15 — discover_stores.py: population-based store discovery

- Rewrote `discover_stores.py` to probe `GetStoresForProductsByLatLon` using lat/lon from `data/reference/geonames-RO.xlsx` (788 Romanian populated places ≥ 5,000 pop), instead of the previous approach that was limited to the 20 UATs already in the DB.
- Deduplication: greedy haversine within 4km radius → 727 probe points; ensures no two adjacent cities trigger the same 5km API buffer twice.
- Checkpoint/resume via `data/discover_stores_checkpoint.json`; safe to interrupt and restart.
- `--dry-run` prints probe points without API calls; `--limit N` for testing; `--debug` for verbose output.
- Confirmed live: 3 probes → 51 new stores; 0 errors.
- Decision: using GeoNames lat/lon directly (no UAT ID matching) keeps the script simple and independent of the UATs table.

### 2026-04-14 — Initial pipeline implementation

- Explored API by reading sample XML responses in `docs/reference/sampleResponses/`
- Created `CLAUDE.md` with project overview, architecture, and API notes
- Implemented `db.py` — SQLite schema + upsert helpers
- Implemented `api.py` — `fetch_xml()` with retry/backoff, XML parsers for all endpoints, `centroid_from_wkt()`
- Implemented `fetch_reference.py` — one-shot pipeline: networks → UATs → categories → products
- Implemented `fetch_prices.py` — daily price pipeline: UAT × product batches
- Fixed invalid XML character entity refs (`&#x1C;` etc.) in product names — API returns these for some categories; added `_strip_invalid_char_refs()` in `api.py`
- Fixed `categ_id` always being `None` — the API doesn't echo category back in product XML; fall back to the queried category ID in `fetch_reference.py`
- Discovered API buffer limit: returns 0 results for `buffer > 5000 m`; corrected plan (was 20 000 m); updated `CLAUDE.md`
- Changed DB path from project root to `data/prices.db`
- Added `--limit` flags to both fetch scripts for fast smoke-testing
- Added tqdm progress bars to both fetch scripts

---

## Gas

### 2026-04-14 — Initial gas pipeline implementation

- Explored gas API endpoints and sample XML responses in `docs/carburanti/reference/`
- Added gas tables to `db.py`: `gas_networks`, `gas_products`, `gas_stations`, `gas_prices`
- Added gas parsers to `api.py`: `parse_gas_networks()`, `parse_gas_products()`, `parse_gas_items()`
- Implemented `fetch_gas_reference.py` — fetches gas networks and fuel product types
- Implemented `fetch_gas_prices.py` — fetches prices per UAT (single request covers all 6 fuel types)
- Gas API is simpler than retail: no batching needed, one request per UAT returns all stations + prices

---

## General

### 2026-04-14 — Checkpoint/resume, last_checked_at, and run logging

- Added `last_checked_at TEXT` column to `prices` and `gas_prices` tables; `init_db()` migrates existing DBs via `ALTER TABLE` with try/except
- Changed `insert_price` and `insert_gas_price` from `INSERT OR IGNORE` to UPSERT: new rows get `fetched_at == last_checked_at`; re-checks update only `last_checked_at`, preserving the original insert timestamp
- Added checkpoint/resume to both price fetch scripts: progress saved to `data/retail_checkpoint.json` / `data/gas_checkpoint.json` after each work unit; `--fresh` flag forces a clean run; checkpoint deleted on clean completion
- Added `runs` table (`script, started_at, finished_at, status, uats_processed, records_written, notes`) to log every pipeline execution
- Added `start_run()` / `finish_run()` helpers in `db.py`; both price scripts wrapped in try/except/finally so status (`completed`, `interrupted`, `error`) is always recorded

### 2026-04-14 — Smarter checkpoint lifecycle (never re-fetch unless `--fresh`)

- On successful completion, checkpoint is now kept with `status: "completed"` instead of being deleted
- Same-day re-runs (e.g. cron re-trigger after a perceived failure) exit immediately — no redundant API calls
- New-day runs detect the date change and start fresh automatically
- Interrupted (`in_progress`) checkpoints always resume regardless of age — supports multi-day rate-limit recovery
- `--fresh` remains the explicit escape hatch to force a clean start
- Backward-compatible: checkpoints without a `status` field are treated as `in_progress`
- Updated `readme.md` to document checkpoint behaviour for both fetch scripts
