# Pipeline Health & Data Quality Layer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a pipeline diagnostic report (Phase 1), a persistent price_flags quality layer (Phase 2), and clean-data public insights (Phase 3).

**Architecture:** Three sequenced phases. Phase 1 is read-only and produces `docs/pipeline-health.html`. Phase 2 adds a `price_flags` table to `data/prices.db` and a `build_price_flags.py` script. Phase 3 extends `generate_site.py` / `export_analytics.py` to exclude flagged records and adds new insight sections.

**Tech Stack:** Python 3.12, SQLite 3 (stdlib), no external deps. Tests use pytest with an in-memory DB. HTML is self-contained (inline CSS, no external assets).

---

## File Map

| File | Action | Phase |
|------|--------|-------|
| `generate_pipeline_report.py` | Create | 1 |
| `tests/test_pipeline_report.py` | Create | 1 |
| `db.py` | Modify — add `price_flags` table + `upsert_price_flag()` | 2 |
| `build_price_flags.py` | Create | 2 |
| `tests/test_price_flags.py` | Create | 2 |
| `generate_site.py` | Modify — add clean-data filter + new insight loaders | 3 |
| `export_analytics.py` | Modify — add `price_flags.csv` export | 3 |
| `.github/workflows/ci_prices.yml` | Modify — add report steps + commit new files | 1+2+3 |

---

## Task 1: Pipeline Diagnostic Report (`generate_pipeline_report.py`)

**Files:**
- Create: `generate_pipeline_report.py`
- Create: `tests/__init__.py`
- Create: `tests/test_pipeline_report.py`

### Background

`prices_current` holds one row per (product_id, store_id) with the latest snapshot including `last_checked_at`. `runs` holds one row per fetch execution. The report reads these without modifying the DB.

The HTML pattern follows `generate_site.py`: a single Python string, self-contained with inline CSS, written to `docs/`.

- [ ] **Step 1.1: Write failing tests**

Create `tests/__init__.py` (empty) and `tests/test_pipeline_report.py`:

```python
# tests/test_pipeline_report.py
import sys, math
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import sqlite3
from db import init_db


@pytest.fixture
def db():
    conn = init_db(":memory:")
    conn.execute("INSERT INTO retail_networks VALUES ('N1','PROFI',NULL)")
    conn.execute("INSERT INTO uats VALUES (1,'Cluj',NULL,NULL,46.7,23.6)")
    conn.execute("INSERT INTO stores VALUES (1,'Profi Cluj','Str 1',46.7,23.6,1,'N1','400000')")
    conn.execute("INSERT INTO stores VALUES (2,'Profi Dej','Str 2',47.1,23.9,1,'N1','405300')")
    conn.execute("INSERT INTO categories VALUES (10,'Lactate',1,NULL,'api')")
    conn.execute("INSERT INTO products VALUES (1,'Lapte 1L',10)")
    conn.execute("INSERT INTO products VALUES (2,'Unt 200g',10)")
    # Store 1: fresh (last_checked_at = today)
    conn.execute("INSERT INTO prices_current VALUES (1,1,10.0,'2026-04-30',NULL,NULL,'L',NULL,NULL,'2026-04-25','2026-04-30')")
    # Store 2: stale (last_checked_at = 4 days ago)
    conn.execute("INSERT INTO prices_current VALUES (1,2,10.5,'2026-04-26',NULL,NULL,'L',NULL,NULL,'2026-04-20','2026-04-26')")
    # Price history for velocity: product 1, store 1 changed on 2026-04-30
    conn.execute("INSERT INTO prices (product_id,store_id,price,price_date,promo,brand,unit,retail_categ_id,retail_categ_name,fetched_at) VALUES (1,1,10.0,'2026-04-30',NULL,NULL,'L',NULL,NULL,'2026-04-30T04:00:00')")
    conn.execute("INSERT INTO prices (product_id,store_id,price,price_date,promo,brand,unit,retail_categ_id,retail_categ_name,fetched_at) VALUES (1,1,9.5,'2026-04-29',NULL,NULL,'L',NULL,NULL,'2026-04-29T04:00:00')")
    # Outlier: product 2, store 1 has abnormally high price
    conn.execute("INSERT INTO prices_current VALUES (2,1,999.0,'2026-04-30',NULL,NULL,'BUC',NULL,NULL,'2026-04-25','2026-04-30')")
    conn.execute("INSERT INTO prices_current VALUES (2,2,5.0,'2026-04-30',NULL,NULL,'BUC',NULL,NULL,'2026-04-25','2026-04-30')")
    # Promo sanity: product 1 store 2 has suspiciously deep promo (0.10 lei for 10 lei product)
    conn.execute("INSERT INTO prices_current VALUES (1,2,0.10,'2026-04-30','PROMO',NULL,'L',NULL,NULL,'2026-04-25','2026-04-30') ON CONFLICT(product_id,store_id) DO UPDATE SET price=0.10, promo='PROMO'")
    # Runs
    conn.execute("INSERT INTO runs VALUES (1,'fetch_prices.py','2026-04-30T04:00:00','2026-04-30T05:10:00','completed',300,50000,NULL)")
    conn.execute("INSERT INTO runs VALUES (2,'fetch_prices.py','2026-04-29T04:00:00',NULL,'interrupted',100,15000,NULL)")
    conn.commit()
    return conn


def test_store_freshness_detects_stale(db):
    from generate_pipeline_report import load_store_freshness
    rows = load_store_freshness(db, as_of_date="2026-05-05")
    stale = [r for r in rows if r["stale"]]
    assert len(stale) >= 1
    assert any(r["store_id"] == 2 for r in stale)


def test_store_freshness_fresh_store_not_stale(db):
    from generate_pipeline_report import load_store_freshness
    rows = load_store_freshness(db, as_of_date="2026-04-30")
    stale = [r for r in rows if r["stale"] and r["store_id"] == 1]
    assert len(stale) == 0


def test_run_stats_counts_completion(db):
    from generate_pipeline_report import load_run_stats
    stats = load_run_stats(db)
    assert len(stats) == 2
    completed = [r for r in stats if r["status"] == "completed"]
    assert len(completed) == 1


def test_outlier_summary_detects_outlier(db):
    from generate_pipeline_report import compute_outlier_summary
    summary = compute_outlier_summary(db)
    # product 2: price 999 vs avg ~502 — the 999 should be flagged
    assert summary["flagged_count"] >= 1
    assert summary["flagged_pct"] > 0


def test_price_velocity_computes_change(db):
    from generate_pipeline_report import compute_price_velocity
    vel = compute_price_velocity(db)
    assert vel["curr_date"] == "2026-04-30"
    assert vel["prev_date"] == "2026-04-29"
    assert vel["changed_count"] >= 1


def test_promo_sanity_catches_deep_promo(db):
    from generate_pipeline_report import load_promo_sanity_issues
    issues = load_promo_sanity_issues(db)
    assert len(issues) >= 1
    pids = [r["product_id"] for r in issues]
    assert 1 in pids  # product 1 has 0.10 promo vs 10.0 median


def test_render_html_returns_string(db):
    from generate_pipeline_report import build_report_data, render_html
    data = build_report_data(db, as_of_date="2026-05-05")
    html = render_html(data)
    assert "<html" in html
    assert "pipeline" in html.lower()
```

