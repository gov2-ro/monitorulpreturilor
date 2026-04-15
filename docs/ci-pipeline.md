# CI Pipeline — GitHub Actions

Automated daily price fetching using GitHub Actions free tier.
Runs on a curated subset of stores and products; commits results directly to the repo.

---

## Overview

Two workflows run on a schedule:

| Workflow | File | Schedule | Purpose |
|----------|------|----------|---------|
| **CI price fetch** | `ci_prices.yml` | Daily, 4× per day | Fetch prices for the CI subset |
| **Weekly reference refresh** | `weekly_stores.yml` | Sundays 03:00 UTC | Refresh networks, categories, products |

### Why 4× per day?

GitHub Actions has a **2-hour hard limit per job**. The fetch script stops after **1h45m** (`--max-runtime 6300`), saves a checkpoint, and the next scheduled run picks up from where it left off. With 4 runs per day the pipeline can cover more ground without manual intervention.

The daily runs stop automatically once all CI-subset stores are done for the day (checkpoint status flips to `completed`); subsequent same-day runs are no-ops.

---

## What gets fetched

### Stores (~120)

Selected by `build_ci_subset.py` using two tiers:

- **Tier 1 — top 10 per network by surrounding population** (~70 stores): covers the largest stores of every retail chain (Kaufland, Lidl, Carrefour, Mega Image, Profi, …).
- **Tier 2 — middle-population geographic batch** (~50 stores): stores in the 35th–65th population percentile, spread across Romania by Z-order grid. Ensures smaller cities and rural chains are represented.

IDs stored in `data/ci_stores.txt` (one store_id per line).

### Products (~500)

Selected by `build_ci_subset.py` using a blended rank of store coverage + price record count:

- **Top 50 overall** — most widely stocked products across all stores.
- **Top 20 per category** — broadens selection to include category-specific staples even if they rank lower overall.

The two lists are unioned and deduplicated. IDs stored in `data/ci_products.txt`.

---

## Files committed to the repo

| File | Updated by | Purpose |
|------|-----------|---------|
| `data/prices_ci.db` | Daily fetch | SQLite DB with prices, stores, products |
| `data/prices_ci_checkpoint.json` | Daily fetch | Resume state (in_progress / completed) |
| `data/ci_stores.txt` | Weekly discovery | CI store ID list |
| `data/ci_products.txt` | Weekly discovery | CI product ID list |
| `data/reference/populatie romania siruta coords.csv` | Manual (static) | Locality centroids for store discovery |

All other `data/` files remain gitignored (local full DB, local checkpoints, CSV exports).

---

## Updating the store/product subset

The store and product lists (`ci_stores.txt`, `ci_products.txt`) are **stable by design** — the CI pipeline monitors the same stores consistently so price history is comparable over time. They are NOT regenerated automatically.

Update them manually after a full local fetch when you have good popularity signal:

```bash
python build_ci_subset.py data/prices.db --debug
git add data/ci_stores.txt data/ci_products.txt
git commit -m "chore(ci): refresh store/product subset"
git push
```

Store discovery (`discover_stores.py`) is a full-pipeline operation — running it in CI just to then monitor 120 out of 3,500 stores is wasteful. Discover locally, then re-run `build_ci_subset.py`.

---

## First-time setup

### 1. Allow GitHub Actions to push to the repo

Go to **Settings → Actions → General → Workflow permissions** and select:

> **Read and write permissions**

Without this, the commit step will fail with a 403.

### 2. Commit the reference CSV

The store discovery script needs the locality population CSV. It is a static file (~300 KB) and only needs to be committed once:

```bash
git add -f "data/reference/populatie romania siruta coords.csv"
git commit -m "chore: add locality reference CSV for GH Actions"
git push
```

### 3. Bootstrap the CI database

The CI DB starts empty. Trigger the **weekly store discovery** workflow first (manually via the GitHub UI or `gh` CLI) to populate reference data and discover stores:

```bash
gh workflow run weekly_stores.yml
```

Wait for it to complete (~20–40 min), then trigger the daily fetch:

```bash
gh workflow run ci_prices.yml
```

After the first daily fetch completes, the product subset (`ci_products.txt`) will be populated and subsequent runs will use it automatically.

### 4. Monitor

```bash
gh run list --workflow=ci_prices.yml      # list recent runs
gh run view <run-id> --log                 # stream logs
```

---

## How resume works

The checkpoint file (`data/prices_ci_checkpoint.json`) tracks which `store_id:batch_index` keys have been fetched:

```json
{
  "fetched_at": "2026-04-15T04:00:12+00:00",
  "status": "in_progress",
  "done": ["123:0", "123:1", ..., "456:16"]
}
```

On each run:
- **`in_progress` checkpoint from today** → resumes automatically (skips done keys; already-completed stores are filtered out before the progress bar starts).
- **`completed` checkpoint from today** → exits immediately (no-op).
- **Checkpoint from a previous day** → starts fresh for today.

The workflow never uses `--fresh`, so progress always accumulates within the same day across multiple cron runs.

---

## Relationship to the local full run

| | Local | CI (GitHub Actions) |
|--|-------|---------------------|
| DB file | `data/prices.db` | `data/prices_ci.db` |
| Checkpoint | `data/prices_checkpoint.json` | `data/prices_ci_checkpoint.json` |
| Stores | All 3 467 | ~120 curated |
| Products | All 6 932 | ~500 curated |
| Runtime | Days | ~30–60 min/day total |

The two pipelines are fully independent and share no state.

---

## Regenerating the CI subset

After a full local fetch, run `build_ci_subset.py` against the full DB to get better-ranked store/product lists, then commit the updated text files:

```bash
python build_ci_subset.py data/prices.db --debug
git add data/ci_stores.txt data/ci_products.txt
git commit -m "chore(ci): refresh store/product subset"
git push
```

The weekly workflow regenerates these from the CI DB (smaller signal, but good enough for week-to-week drift).

---

## Manually running a subset

```bash
# Fetch prices for the CI subset locally (no time limit)
python fetch_prices.py data/prices_ci.db \
  --store-ids-file data/ci_stores.txt \
  --product-ids-file data/ci_products.txt

# Rebuild subset files from local full DB
python build_ci_subset.py data/prices.db --debug

# Regenerate the store subset without touching products
python build_ci_subset.py data/prices.db --top-overall 0 --top-per-cat 0
```
