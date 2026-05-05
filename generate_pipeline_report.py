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
import html
import math
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "prices.db"
DEFAULT_OUT = ROOT / "docs" / "pipeline-health.html"

STALE_DAYS = 2
OUTLIER_Z = 3.0
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
    cols = ["id", "script", "started_at", "finished_at", "status",
            "uats_processed", "records_written", "notes"]
    return [dict(zip(cols, r)) for r in rows]


def compute_outlier_summary(conn, z_threshold=OUTLIER_Z):
    """Return {total_records, flagged_count, flagged_pct, top_products}.

    Uses mean + variance from prices_current (avoids SQRT in SQL):
    condition: (price - avg)^2 > z^2 * variance
    """
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

    # Also flag products with only 2 stores but extreme ratio (>10x spread)
    ratio_rows = conn.execute("""
        SELECT product_id,
               COUNT(*) AS n,
               AVG(price) AS avg_p,
               MIN(price) AS min_p,
               MAX(price) AS max_p
        FROM prices_current
        WHERE price > 0
        GROUP BY product_id
        HAVING n >= 2 AND max_p > min_p * 10
    """).fetchall()

    total = conn.execute("SELECT COUNT(*) FROM prices_current WHERE price > 0").fetchone()[0]

    outlier_pids = {}  # pid -> (lo, hi, avg)
    for pid, n, avg, var in stats_rows:
        if var > 0:
            stdev = math.sqrt(var)
            hi = avg + z_threshold * stdev
            lo = avg - z_threshold * stdev
            mi, ma = conn.execute(
                "SELECT MIN(price), MAX(price) FROM prices_current WHERE product_id=? AND price>0",
                (pid,)
            ).fetchone()
            if mi < lo or ma > hi:
                outlier_pids[pid] = (lo, hi, avg)

    # Include ratio-based outliers (catches 2-store case)
    for pid, n, avg, min_p, max_p in ratio_rows:
        if pid not in outlier_pids:
            # Use ratio threshold: flag the max if max > 10 * min
            lo = 0
            hi = min_p * 10
            outlier_pids[pid] = (lo, hi, avg)

    flagged = 0
    top_products = []
    for pid, (lo, hi, avg) in list(outlier_pids.items())[:500]:
        cnt = conn.execute(
            "SELECT COUNT(*) FROM prices_current WHERE product_id=? AND price>0 AND (price<? OR price>?)",
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
    """Return list of promos whose price < depth_pct% of the product's avg regular price."""
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
    cols = ["product_id", "product", "store_id", "store", "promo_price", "regular_avg", "pct_of_regular"]
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
    rag_vel = _rag(velocity["status"] == "ok", velocity["status"] in ("stuck", "insufficient_data"),
                   f"{velocity['changed_pct']}% changed — normal",
                   f"{velocity['changed_pct']}% changed — watch",
                   velocity.get("status", ""))
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
            f"<td>{html.escape(str(r['name']))}</td><td>{html.escape(str(r['network']))}</td>"
            f"<td>{html.escape(str(r['last_date']))}</td><td>{r['days_stale']}</td>"
            f"<td>{'Stale' if r['stale'] else 'OK'}</td>"
        )
    )

    run_rows = runs[:10]
    run_table = rows_table(
        ["ID", "Script", "Started", "Status", "UATs", "Records"],
        run_rows,
        lambda r: (
            f"<td>{r['id']}</td><td>{html.escape(str(r['script']))}</td><td>{(r['started_at'] or '')[:16]}</td>"
            f"<td>{html.escape(str(r['status']))}</td><td>{r['uats_processed'] or '—'}</td>"
            f"<td>{r['records_written'] or '—'}</td>"
        )
    )

    outlier_rows = outlier["top_products"][:20]
    outlier_table = rows_table(
        ["Product", "Outlier records", "Avg price"],
        outlier_rows,
        lambda r: f"<td>{html.escape(str(r['name']))}</td><td>{r['outlier_count']}</td><td>{r['avg']}</td>"
    )

    promo_table = rows_table(
        ["Product", "Store", "Promo price", "Regular avg", "% of regular"],
        promos[:20],
        lambda r: (
            f"<td>{html.escape(str(r['product']))}</td><td>{html.escape(str(r['store']))}</td>"
            f"<td>{r['promo_price']}</td><td>{round(r['regular_avg'], 2)}</td>"
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
  <div class="card"><h3>Price Velocity</h3>{rag_badge(rag_vel)}<br><small>{velocity.get('changed_count', 0)} changes on {velocity.get('curr_date') or '—'}</small></div>
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
    report_html = render_html(data)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report_html, encoding="utf-8")
    print(f"  Written {out} ({len(report_html) // 1024} KB)")
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