- [ ] **Step 1.2: Run tests to confirm they all fail**

```bash
cd /Users/pax/devbox/gov2/monitorulpreturilor
source ~/devbox/envs/240826/bin/activate
pytest tests/test_pipeline_report.py -v 2>&1 | head -40
```

Expected: `ModuleNotFoundError: No module named 'generate_pipeline_report'`

- [ ] **Step 1.3: Implement `generate_pipeline_report.py`**

```python
#!/usr/bin/env python3
"""Generate docs/pipeline-health.html — pipeline diagnostic report.

Reads the DB and emits a self-contained HTML report with traffic-light
indicators for store freshness, run completion, price outliers,
price change velocity, and promo sanity.

Usage:
    python generate_pipeline_report.py
    python generate_pipeline_report.py --db data/prices.db --out docs/pipeline-health.html
"""

import argparse
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "prices.db"
DEFAULT_OUT = ROOT / "docs" / "pipeline-health.html"

STALE_DAYS = 2
OUTLIER_Z = 3.0
SPIKE_PCT = 50.0
PROMO_DEPTH_PCT = 20.0  # promo price < 20% of median regular = suspicious


# ── Data loaders ─────────────────────────────────────────────────────────────

def load_store_freshness(conn, stale_days=STALE_DAYS, as_of_date=None):
    """Return list of {store_id, name, network, last_date, days_stale, stale}."""
    if as_of_date is None:
        as_of_date = datetime.now(timezone.utc).date().isoformat()
    rows = conn.execute("""
        SELECT s.id AS store_id, s.name, COALESCE(n.name,'Unknown') AS network,
               MAX(pc.last_checked_at) AS last_date
        FROM prices_current pc
        JOIN stores s ON pc.store_id = s.id
        LEFT JOIN retail_networks n ON s.network_id = n.id
        GROUP BY s.id
    """).fetchall()
    out = []
    for store_id, name, network, last_date in rows:
        last_day = (last_date or "")[:10]
        if last_day:
            from datetime import date
            d0 = date.fromisoformat(last_day)
            d1 = date.fromisoformat(as_of_date[:10])
            days = (d1 - d0).days
        else:
            days = 9999
        out.append({
            "store_id": store_id,
            "name": name,
            "network": network,
            "last_date": last_day,
            "days_stale": days,
            "stale": days > stale_days,
        })
    out.sort(key=lambda r: (-r["days_stale"], r["name"]))
    return out


def load_run_stats(conn, limit=15):
    """Return list of recent run rows."""
    rows = conn.execute("""
        SELECT id, script, started_at, finished_at, status,
               uats_processed, records_written, notes
        FROM runs ORDER BY id DESC LIMIT ?
    """, (limit,)).fetchall()
    cols = ["id","script","started_at","finished_at","status",
            "uats_processed","records_written","notes"]
    return [dict(zip(cols, r)) for r in rows]


def compute_outlier_summary(conn, z_threshold=OUTLIER_Z):
    """Return {total_records, flagged_count, flagged_pct, top_products}.

    Uses mean + variance from prices_current (no SQRT needed in SQL):
    condition: (price - avg)^2 > z^2 * variance
    """
    # Per-product stats
    stats_rows = conn.execute("""
        SELECT product_id,
               COUNT(*) AS n,
               AVG(price) AS avg_p,
               AVG(price * price) - AVG(price) * AVG(price) AS var_p
        FROM prices_current
        WHERE price > 0
        GROUP BY product_id
        HAVING n >= 3 AND (AVG(price * price) - AVG(price) * AVG(price)) > 0.001
    """).fetchall()

    total = conn.execute("SELECT COUNT(*) FROM prices_current WHERE price > 0").fetchone()[0]

    outlier_pids = set()
    for pid, n, avg, var in stats_rows:
        if var > 0:
            # Check via pre-computed bounds; actual per-record check happens in Phase 2
            stdev = math.sqrt(var)
            # Flag product if its range spans more than z*stdev
            hi = avg + z_threshold * stdev
            lo = avg - z_threshold * stdev
            # Quick check: get min/max for this product
            mi, ma = conn.execute(
                "SELECT MIN(price), MAX(price) FROM prices_current WHERE product_id=? AND price>0",
                (pid,)
            ).fetchone()
            if mi < lo or ma > hi:
                outlier_pids.add((pid, lo, hi, avg))

    # Count actual flagged records
    flagged = 0
    top_products = []
    for pid, lo, hi, avg in list(outlier_pids)[:500]:  # cap to avoid slow loop
        cnt = conn.execute(
            "SELECT COUNT(*) FROM prices_current WHERE product_id=? AND price>0 AND (price<?  OR price>?)",
            (pid, lo, hi)
        ).fetchone()[0]
        if cnt:
            name = conn.execute("SELECT name FROM products WHERE id=?", (pid,)).fetchone()
            name = name[0] if name else f"#{pid}"
            flagged += cnt
            top_products.append({"pid": pid, "name": name, "outlier_count": cnt, "avg": round(avg, 2)})

    top_products.sort(key=lambda r: -r["outlier_count"])
    flagged_pct = round(100 * flagged / total, 2) if total else 0
    return {
        "total_records": total,
        "flagged_count": flagged,
        "flagged_pct": flagged_pct,
        "top_products": top_products[:20],
    }


def compute_price_velocity(conn):
    """Return {prev_date, curr_date, changed_count, total_current, changed_pct}."""
    dates = conn.execute(
        "SELECT DISTINCT price_date FROM prices WHERE price_date IS NOT NULL ORDER BY price_date DESC LIMIT 2"
    ).fetchall()
    if len(dates) < 2:
        return {"prev_date": None, "curr_date": None, "changed_count": 0,
                "total_current": 0, "changed_pct": 0.0, "status": "insufficient_data"}
    curr_date, prev_date = dates[0][0], dates[1][0]
    changed = conn.execute(
        "SELECT COUNT(*) FROM prices WHERE price_date=?", (curr_date,)
    ).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM prices_current").fetchone()[0]
    pct = round(100 * changed / total, 2) if total else 0
    status = "stuck" if pct == 0 else ("bulk_change" if pct > 30 else "ok")
    return {
        "curr_date": curr_date,
        "prev_date": prev_date,
        "changed_count": changed,
        "total_current": total,
        "changed_pct": pct,
        "status": status,
    }


def load_promo_sanity_issues(conn, depth_pct=PROMO_DEPTH_PCT):
    """Return list of promos whose price < depth_pct% of the product's median regular price."""
    threshold = depth_pct / 100.0
    rows = conn.execute("""
        WITH regular_avg AS (
          SELECT product_id, AVG(price) AS reg_avg
          FROM prices_current
          WHERE promo IS NULL AND price > 0
          GROUP BY product_id
        )
        SELECT pc.product_id, pr.name AS product,
               pc.store_id, s.name AS store,
               pc.price AS promo_price,
               ra.reg_avg AS regular_avg,
               ROUND(pc.price / ra.reg_avg * 100, 1) AS pct_of_regular
        FROM prices_current pc
        JOIN regular_avg ra ON pc.product_id = ra.product_id
        JOIN products pr ON pc.product_id = pr.id
        JOIN stores s ON pc.store_id = s.id
        WHERE pc.promo IS NOT NULL
          AND pc.price > 0
          AND ra.reg_avg > 0
          AND pc.price < ra.reg_avg * ?
        ORDER BY pct_of_regular
        LIMIT 50
    """, (threshold,)).fetchall()
    cols = ["product_id","product","store_id","store","promo_price","regular_avg","pct_of_regular"]
    return [dict(zip(cols, r)) for r in rows]


def build_report_data(conn, as_of_date=None):
    """Collect all report metrics into a single dict."""
    if as_of_date is None:
        as_of_date = datetime.now(timezone.utc).isoformat()
    return {
        "generated_at": as_of_date,
        "store_freshness": load_store_freshness(conn, as_of_date=as_of_date),
        "run_stats": load_run_stats(conn),
        "outlier_summary": compute_outlier_summary(conn),
        "price_velocity": compute_price_velocity(conn),
        "promo_issues": load_promo_sanity_issues(conn),
    }


# ── HTML rendering ────────────────────────────────────────────────────────────

def _rag(condition_green, condition_red, label_green, label_amber, label_red):
    """Return (css_class, label) traffic-light tuple."""
    if condition_green:
        return "rag-green", label_green
    if condition_red:
        return "rag-red", label_red
    return "rag-amber", label_amber


def render_html(data):
    freshness = data["store_freshness"]
    stale_count = sum(1 for r in freshness if r["stale"])
    stale_pct = round(100 * stale_count / len(freshness), 1) if freshness else 0

    runs = data["run_stats"]
    outlier = data["outlier_summary"]
    velocity = data["price_velocity"]
    promos = data["promo_issues"]

    rag_fresh = _rag(stale_pct == 0, stale_pct > 10,
                     "All stores fresh", f"{stale_pct}% stale — monitor", f"{stale_pct}% stale — investigate")
    rag_outlier = _rag(outlier["flagged_pct"] < 1, outlier["flagged_pct"] > 5,
                       "Outlier rate nominal", "Some outliers — review", "High outlier rate")
    rag_vel = _rag(velocity["status"] == "ok", velocity["status"] in ("stuck","insufficient_data"),
                   f"{velocity['changed_pct']}% changed — normal",
                   f"{velocity['changed_pct']}% changed — watch",
                   velocity.get("status",""))
    rag_promo = _rag(len(promos) == 0, len(promos) > 10,
                     "Promo depths OK", f"{len(promos)} suspicious promos", f"{len(promos)} suspicious promos — high")

    def rag_badge(rag):
        css, label = rag
        return f'<span class="{css}">{label}</span>'

    def rows_table(cols, rows, row_fn):
        header = "".join(f"<th>{c}</th>" for c in cols)
        body = "".join(f"<tr>{row_fn(r)}</tr>" for r in rows)
        return f"<table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>"

    freshness_table = rows_table(
        ["Store", "Network", "Last seen", "Days stale", "Status"],
        freshness[:50],
        lambda r: (
            f"<td>{r['name']}</td><td>{r['network']}</td>"
            f"<td>{r['last_date']}</td><td>{r['days_stale']}</td>"
            f"<td>{'🔴 Stale' if r['stale'] else '🟢 OK'}</td>"
        )
    )

    run_rows = runs[:10]
    run_table = rows_table(
        ["ID", "Script", "Started", "Status", "UATs", "Records"],
        run_rows,
        lambda r: (
            f"<td>{r['id']}</td><td>{r['script']}</td><td>{r['started_at'][:16]}</td>"
            f"<td>{r['status']}</td><td>{r['uats_processed'] or '—'}</td>"
            f"<td>{r['records_written'] or '—'}</td>"
        )
    )

    outlier_rows = outlier["top_products"][:20]
    outlier_table = rows_table(
        ["Product", "Outlier records", "Avg price"],
        outlier_rows,
        lambda r: f"<td>{r['name']}</td><td>{r['outlier_count']}</td><td>{r['avg']}</td>"
    )

    promo_table = rows_table(
        ["Product", "Store", "Promo price", "Regular avg", "% of regular"],
        promos[:20],
        lambda r: (
            f"<td>{r['product']}</td><td>{r['store']}</td>"
            f"<td>{r['promo_price']}</td><td>{round(r['regular_avg'],2)}</td>"
            f"<td>{r['pct_of_regular']}%</td>"
        )
    )

    return f"""<!DOCTYPE html>
<html lang="ro">
<head>
<meta charset="utf-8">
<title>Pipeline Health Report</title>
<style>
  body{{font-family:system-ui,sans-serif;margin:2rem;color:#1e293b;background:#f8fafc}}
  h1{{font-size:1.5rem;margin-bottom:.25rem}}
  .ts{{color:#64748b;font-size:.85rem;margin-bottom:2rem}}
  .grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:1rem;margin-bottom:2rem}}
  .card{{background:#fff;border:1px solid #e2e8f0;border-radius:.5rem;padding:1rem}}
  .card h3{{margin:0 0 .5rem;font-size:.85rem;color:#64748b;text-transform:uppercase}}
  .rag-green{{background:#dcfce7;color:#166534;padding:.25rem .75rem;border-radius:9999px;font-size:.85rem;font-weight:600}}
  .rag-amber{{background:#fef3c7;color:#92400e;padding:.25rem .75rem;border-radius:9999px;font-size:.85rem;font-weight:600}}
  .rag-red  {{background:#fee2e2;color:#991b1b;padding:.25rem .75rem;border-radius:9999px;font-size:.85rem;font-weight:600}}
  h2{{margin-top:2rem;font-size:1.1rem;border-bottom:1px solid #e2e8f0;padding-bottom:.5rem}}
  table{{width:100%;border-collapse:collapse;font-size:.85rem;background:#fff;border-radius:.5rem;overflow:hidden;border:1px solid #e2e8f0}}
  th{{background:#f1f5f9;text-align:left;padding:.5rem .75rem;font-weight:600}}
  td{{padding:.4rem .75rem;border-top:1px solid #f1f5f9}}
  tr:hover td{{background:#f8fafc}}
</style>
</head>
<body>
<h1>Pipeline Health Report</h1>
<p class="ts">Generated: {data['generated_at']}</p>

<div class="grid">
  <div class="card"><h3>Store Freshness</h3>{rag_badge(rag_fresh)}<br><small>{stale_count} of {len(freshness)} stores stale</small></div>
  <div class="card"><h3>Price Outliers</h3>{rag_badge(rag_outlier)}<br><small>{outlier['flagged_count']} records ({outlier['flagged_pct']}%)</small></div>
  <div class="card"><h3>Price Velocity</h3>{rag_badge(rag_vel)}<br><small>{velocity.get('changed_count',0)} changes on {velocity.get('curr_date','—')}</small></div>
  <div class="card"><h3>Promo Sanity</h3>{rag_badge(rag_promo)}<br><small>{len(promos)} deep promo anomalies</small></div>
</div>

<h2>Run History (last {len(run_rows)} runs)</h2>
{run_table}

<h2>Store Freshness — Top 50 by staleness</h2>
{freshness_table}

<h2>Top Outlier Products ({len(outlier_rows)} shown)</h2>
{outlier_table}

<h2>Deep Promo Anomalies</h2>
{promo_table}
</body></html>"""


# ── Entry point ───────────────────────────────────────────────────────────────

def main(db_path=DEFAULT_DB, out_path=DEFAULT_OUT, as_of_date=None):
    print(f"Generating pipeline report from {db_path}")
    conn = sqlite3.connect(str(db_path))
    data = build_report_data(conn, as_of_date=as_of_date)
    conn.close()
    html = render_html(data)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"  Written {out} ({len(html)//1024} KB)")
    v = data["price_velocity"]
    s = data["outlier_summary"]
    stale = sum(1 for r in data["store_freshness"] if r["stale"])
    print(f"  Stores stale: {stale} | Outlier records: {s['flagged_count']} ({s['flagged_pct']}%) | Velocity: {v['changed_pct']}%")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--date", default=None, help="as_of_date for freshness calc (YYYY-MM-DD)")
    args = ap.parse_args()
    main(args.db, args.out, args.date)
```

