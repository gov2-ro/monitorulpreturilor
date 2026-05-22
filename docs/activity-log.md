# Activity Log

---

## General

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