- [ ] **Step 1.4: Run tests — expect pass**

```bash
source ~/devbox/envs/240826/bin/activate
pytest tests/test_pipeline_report.py -v
```

Expected: all 7 tests PASS

- [ ] **Step 1.5: Smoke test against real DB**

```bash
source ~/devbox/envs/240826/bin/activate
python generate_pipeline_report.py
```

Expected output like:
```
Generating pipeline report from data/prices.db
  Written docs/pipeline-health.html (XX KB)
  Stores stale: N | Outlier records: N (N%) | Velocity: N%
```

Open `docs/pipeline-health.html` in browser (or `npx playwright`) and confirm the four summary cards render with colours and the tables are populated.

- [ ] **Step 1.6: Commit**

```bash
git add generate_pipeline_report.py tests/__init__.py tests/test_pipeline_report.py docs/pipeline-health.html
git commit -m "feat: add pipeline diagnostic report (Phase 1)"
```

---

## Task 2: Add `price_flags` to `db.py`

**Files:**
- Modify: `db.py`

- [ ] **Step 2.1: Write failing test**

Add to `tests/test_pipeline_report.py` (or create `tests/test_db.py`):

```python
# tests/test_db.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db import init_db, upsert_price_flag
import json


def test_price_flags_table_created():
    conn = init_db(":memory:")
    # Table must exist
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='price_flags'"
    ).fetchone()
    assert exists is not None


def test_upsert_price_flag_inserts():
    conn = init_db(":memory:")
    upsert_price_flag(conn, 1, 1, "2026-04-30", "outlier_price",
                      {"price": 999.0, "mean": 10.0, "z_score": 8.5})
    conn.commit()
    row = conn.execute("SELECT flag_type, details FROM price_flags").fetchone()
    assert row[0] == "outlier_price"
    d = json.loads(row[1])
    assert d["z_score"] == 8.5


def test_upsert_price_flag_idempotent():
    conn = init_db(":memory:")
    upsert_price_flag(conn, 1, 1, "2026-04-30", "outlier_price", {"price": 999.0})
    upsert_price_flag(conn, 1, 1, "2026-04-30", "outlier_price", {"price": 999.0})
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM price_flags").fetchone()[0]
    assert count == 1
```

- [ ] **Step 2.2: Run tests — expect fail**

```bash
pytest tests/test_db.py -v
```

Expected: `FAILED` — `price_flags` table missing, `upsert_price_flag` not defined.

- [ ] **Step 2.3: Add `price_flags` table and `upsert_price_flag` to `db.py`**

In `db.py`, add to the `executescript` block inside `init_db()` (after the `runs` table DDL, before `conn.commit()`):

```python
    CREATE TABLE IF NOT EXISTS price_flags (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id  INTEGER NOT NULL,
        store_id    INTEGER NOT NULL,
        price_date  TEXT NOT NULL,
        flag_type   TEXT NOT NULL,
        details     TEXT,
        created_at  TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(product_id, store_id, price_date, flag_type)
    );
    CREATE INDEX IF NOT EXISTS idx_price_flags_lookup
        ON price_flags(product_id, store_id, price_date);
```

Then add the helper function at the end of `db.py`:

```python
def upsert_price_flag(conn, product_id, store_id, price_date, flag_type, details=None):
    """Insert a price flag, ignoring duplicates (same product/store/date/type)."""
    import json as _json
    conn.execute(
        """INSERT OR IGNORE INTO price_flags
           (product_id, store_id, price_date, flag_type, details)
           VALUES (?,?,?,?,?)""",
        (product_id, store_id, price_date, flag_type,
         _json.dumps(details) if details is not None else None),
    )
```

- [ ] **Step 2.4: Run tests — expect pass**

```bash
pytest tests/test_db.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 2.5: Commit**

```bash
git add db.py tests/test_db.py
git commit -m "feat: add price_flags table and upsert helper to db.py"
```

---

## Task 3: Build `build_price_flags.py` (Phase 2)

**Files:**
- Create: `build_price_flags.py`
- Create: `tests/test_price_flags.py`

### Background

Three flag types:
- `outlier_price`: price deviates > 3 modified-z from the per-product **median** in `prices_current`. Uses median+MAD (modified z-score: `0.6745 * |price - median| / MAD > z_threshold`). Details keys: `{"price", "median", "mad", "z_score"}`. **Note:** mean+stdev was originally spec'd but has a masking-effect bug on small N (e.g. 3 stores: [10, 10.2, 999] → mean=340, z(999)=1.4, outlier undetected). Median+MAD is the standard fix.
- `price_spike`: price changed > 50% vs the most recent prior price for the same (product, store) in `prices` history. Compares last two distinct dates. Details keys: `{"curr_price", "prev_price", "pct_change", "curr_date", "prev_date"}`.
- `promo_too_deep`: promo price < 20% of the product's average regular price in `prices_current`. Details keys: `{"promo_price", "regular_avg", "pct_of_regular"}`.

Phase 2 writes individual records to `price_flags`. Phase 1's `compute_outlier_summary()` was an estimate; this is the authoritative record.

- [ ] **Step 3.1: Write failing tests**

```python
# tests/test_price_flags.py
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from db import init_db


@pytest.fixture
def db():
    conn = init_db(":memory:")
    conn.execute("INSERT INTO retail_networks VALUES ('N1','PROFI',NULL)")
    conn.execute("INSERT INTO stores VALUES (1,'Profi Cluj','Str 1',46.7,23.6,1,'N1','400000')")
    conn.execute("INSERT INTO stores VALUES (2,'Profi Dej','Str 2',47.1,23.9,1,'N1','400100')")
    conn.execute("INSERT INTO stores VALUES (3,'Profi Turda','Str 3',46.5,23.7,1,'N1','400200')")
    conn.execute("INSERT INTO categories VALUES (10,'Lactate',1,NULL,'api')")
    conn.execute("INSERT INTO products VALUES (1,'Lapte 1L',10)")
    conn.execute("INSERT INTO products VALUES (2,'Unt 200g',10)")
    # Product 1: prices mostly 10 lei, one outlier at 999 lei
    conn.execute("INSERT INTO prices_current VALUES (1,1,10.0,'2026-04-30',NULL,NULL,'L',NULL,NULL,'2026-04-25','2026-04-30')")
    conn.execute("INSERT INTO prices_current VALUES (1,2,10.2,'2026-04-30',NULL,NULL,'L',NULL,NULL,'2026-04-25','2026-04-30')")
    conn.execute("INSERT INTO prices_current VALUES (1,3,999.0,'2026-04-30',NULL,NULL,'L',NULL,NULL,'2026-04-25','2026-04-30')")
    # Product 2: normal prices 5 lei, promo at 0.50 (too deep)
    conn.execute("INSERT INTO prices_current VALUES (2,1,5.0,'2026-04-30',NULL,NULL,'BUC',NULL,NULL,'2026-04-25','2026-04-30')")
    conn.execute("INSERT INTO prices_current VALUES (2,2,5.1,'2026-04-30',NULL,NULL,'BUC',NULL,NULL,'2026-04-25','2026-04-30')")
    conn.execute("INSERT INTO prices_current VALUES (2,3,0.50,'2026-04-30','PROMO',NULL,'BUC',NULL,NULL,'2026-04-25','2026-04-30')")
    # Price history for spike detection: product 1 store 1 jumped 80%
    conn.execute("INSERT INTO prices (product_id,store_id,price,price_date,promo,brand,unit,retail_categ_id,retail_categ_name,fetched_at) VALUES (1,1,10.0,'2026-04-30',NULL,NULL,'L',NULL,NULL,'2026-04-30')")
    conn.execute("INSERT INTO prices (product_id,store_id,price,price_date,promo,brand,unit,retail_categ_id,retail_categ_name,fetched_at) VALUES (1,1,5.5,'2026-04-29',NULL,NULL,'L',NULL,NULL,'2026-04-29')")
    conn.commit()
    return conn


def test_flag_outlier_prices(db):
    from build_price_flags import flag_outlier_prices
    count = flag_outlier_prices(db)
    db.commit()
    assert count >= 1
    row = db.execute(
        "SELECT flag_type, details FROM price_flags WHERE product_id=1 AND store_id=3"
    ).fetchone()
    assert row is not None
    assert row[0] == "outlier_price"
    d = json.loads(row[1])
    assert d["price"] == 999.0
    assert "z_score" in d


def test_flag_outlier_no_false_positive(db):
    from build_price_flags import flag_outlier_prices
    flag_outlier_prices(db)
    db.commit()
    # Product 1, store 1 (price=10.0) should NOT be flagged
    row = db.execute(
        "SELECT * FROM price_flags WHERE product_id=1 AND store_id=1 AND flag_type='outlier_price'"
    ).fetchone()
    assert row is None


def test_flag_price_spikes(db):
    from build_price_flags import flag_price_spikes
    count = flag_price_spikes(db)
    db.commit()
    assert count >= 1  # product 1 store 1: 5.5→10.0 = +81.8%
    row = db.execute(
        "SELECT flag_type, details FROM price_flags WHERE product_id=1 AND store_id=1"
    ).fetchone()
    assert row is not None
    assert row[0] == "price_spike"
    d = json.loads(row[1])
    assert d["pct_change"] > 50


def test_flag_promo_too_deep(db):
    from build_price_flags import flag_promo_too_deep
    count = flag_promo_too_deep(db)
    db.commit()
    assert count >= 1  # product 2 store 3: 0.50 promo vs 5.0 regular
    row = db.execute(
        "SELECT flag_type FROM price_flags WHERE product_id=2 AND store_id=3"
    ).fetchone()
    assert row is not None
    assert row[0] == "promo_too_deep"


def test_flag_idempotent(db):
    from build_price_flags import flag_outlier_prices
    flag_outlier_prices(db); db.commit()
    count_before = db.execute("SELECT COUNT(*) FROM price_flags").fetchone()[0]
    flag_outlier_prices(db); db.commit()
    count_after = db.execute("SELECT COUNT(*) FROM price_flags").fetchone()[0]
    assert count_before == count_after
```

- [ ] **Step 3.2: Run tests — expect fail**

```bash
pytest tests/test_price_flags.py -v
```

Expected: `ModuleNotFoundError: No module named 'build_price_flags'`

- [ ] **Step 3.3: Implement `build_price_flags.py`**

```python
#!/usr/bin/env python3
"""Persist price quality flags to the price_flags table.

Run after each daily fetch to populate outlier_price, price_spike,
and promo_too_deep flags. Safe to re-run — uses INSERT OR IGNORE.

Usage:
    python build_price_flags.py
    python build_price_flags.py --db data/prices.db
"""

import argparse
import json
import math
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "prices.db"

Z_THRESHOLD = 3.0
SPIKE_PCT = 0.50       # > 50% change
PROMO_DEPTH = 0.20     # promo < 20% of regular avg


def flag_outlier_prices(conn, z_threshold=Z_THRESHOLD):
    """Insert outlier_price flags for prices > z_threshold σ from product mean.

    Computes per-product (mean, variance) in SQL, then fetches individual
    records for products whose price range spans the outlier zone.
    Returns count of new flags inserted.
    """
    stats = conn.execute("""
        SELECT product_id,
               AVG(price) AS avg_p,
               AVG(price * price) - AVG(price) * AVG(price) AS var_p
        FROM prices_current
        WHERE price > 0
        GROUP BY product_id
        HAVING COUNT(*) >= 3
           AND (AVG(price * price) - AVG(price) * AVG(price)) > 0.001
    """).fetchall()

    inserted = 0

    for pid, avg, var in stats:
        if var <= 0:
            continue
        stdev = math.sqrt(var)
        lo = avg - z_threshold * stdev
        hi = avg + z_threshold * stdev

        outliers = conn.execute(
            """SELECT store_id, price, price_date
               FROM prices_current
               WHERE product_id=? AND price>0 AND (price<? OR price>?)""",
            (pid, lo, hi)
        ).fetchall()

        for store_id, price, price_date in outliers:
            z = (price - avg) / stdev
            details = json.dumps({"price": price, "mean": round(avg, 4),
                                  "stdev": round(stdev, 4), "z_score": round(z, 2)})
            cur = conn.execute(
                """INSERT OR IGNORE INTO price_flags
                   (product_id, store_id, price_date, flag_type, details)
                   VALUES (?,?,?,'outlier_price',?)""",
                (pid, store_id, price_date, details)
            )
            inserted += cur.rowcount

    print(f"  outlier_price: {inserted} flags inserted")
    return inserted


def flag_price_spikes(conn, spike_pct=SPIKE_PCT):
    """Insert price_spike flags for > spike_pct change vs previous price.

    Compares the two most recent distinct price_dates in the prices table.
    Returns count of new flags inserted.
    """
    dates = conn.execute(
        "SELECT DISTINCT price_date FROM prices WHERE price_date IS NOT NULL ORDER BY price_date DESC LIMIT 2"
    ).fetchall()
    if len(dates) < 2:
        print("  price_spike: insufficient history (need 2+ dates)")
        return 0

    curr_date, prev_date = dates[0][0], dates[1][0]

    spikes = conn.execute("""
        SELECT a.product_id, a.store_id, a.price AS curr_price, b.price AS prev_price
        FROM prices a
        JOIN prices b ON a.product_id=b.product_id AND a.store_id=b.store_id
        WHERE a.price_date=? AND b.price_date=?
          AND b.price > 0
          AND ABS(a.price - b.price) / b.price > ?
    """, (curr_date, prev_date, spike_pct)).fetchall()

    inserted = 0
    for pid, sid, curr, prev in spikes:
        pct = round((curr - prev) / prev * 100, 1)
        details = json.dumps({"curr_price": curr, "prev_price": prev,
                               "pct_change": pct, "curr_date": curr_date,
                               "prev_date": prev_date})
        cur = conn.execute(
            """INSERT OR IGNORE INTO price_flags
               (product_id, store_id, price_date, flag_type, details)
               VALUES (?,?,?,'price_spike',?)""",
            (pid, sid, curr_date, details)
        )
        inserted += cur.rowcount

    print(f"  price_spike: {inserted} flags inserted ({curr_date} vs {prev_date})")
    return inserted


def flag_promo_too_deep(conn, depth=PROMO_DEPTH):
    """Insert promo_too_deep flags for promo prices < depth × product regular avg.

    Returns count of new flags inserted.
    """
    rows = conn.execute("""
        WITH reg AS (
          SELECT product_id, AVG(price) AS reg_avg
          FROM prices_current
          WHERE promo IS NULL AND price > 0
          GROUP BY product_id
        )
        SELECT pc.product_id, pc.store_id, pc.price, pc.price_date, r.reg_avg
        FROM prices_current pc
        JOIN reg r ON pc.product_id = r.product_id
        WHERE pc.promo IS NOT NULL
          AND pc.price > 0
          AND r.reg_avg > 0
          AND pc.price < r.reg_avg * ?
    """, (depth,)).fetchall()

    inserted = 0
    for pid, sid, price, price_date, reg_avg in rows:
        pct = round(price / reg_avg * 100, 1)
        details = json.dumps({"promo_price": price, "regular_avg": round(reg_avg, 4),
                               "pct_of_regular": pct})
        cur = conn.execute(
            """INSERT OR IGNORE INTO price_flags
               (product_id, store_id, price_date, flag_type, details)
               VALUES (?,?,?,'promo_too_deep',?)""",
            (pid, sid, price_date, details)
        )
        inserted += cur.rowcount

    print(f"  promo_too_deep: {inserted} flags inserted")
    return inserted


def build(db_path=DEFAULT_DB):
    print(f"Building price flags from {db_path}")
    conn = sqlite3.connect(str(db_path))
    total = 0
    total += flag_outlier_prices(conn)
    total += flag_price_spikes(conn)
    total += flag_promo_too_deep(conn)
    conn.commit()
    existing = conn.execute("SELECT COUNT(*) FROM price_flags").fetchone()[0]
    conn.close()
    print(f"  Total new flags: {total} | Total in DB: {existing}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()
    build(args.db)
```

- [ ] **Step 3.4: Run tests — expect pass**

```bash
pytest tests/test_price_flags.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 3.5: Smoke test against real DB**

```bash
source ~/devbox/envs/240826/bin/activate
python build_price_flags.py
```

Expected output like:
```
Building price flags from data/prices.db
  outlier_price: N flags inserted
  price_spike: N flags inserted (2026-04-30 vs 2026-04-29)
  promo_too_deep: N flags inserted
  Total new flags: N | Total in DB: N
```

Then inspect the PENNY question:
```bash
sqlite3 data/prices.db "
SELECT flag_type, COUNT(*) AS cnt FROM price_flags GROUP BY flag_type;
SELECT s.name AS network, COUNT(*) AS flags
FROM price_flags f JOIN stores s ON f.store_id = s.id
JOIN retail_networks n ON s.network_id = n.id
WHERE n.name LIKE '%PENNY%' AND f.flag_type = 'outlier_price'
GROUP BY s.name LIMIT 5;
"
```

If PENNY outlier count is large → data artifact. If small → genuine regional pricing.

- [ ] **Step 3.6: Commit**

```bash
git add build_price_flags.py tests/test_price_flags.py
git commit -m "feat: add price_flags builder with outlier/spike/promo checks (Phase 2)"
```

---

## Task 4: Phase 3 — Clean-Data Filter + New Insight Sections in `generate_site.py`

**Files:**
- Modify: `generate_site.py` (add `_clean_join()` helper + `load_price_changes_week()` + `load_promo_effectiveness()`)
- Modify: `export_analytics.py` (add `price_flags.csv` export)

### Background

`price_flags` may not exist on all deployments yet. All new queries must gracefully degrade when the table is absent. The `_clean_join()` helper returns the appropriate SQL fragment based on whether the table exists.

- [ ] **Step 4.1: Add `_clean_join()` helper and `load_price_changes_week()` to `generate_site.py`**

Add after the `query()` helper function (around line 66):

```python
def _has_price_flags(conn):
    """True if the price_flags table exists."""
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='price_flags'"
    ).fetchone())


def _clean_join(conn, price_table_alias="p"):
    """Return (join_clause, where_clause) to exclude flagged prices.
    Returns empty strings if price_flags table doesn't exist yet.
    """
    if not _has_price_flags(conn):
        return "", ""
    a = price_table_alias
    j = f" LEFT JOIN price_flags pf ON pf.product_id={a}.product_id AND pf.store_id={a}.store_id AND pf.price_date={a}.price_date"
    w = " AND pf.id IS NULL"
    return j, w
```

Then add the new loader (after `load_coverage`, around line 193):

```python
def load_price_changes_week(conn):
    """Products with biggest price changes across the last 7 days.

    Compares each product/store's earliest vs latest price within the window,
    requires price in ≥2 networks to filter noise.
    Returns top 50 increases and top 50 decreases.
    """
    j, w = _clean_join(conn)
    rows = query(conn, f"""
        WITH window AS (
          SELECT p.product_id, p.store_id, p.price, p.price_date,
                 s.network_id
          FROM prices p
          JOIN stores s ON p.store_id = s.id{j}
          WHERE p.price_date >= date((SELECT MAX(price_date) FROM prices), '-7 days')
            AND p.price > 0{w}
        ),
        bounds AS (
          SELECT product_id,
                 MIN(price_date) AS first_date, MAX(price_date) AS last_date,
                 COUNT(DISTINCT network_id) AS networks
          FROM window GROUP BY product_id HAVING networks >= 2
        ),
        first_p AS (
          SELECT w.product_id, AVG(w.price) AS avg_first
          FROM window w JOIN bounds b ON w.product_id=b.product_id
          WHERE w.price_date = b.first_date GROUP BY w.product_id
        ),
        last_p AS (
          SELECT w.product_id, AVG(w.price) AS avg_last
          FROM window w JOIN bounds b ON w.product_id=b.product_id
          WHERE w.price_date = b.last_date GROUP BY w.product_id
        )
        SELECT pr.name AS product, c.name AS category,
               ROUND(fp.avg_first, 2) AS price_start,
               ROUND(lp.avg_last,  2) AS price_end,
               ROUND((lp.avg_last - fp.avg_first) / fp.avg_first * 100, 1) AS change_pct,
               b.first_date, b.last_date
        FROM bounds b
        JOIN first_p fp ON b.product_id = fp.product_id
        JOIN last_p  lp ON b.product_id = lp.product_id
        JOIN products pr ON b.product_id = pr.id
        JOIN categories c ON pr.categ_id = c.id
        WHERE fp.avg_first > 0
        ORDER BY change_pct DESC
    """)
    increases = [r for r in rows if r["change_pct"] > 0][:50]
    decreases = sorted([r for r in rows if r["change_pct"] < 0], key=lambda r: r["change_pct"])[:50]
    return {"increases": increases, "decreases": decreases}


def load_promo_effectiveness(conn):
    """Per-network: what % of promo prices are genuinely cheaper than all regular prices?

    'Genuinely cheap' = promo price < global MIN regular price for that product.
    Excludes SELGROS.
    """
    j, w = _clean_join(conn)
    return query(conn, f"""
        WITH reg AS (
          SELECT p.product_id, MIN(p.price) AS global_min_regular
          FROM prices_current p
          JOIN stores s ON p.store_id = s.id{j}
          WHERE p.promo IS NULL AND p.price > 0{w}
          GROUP BY p.product_id
        ),
        promo AS (
          SELECT p.product_id, s.network_id, MIN(p.price) AS best_promo
          FROM prices_current p
          JOIN stores s ON p.store_id = s.id{j}
          WHERE p.promo IS NOT NULL AND p.price > 0{w}
          GROUP BY p.product_id, s.network_id
        )
        SELECT n.name AS network,
               COUNT(*) AS promo_products,
               SUM(CASE WHEN pr.best_promo < r.global_min_regular THEN 1 ELSE 0 END) AS genuinely_cheap,
               ROUND(100.0 * SUM(CASE WHEN pr.best_promo < r.global_min_regular THEN 1 ELSE 0 END) / COUNT(*), 1) AS pct_genuinely_cheap,
               ROUND(AVG(pr.best_promo / r.global_min_regular * 100), 1) AS avg_promo_vs_market_min_pct
        FROM promo pr
        JOIN reg r ON pr.product_id = r.product_id
        JOIN retail_networks n ON pr.network_id = n.id
        WHERE n.name NOT LIKE '%SELGROS%'
        GROUP BY n.name
        HAVING promo_products >= 5
        ORDER BY pct_genuinely_cheap DESC
    """)
```

- [ ] **Step 4.2: Verify no import errors**

```bash
source ~/devbox/envs/240826/bin/activate
python -c "from generate_site import load_price_changes_week, load_promo_effectiveness; print('OK')"
```

Expected: `OK`

- [ ] **Step 4.3: Smoke test new loaders against real DB**

```bash
python -c "
import sqlite3
from generate_site import load_price_changes_week, load_promo_effectiveness
conn = sqlite3.connect('data/prices.db')
changes = load_price_changes_week(conn)
print('Increases:', len(changes['increases']), changes['increases'][:2] if changes['increases'] else [])
print('Decreases:', len(changes['decreases']))
promos = load_promo_effectiveness(conn)
print('Promo effectiveness by network:')
for r in promos: print(' ', r)
"
```

Inspect output. Promo effectiveness should show Carrefour and Penny (high promo activity). Check that % makes sense.

- [ ] **Step 4.4: Add `price_flags.csv` to `export_analytics.py`**

In `export_analytics.py`, add to the `EXPORTS` list:

```python
    {
        "file": "price_flags_summary.csv",
        "sql": """
            SELECT flag_type, COUNT(*) AS total,
                   COUNT(DISTINCT product_id) AS products,
                   COUNT(DISTINCT store_id) AS stores
            FROM price_flags
            GROUP BY flag_type
        """,
        "desc": "Summary count of price flags by type",
    },
```

- [ ] **Step 4.5: Verify export runs without error**

```bash
source ~/devbox/envs/240826/bin/activate
python export_analytics.py
```

Expected: runs without error; `docs/data/price_flags_summary.csv` created (or logged as skipped if `price_flags` table doesn't exist yet).

`export_analytics.py` already has a `try/except Exception` around each export at line 73 — if `price_flags` is absent the loop prints `price_flags_summary.csv: ERROR — no such table: price_flags` to stderr and continues. No change needed.

- [ ] **Step 4.6: Apply clean-data filter to existing network-comparison loaders**

`load_price_index()` (line ~82) and `load_price_index_by_category()` (line ~114) in `generate_site.py` both query the `prices` table without excluding flagged records. Update both to use `_clean_join()`.

In `load_price_index()`, after `WHERE p.price > 0` add the join and condition:

```python
def load_price_index(conn):
    j, w = _clean_join(conn)
    rows = query(conn, f"""
        WITH pnp AS (
          SELECT p.product_id, n.name AS network, AVG(p.price) AS avg_price
          FROM prices p
          JOIN stores s ON p.store_id = s.id
          JOIN retail_networks n ON s.network_id = n.id{j}
          WHERE p.price > 0{w}
          GROUP BY p.product_id, n.name
        ),
        ...  -- rest of CTE unchanged
    """)
    return rows
```

Apply the same `{j}` / `{w}` substitution to `load_price_index_by_category()`. The join goes after the `retail_networks` join line; `{w}` replaces the bare `WHERE p.price > 0` condition. Both functions already have the full CTE body — only the injection points change.

After editing, confirm:
```bash
python -c "
import sqlite3
from generate_site import load_price_index
conn = sqlite3.connect('data/prices.db')
rows = load_price_index(conn)
print(rows[:3])
"
```

- [ ] **Step 4.7: Add `load_store_price_index()` to `generate_site.py`**

The spec calls for ranking stores by average price for a common basket. `build_baskets.py` already builds baskets and writes `docs/data/baskets/*.json`. Add a loader that reads the basket data to produce a store ranking without re-computing:

```python
def load_store_price_index(conn):
    """Rank stores by average price across the most common products.

    Uses prices_current (snapshot) not prices (history) for speed.
    Requires ≥20 products in common with the product set to qualify a store.
    Excludes SELGROS. Returns top 100 cheapest + top 20 most expensive.
    """
    j, w = _clean_join(conn, price_table_alias="pc")
    rows = query(conn, f"""
        WITH popular AS (
          SELECT product_id FROM prices_current
          GROUP BY product_id
          HAVING COUNT(DISTINCT store_id) >= 50
          ORDER BY COUNT(DISTINCT store_id) DESC
          LIMIT 200
        ),
        store_avg AS (
          SELECT pc.store_id,
                 COUNT(DISTINCT pc.product_id) AS products_priced,
                 AVG(pc.price) AS avg_price
          FROM prices_current pc
          JOIN popular p ON pc.product_id = p.product_id
          JOIN stores s ON pc.store_id = s.id
          LEFT JOIN retail_networks n ON s.network_id = n.id{j}
          WHERE pc.price > 0
            AND (n.name IS NULL OR n.name NOT LIKE '%SELGROS%'){w}
          GROUP BY pc.store_id
          HAVING products_priced >= 20
        )
        SELECT s.name AS store, COALESCE(n.name,'Unknown') AS network,
               u.name AS city,
               ROUND(sa.avg_price, 2) AS avg_price,
               sa.products_priced
        FROM store_avg sa
        JOIN stores s ON sa.store_id = s.id
        LEFT JOIN retail_networks n ON s.network_id = n.id
        LEFT JOIN uats u ON s.uat_id = u.id
        ORDER BY sa.avg_price
        LIMIT 120
    """)
    return {"cheapest": rows[:100], "priciest": rows[-20:] if len(rows) >= 20 else rows}
```

Smoke test:
```bash
python -c "
import sqlite3
from generate_site import load_store_price_index
conn = sqlite3.connect('data/prices.db')
idx = load_store_price_index(conn)
print('Cheapest:', idx['cheapest'][:3])
print('Priciest:', idx['priciest'][:3])
"
```

- [ ] **Step 4.8: Commit**

```bash
git add generate_site.py export_analytics.py
git commit -m "feat: add clean-data filter, price change tracker, promo effectiveness, store price index (Phase 3)"
```

---

## Task 5: Wire into CI

**Files:**
- Modify: `.github/workflows/ci_prices.yml`

- [ ] **Step 5.1: Add `build_price_flags` step before `export_analytics` in CI**

The CI already calls `export_analytics.py` at line 82 and `build_anomalies.py` at line 88. `build_price_flags.py` must run **before** `export_analytics.py` so the `price_flags` table is populated when the CSV export runs.

In `.github/workflows/ci_prices.yml`, add this step **before** the `Export analytics` step (currently line ~82):

```yaml
      - name: Build price flags
        run: python build_price_flags.py data/prices_ci.db
```

Then add the health report step **after** `Generate site` but before `Export analytics`:

```yaml
      - name: Generate pipeline health report
        run: python generate_pipeline_report.py --db data/prices_ci.db --out docs/pipeline-health.html
```

- [ ] **Step 5.2: Add new output file to the commit step**

In the `Commit results` step, add to the `git add` block:

```bash
          git add docs/pipeline-health.html 2>/dev/null || true
```

Place it after the existing `git add docs/analytics.html docs/CNAME || true` line.

- [ ] **Step 5.3: Verify CI yaml is valid**

```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/ci_prices.yml'))" && echo "YAML OK"
```

Expected: `YAML OK`

- [ ] **Step 5.4: Commit**

```bash
git add .github/workflows/ci_prices.yml
git commit -m "ci: add price flags builder and pipeline health report to daily workflow"
```

---

## Verification Checklist

After all tasks are done:

```bash
# All tests pass
source ~/devbox/envs/240826/bin/activate
pytest tests/ -v

# Phase 1: pipeline report renders with data
python generate_pipeline_report.py
# Open docs/pipeline-health.html — confirm 4 summary cards, tables populated

# Phase 2: flag counts look reasonable
python build_price_flags.py
sqlite3 data/prices.db "SELECT flag_type, COUNT(*) FROM price_flags GROUP BY flag_type;"

# Phase 3: smoke test PENNY question
sqlite3 data/prices.db "
  SELECT COUNT(*) AS penny_flags
  FROM price_flags f
  JOIN stores s ON f.store_id = s.id
  JOIN retail_networks n ON s.network_id = n.id
  WHERE n.name LIKE '%PENNY%' AND f.flag_type = 'outlier_price';
"
# If penny_flags ≈ 0 → 70% spread is genuine regional pricing (publish it)
# If penny_flags is high → data artifact (filter before publishing)

# Phase 3: verify promo effectiveness makes sense
python -c "
import sqlite3
from generate_site import load_promo_effectiveness
conn = sqlite3.connect('data/prices.db')
for r in load_promo_effectiveness(conn):
    print(r['network'], r['pct_genuinely_cheap'], '%')
"
```
