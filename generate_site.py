#!/usr/bin/env python3
"""
Generate static GitHub Pages site from data/prices.db.

Pages generated:
  docs/index.html        — Dashboard with KPIs, charts, navigation
  docs/price-index.html  — Network Price Index ("who's cheapest")
  docs/fuel.html         — Fuel Price Leaderboard
  docs/pipeline.html     — Pipeline health & coverage
  docs/stores_map.html   — Enhanced interactive store map

Usage:
    python generate_site.py
    python generate_site.py --db data/prices.db --out docs/
"""

import argparse
import json
import sqlite3
from pathlib import Path

DB_PATH = Path("data/prices.db")
OUT_DIR = Path("docs")

# ── Network colors ──────────────────────────────────────────────────────

NETWORK_COLORS = {
    "PROFI":      "#e74c3c",
    "MEGA IMAGE": "#8e44ad",
    "CARREFOUR":  "#2980b9",
    "KAUFLAND":   "#e67e22",
    "AUCHAN":     "#27ae60",
    "PENNY":      "#c0392b",
    "LIDL":       "#f1c40f",
    "SELGROS":    "#16a085",
    "SUPECO":     "#d35400",
    "CORA":       "#2c3e50",
}
GAS_COLORS = {
    "PETROM":    "#004a99",
    "OMV":       "#009639",
    "ROMPETROL": "#ffd700",
    "LUKOIL":    "#e31e24",
    "MOL":       "#ff6600",
    "SOCAR":     "#00a1de",
    "GAZPROM":   "#0066b3",
}
DEFAULT_COLOR = "#94a3b8"


def net_color(name: str, palette=NETWORK_COLORS) -> str:
    if not name:
        return DEFAULT_COLOR
    upper = name.upper()
    for key, color in palette.items():
        if key in upper:
            return color
    return DEFAULT_COLOR


# ── Database queries ────────────────────────────────────────────────────

def query(conn, sql, params=()):
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def load_summary(conn):
    """KPI counts and latest dates."""
    counts = {}
    for table in ("stores", "products", "categories", "retail_networks",
                  "gas_stations", "gas_networks", "gas_products", "uats",
                  "prices", "gas_prices"):
        counts[table] = query(conn, f"SELECT COUNT(*) as n FROM {table}")[0]["n"]

    latest_retail = query(conn, "SELECT MAX(price_date) as d FROM prices")[0]["d"] or "—"
    latest_gas = query(conn, "SELECT MAX(price_date) as d FROM gas_prices")[0]["d"] or "—"

    return {**counts, "latest_retail": latest_retail, "latest_gas": latest_gas}


def load_price_index(conn):
    """Network price index: for products in 3+ networks, avg(price / cheapest * 100)."""
    rows = query(conn, """
        WITH pnp AS (
          SELECT p.product_id, n.name AS network, AVG(p.price) AS avg_price
          FROM prices p
          JOIN stores s ON p.store_id = s.id
          JOIN retail_networks n ON s.network_id = n.id
          WHERE p.price > 0
          GROUP BY p.product_id, n.name
        ),
        multi AS (
          SELECT product_id FROM pnp
          GROUP BY product_id HAVING COUNT(DISTINCT network) >= 3
        ),
        ref AS (
          SELECT product_id, MIN(avg_price) AS min_price
          FROM pnp WHERE product_id IN (SELECT product_id FROM multi)
          GROUP BY product_id
        )
        SELECT pnp.network,
               ROUND(AVG(pnp.avg_price / ref.min_price * 100), 1) AS price_index,
               COUNT(*) AS products
        FROM pnp
        JOIN multi m ON pnp.product_id = m.product_id
        JOIN ref ON pnp.product_id = ref.product_id
        GROUP BY pnp.network
        ORDER BY price_index
    """)
    return rows


def load_price_index_by_category(conn):
    """Network price index broken down by top-level category."""
    rows = query(conn, """
        WITH pnp AS (
          SELECT p.product_id, n.name AS network, AVG(p.price) AS avg_price,
                 c.name AS category
          FROM prices p
          JOIN stores s ON p.store_id = s.id
          JOIN retail_networks n ON s.network_id = n.id
          JOIN products pr ON p.product_id = pr.id
          JOIN categories c ON pr.categ_id = c.id
          WHERE p.price > 0 AND c.parent_id = 1
          GROUP BY p.product_id, n.name, c.name
        ),
        multi AS (
          SELECT product_id FROM pnp
          GROUP BY product_id HAVING COUNT(DISTINCT network) >= 3
        ),
        ref AS (
          SELECT product_id, MIN(avg_price) AS min_price
          FROM pnp WHERE product_id IN (SELECT product_id FROM multi)
          GROUP BY product_id
        )
        SELECT pnp.category, pnp.network,
               ROUND(AVG(pnp.avg_price / ref.min_price * 100), 1) AS price_index,
               COUNT(*) AS products
        FROM pnp
        JOIN multi m ON pnp.product_id = m.product_id
        JOIN ref ON pnp.product_id = ref.product_id
        GROUP BY pnp.category, pnp.network
        ORDER BY pnp.category, price_index
    """)
    # Group by category
    by_cat = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r)
    return by_cat


def load_fuel_prices(conn):
    """Fuel prices by network and fuel type."""
    return query(conn, """
        SELECT gn.name AS network, gp2.name AS fuel, gp.product_id AS fuel_id,
               ROUND(AVG(gp.price), 2) AS avg_price,
               ROUND(MIN(gp.price), 2) AS min_price,
               ROUND(MAX(gp.price), 2) AS max_price,
               COUNT(DISTINCT gp.station_id) AS stations
        FROM gas_prices gp
        JOIN gas_stations gs ON gp.station_id = gs.id
        JOIN gas_networks gn ON gs.network_id = gn.id
        JOIN gas_products gp2 ON gp.product_id = gp2.id
        GROUP BY gn.name, gp2.name
        ORDER BY gp2.name, avg_price
    """)


def load_runs(conn):
    """Pipeline run history."""
    return query(conn, """
        SELECT id, script, started_at, finished_at, status,
               uats_processed, records_written, notes
        FROM runs ORDER BY id DESC LIMIT 20
    """)


def load_coverage(conn):
    """Store coverage by network for latest date."""
    return query(conn, """
        SELECT n.name AS network,
               COUNT(DISTINCT s.id) AS stores_with_prices,
               COUNT(DISTINCT p.product_id) AS products_covered,
               (SELECT COUNT(*) FROM stores s2 WHERE s2.network_id = n.id) AS total_stores
        FROM prices p
        JOIN stores s ON p.store_id = s.id
        JOIN retail_networks n ON s.network_id = n.id
        WHERE p.price_date = (SELECT MAX(price_date) FROM prices)
        GROUP BY n.name
        ORDER BY stores_with_prices DESC
    """)


def load_network_trends(conn):
    """Network Price Index per date (time-series)."""
    return query(conn, """
        WITH base AS (
          SELECT p.price_date, s.network_id, p.product_id, AVG(p.price) AS avg_price
          FROM prices p JOIN stores s ON p.store_id = s.id
          WHERE p.price > 0
          GROUP BY p.price_date, s.network_id, p.product_id
        ),
        multi AS (
          SELECT product_id, price_date FROM base
          GROUP BY product_id, price_date
          HAVING COUNT(DISTINCT network_id) >= 3
        ),
        filt AS (
          SELECT b.* FROM base b
          JOIN multi m ON b.product_id = m.product_id AND b.price_date = m.price_date
        ),
        mins AS (
          SELECT price_date, product_id, MIN(avg_price) AS min_price
          FROM filt GROUP BY price_date, product_id
        ),
        indexed AS (
          SELECT f.price_date, f.network_id,
                 AVG(f.avg_price / m.min_price * 100) AS idx
          FROM filt f JOIN mins m ON f.price_date=m.price_date AND f.product_id=m.product_id
          GROUP BY f.price_date, f.network_id
        )
        SELECT i.price_date, n.name AS network, ROUND(i.idx, 1) AS index_value
        FROM indexed i JOIN retail_networks n ON i.network_id = n.id
        ORDER BY i.price_date, i.idx
    """)


def load_category_trends(conn):
    """Average price per top-level category per date."""
    return query(conn, """
        SELECT p.price_date, c.name AS category,
               ROUND(AVG(p.price), 2) AS avg_price,
               COUNT(DISTINCT p.product_id) AS products
        FROM prices p
        JOIN products pr ON p.product_id = pr.id
        JOIN categories c ON pr.categ_id = c.id
        WHERE p.price > 0 AND c.parent_id = 1
        GROUP BY p.price_date, c.name
        ORDER BY p.price_date, c.name
    """)


def load_fuel_trends(conn):
    """Average fuel price per network and product type per date."""
    return query(conn, """
        SELECT gp.price_date, n.name AS network, gp2.name AS fuel,
               ROUND(AVG(gp.price), 3) AS avg_price
        FROM gas_prices gp
        JOIN gas_stations gs ON gp.station_id = gs.id
        JOIN gas_networks n ON gs.network_id = n.id
        JOIN gas_products gp2 ON gp.product_id = gp2.id
        WHERE gp.price > 0
        GROUP BY gp.price_date, n.id, gp2.id
        ORDER BY gp2.name, gp.price_date, n.name
    """)


def load_compare_index(conn):
    """Lightweight compare index: product list + network colors for the dropdown."""
    latest = query(conn, "SELECT MAX(price_date) AS d FROM prices")[0]["d"]
    if not latest:
        return {"latest_date": None, "products": [], "net_colors": {}}

    products = query(conn, """
        WITH qualifying AS (
          SELECT p.product_id
          FROM prices p JOIN stores s ON p.store_id = s.id
          WHERE p.price_date = ? AND p.price > 0
          GROUP BY p.product_id
          HAVING COUNT(DISTINCT s.network_id) >= 3
        )
        SELECT DISTINCT p.product_id AS id, pr.name AS name, c.name AS cat
        FROM prices p
        JOIN qualifying q ON p.product_id = q.product_id
        JOIN products pr ON p.product_id = pr.id
        JOIN categories c ON pr.categ_id = c.id
        WHERE p.price_date = ?
        ORDER BY pr.name
    """, (latest, latest))

    networks = query(conn, "SELECT name FROM retail_networks ORDER BY name")
    net_colors = {r["name"]: net_color(r["name"]) for r in networks}

    return {"latest_date": latest, "products": products, "net_colors": net_colors}


def build_compare_data_files(conn, out_dir: Path):
    """Write docs/data/index.json and docs/data/products/{id}.csv for each product."""
    import csv, io

    data_dir = out_dir / "data" / "products"
    data_dir.mkdir(parents=True, exist_ok=True)

    latest = query(conn, "SELECT MAX(price_date) AS d FROM prices")[0]["d"]
    if not latest:
        return 0

    # Latest cross-network prices
    latest_rows = query(conn, """
        WITH qualifying AS (
          SELECT p.product_id
          FROM prices p JOIN stores s ON p.store_id = s.id
          WHERE p.price_date = ? AND p.price > 0
          GROUP BY p.product_id
          HAVING COUNT(DISTINCT s.network_id) >= 3
        )
        SELECT p.product_id, pr.name AS product_name, c.name AS category,
               n.name AS network,
               ROUND(AVG(p.price), 2) AS avg_price,
               ROUND(MIN(p.price), 2) AS min_price,
               ROUND(MAX(p.price), 2) AS max_price,
               COUNT(DISTINCT p.store_id) AS stores
        FROM prices p
        JOIN qualifying q ON p.product_id = q.product_id
        JOIN products pr ON p.product_id = pr.id
        JOIN categories c ON pr.categ_id = c.id
        JOIN stores s ON p.store_id = s.id
        JOIN retail_networks n ON s.network_id = n.id
        WHERE p.price_date = ? AND p.price > 0
        GROUP BY p.product_id, n.id
        ORDER BY p.product_id, avg_price
    """, (latest, latest))

    product_ids = list({r["product_id"] for r in latest_rows})
    if not product_ids:
        return 0

    # History for all qualifying products
    placeholders = ",".join("?" * len(product_ids))
    history_rows = query(conn, f"""
        SELECT p.product_id, n.name AS network, p.price_date,
               ROUND(AVG(p.price), 2) AS avg_price
        FROM prices p
        JOIN stores s ON p.store_id = s.id
        JOIN retail_networks n ON s.network_id = n.id
        WHERE p.product_id IN ({placeholders}) AND p.price > 0
        GROUP BY p.product_id, n.id, p.price_date
        ORDER BY p.product_id, p.price_date, n.name
    """, product_ids)

    # Group by product_id
    by_pid_latest: dict = {}
    for r in latest_rows:
        by_pid_latest.setdefault(r["product_id"], []).append(r)

    by_pid_history: dict = {}
    for r in history_rows:
        by_pid_history.setdefault(r["product_id"], []).append(r)

    # Write one CSV per product: two sections separated by blank line
    for pid in product_ids:
        buf = io.StringIO()
        w = csv.writer(buf)

        # Section 1 — latest prices
        w.writerow(["network", "avg_price", "min_price", "max_price", "stores"])
        for r in by_pid_latest.get(pid, []):
            w.writerow([r["network"], r["avg_price"], r["min_price"],
                        r["max_price"], r["stores"]])

        buf.write("\n")  # blank line separator

        # Section 2 — history
        w.writerow(["network", "price_date", "avg_price"])
        for r in by_pid_history.get(pid, []):
            w.writerow([r["network"], r["price_date"], r["avg_price"]])

        (data_dir / f"{pid}.csv").write_text(buf.getvalue(), encoding="utf-8")

    # Write index.json — product list + metadata for the dropdown
    seen: dict = {}
    for r in latest_rows:
        pid = r["product_id"]
        if pid not in seen:
            seen[pid] = {"id": pid, "name": r["product_name"], "cat": r["category"]}

    networks = query(conn, "SELECT name FROM retail_networks ORDER BY name")
    index = {
        "latest_date": latest,
        "net_colors": {r["name"]: net_color(r["name"]) for r in networks},
        "products": list(seen.values()),
    }
    (out_dir / "data" / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    return len(product_ids)


def load_stores(conn):
    """Stores with network names for map."""
    return query(conn, """
        SELECT s.id, s.name, s.addr, s.lat, s.lon,
               COALESCE(n.name, '') AS network
        FROM stores s
        LEFT JOIN retail_networks n ON s.network_id = n.id
        WHERE s.lat IS NOT NULL AND s.lon IS NOT NULL
          AND s.lat != 0 AND s.lon != 0
        ORDER BY n.name NULLS LAST, s.name
    """)


# ── Shared HTML components ──────────────────────────────────────────────

SHARED_CSS = """
:root {
  --bg: #f1f5f9; --card: #ffffff; --text: #1e293b; --muted: #64748b;
  --border: #e2e8f0; --primary: #2563eb; --primary-light: #dbeafe;
  --success: #16a34a; --warning: #f59e0b; --danger: #ef4444;
  --radius: 10px; --shadow: 0 1px 3px rgba(0,0,0,.08), 0 1px 2px rgba(0,0,0,.06);
}
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       background: var(--bg); color: var(--text); line-height: 1.5; }
a { color: var(--primary); text-decoration: none; }
a:hover { text-decoration: underline; }

/* Nav */
.nav { background: #0f172a; color: #fff; padding: 0 24px; display: flex;
       align-items: center; gap: 0; position: sticky; top: 0; z-index: 100;
       box-shadow: 0 1px 3px rgba(0,0,0,.2); }
.nav-brand { font-weight: 700; font-size: 15px; padding: 14px 16px 14px 0;
             color: #fff; white-space: nowrap; border-right: 1px solid rgba(255,255,255,.15);
             margin-right: 8px; }
.nav a { color: rgba(255,255,255,.7); padding: 14px 16px; font-size: 13.5px;
         font-weight: 500; transition: color .15s; text-decoration: none;
         border-bottom: 2px solid transparent; }
.nav a:hover { color: #fff; text-decoration: none; }
.nav a.active { color: #fff; border-bottom-color: #60a5fa; }

/* Layout */
.container { max-width: 1200px; margin: 0 auto; padding: 24px 20px; }
h1 { font-size: 22px; font-weight: 700; margin-bottom: 4px; }
.subtitle { color: var(--muted); font-size: 14px; margin-bottom: 24px; }

/* KPI cards */
.kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
            gap: 14px; margin-bottom: 28px; }
.kpi { background: var(--card); border-radius: var(--radius); padding: 18px 20px;
       box-shadow: var(--shadow); }
.kpi-value { font-size: 28px; font-weight: 700; color: var(--text); line-height: 1.2; }
.kpi-label { font-size: 12.5px; color: var(--muted); margin-top: 4px; text-transform: uppercase;
             letter-spacing: .3px; }

/* Cards */
.card { background: var(--card); border-radius: var(--radius); padding: 24px;
        box-shadow: var(--shadow); margin-bottom: 24px; }
.card-title { font-size: 16px; font-weight: 600; margin-bottom: 16px; }

/* Tables */
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
th { text-align: left; padding: 10px 12px; border-bottom: 2px solid var(--border);
     font-weight: 600; color: var(--muted); font-size: 11.5px; text-transform: uppercase;
     letter-spacing: .3px; }
td { padding: 9px 12px; border-bottom: 1px solid var(--border); }
tr:hover td { background: #f8fafc; }

/* Chart container */
.chart-box { position: relative; height: 340px; }

/* Tabs / pills */
.tabs { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 18px; }
.tab-btn { padding: 6px 14px; border-radius: 6px; border: 1px solid var(--border);
           background: var(--card); font-size: 13px; cursor: pointer; color: var(--text);
           transition: all .15s; }
.tab-btn:hover { border-color: var(--primary); color: var(--primary); }
.tab-btn.active { background: var(--primary); color: #fff; border-color: var(--primary); }

/* Status badges */
.badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11.5px;
         font-weight: 600; }
.badge-ok { background: #dcfce7; color: #166534; }
.badge-warn { background: #fef3c7; color: #92400e; }
.badge-err { background: #fee2e2; color: #991b1b; }
.badge-run { background: #dbeafe; color: #1e40af; }

/* Bar (inline) */
.bar-cell { position: relative; }
.bar-fill { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 2px;
            opacity: .15; }
.bar-text { position: relative; z-index: 1; }

/* Footer */
.footer { text-align: center; padding: 32px 20px 24px; color: var(--muted); font-size: 12px; }

/* Responsive */
@media (max-width: 640px) {
  .nav { padding: 0 12px; flex-wrap: wrap; }
  .nav-brand { border-right: none; padding-right: 8px; margin-right: 0; }
  .nav a { padding: 10px 10px; font-size: 12.5px; }
  .container { padding: 16px 12px; }
  .kpi-grid { grid-template-columns: repeat(2, 1fr); gap: 10px; }
  .kpi-value { font-size: 22px; }
  h1 { font-size: 18px; }
  .chart-box { height: 260px; }
}
"""

NAV_ITEMS = [
    ("index.html",       "Dashboard"),
    ("price-index.html", "Index Prețuri"),
    ("trends.html",      "Tendințe"),
    ("compare.html",     "Comparare"),
    ("fuel.html",        "Carburanți"),
    ("pipeline.html",    "Pipeline"),
    ("stores_map.html",  "Hartă"),
]


def nav_html(active_page: str) -> str:
    links = []
    for href, label in NAV_ITEMS:
        cls = ' class="active"' if href == active_page else ""
        links.append(f'<a href="{href}"{cls}>{label}</a>')
    return (
        '<nav class="nav">'
        '<span class="nav-brand">Monitorul Prețurilor<sup>+</sup></span>'
        + "".join(links) +
        '</nav>'
    )


def page_shell(title: str, active_page: str, body: str, extra_head: str = "",
               extra_scripts: str = "") -> str:
    return f"""<!DOCTYPE html>
<html lang="ro">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{title} — Monitorul Prețurilor+ – gov2.ro</title>
<style>{SHARED_CSS}</style>
{extra_head}
</head>
<body>
{nav_html(active_page)}
{body}
<footer class="footer">
  <b>Monitorul Prețurilor<sup>+</sup></b> (WIP) &middot; Acesta nu este un proiect oficial al Guvernului României. Date preluate de pe <a href="https://monitorulpreturilor.info/" target=_blank rel="nofollow">monitorulpreturilor.info</a>  &middot; <a href="https://github.com/gov2-ro/monitorulpreturilor">GitHub</a>
</footer>
{extra_scripts}
</body>
</html>"""


def jdump(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


# ── Page generators ─────────────────────────────────────────────────────

def gen_index(summary, price_index, fuel_prices):
    """Dashboard / landing page."""
    # Fuel summary: cheapest network per fuel type
    fuel_by_type = {}
    for fp in fuel_prices:
        if fp["fuel"] not in fuel_by_type:
            fuel_by_type[fp["fuel"]] = fp  # already sorted by avg_price

    fuel_rows = ""
    for fuel, fp in fuel_by_type.items():
        fuel_rows += (
            f'<tr><td>{fuel}</td>'
            f'<td style="font-weight:600">{fp["network"]}</td>'
            f'<td>{fp["avg_price"]} RON</td></tr>'
        )

    body = f"""
<div class="container">
  <h1>Dashboard</h1>
  <p class="subtitle">Monitorizarea prețurilor din România — date publice publicate de către <a href="https://www.consiliulconcurentei.ro/" target=_blank rel="nofollow">Consiliul Concurenței</a> &rarr; <a href="https://monitorulpreturilor.info/" target=_blank rel="nofollow">monitorulpreturilor.info</a></p>

  <div class="kpi-grid">
    <div class="kpi">
      <div class="kpi-value">{summary['stores']:,}</div>
      <div class="kpi-label">Magazine</div>
    </div>
    <div class="kpi">
      <div class="kpi-value">{summary['retail_networks']:,}</div>
      <div class="kpi-label">Rețele retail</div>
    </div>
    <div class="kpi">
      <div class="kpi-value">{summary['products']:,}</div>
      <div class="kpi-label">Produse</div>
    </div>
    <div class="kpi">
      <div class="kpi-value">{summary['prices']:,}</div>
      <div class="kpi-label">Prețuri colectate</div>
    </div>
    <div class="kpi">
      <div class="kpi-value">{summary['gas_stations']:,}</div>
      <div class="kpi-label">Benzinării</div>
    </div>
    <div class="kpi">
      <div class="kpi-value">{summary['gas_prices']:,}</div>
      <div class="kpi-label">Prețuri carburanți</div>
    </div>
  </div>

  <div style="display:grid; grid-template-columns: 1fr 1fr; gap: 24px;">
    <div class="card" style="grid-column: 1 / -1;">
      <div class="card-title">Index Prețuri pe Rețea</div>
      <p style="color:var(--muted);font-size:13px;margin-bottom:14px">
        100 = cea mai ieftină rețea (produse comparabile, disponibile în 3+ rețele)</p>
      <div class="chart-box"><canvas id="indexChart"></canvas></div>
    </div>

    <div class="card">
      <div class="card-title">Cel mai ieftin carburant</div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Tip</th><th>Rețea</th><th>Preț mediu</th></tr></thead>
          <tbody>{fuel_rows}</tbody>
        </table>
      </div>
    </div>

    <div class="card">
      <div class="card-title">Date recente</div>
      <table>
        <tr><td style="color:var(--muted)">Ultimul preț retail</td>
            <td style="font-weight:600">{summary['latest_retail']}</td></tr>
        <tr><td style="color:var(--muted)">Ultimul preț carburant</td>
            <td style="font-weight:600">{summary['latest_gas']}</td></tr>
        <tr><td style="color:var(--muted)">UAT-uri acoperite</td>
            <td style="font-weight:600">{summary['uats']}</td></tr>
        <tr><td style="color:var(--muted)">Categorii produse</td>
            <td style="font-weight:600">{summary['categories']}</td></tr>
      </table>
    </div>
  </div>
</div>"""

    scripts = f"""
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script>
const piData = {jdump(price_index)};
new Chart(document.getElementById('indexChart'), {{
  type: 'bar',
  data: {{
    labels: piData.map(d => d.network),
    datasets: [{{
      label: 'Index preț (100 = cel mai ieftin)',
      data: piData.map(d => d.price_index),
      backgroundColor: piData.map(d => {{
        const idx = d.price_index;
        if (idx <= 110) return '#22c55e';
        if (idx <= 130) return '#84cc16';
        if (idx <= 150) return '#eab308';
        if (idx <= 180) return '#f97316';
        return '#ef4444';
      }}),
      borderRadius: 4,
      barPercentage: 0.7,
    }}]
  }},
  options: {{
    indexAxis: 'y',
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{
      legend: {{ display: false }},
      tooltip: {{ callbacks: {{
        label: ctx => ctx.parsed.x.toFixed(1) + ' (din ' + piData[ctx.dataIndex].products + ' produse)'
      }} }}
    }},
    scales: {{
      x: {{ beginAtZero: true, grid: {{ color: '#f1f5f9' }},
            ticks: {{ callback: v => v }} }},
      y: {{ grid: {{ display: false }} }}
    }}
  }}
}});
</script>"""

    return page_shell("Dashboard", "index.html", body, extra_scripts=scripts)


def gen_price_index(price_index, by_category):
    """Network price index page with category breakdown."""
    # Overall table rows
    overall_rows = ""
    for r in price_index:
        idx = r["price_index"]
        color = ("#22c55e" if idx <= 110 else "#84cc16" if idx <= 130 else
                 "#eab308" if idx <= 150 else "#f97316" if idx <= 180 else "#ef4444")
        pct = min(idx / 2.5, 100)
        overall_rows += (
            f'<tr><td style="font-weight:600">{r["network"]}</td>'
            f'<td class="bar-cell">'
            f'<div class="bar-fill" style="width:{pct}%;background:{color}"></div>'
            f'<span class="bar-text">{idx}</span></td>'
            f'<td>{r["products"]:,}</td></tr>'
        )

    # Category tabs + data
    cats = sorted(by_category.keys())
    cat_buttons = ""
    for i, cat in enumerate(cats):
        cls = "tab-btn active" if i == 0 else "tab-btn"
        cat_buttons += f'<button class="{cls}" data-cat="{cat}">{cat}</button>'

    body = f"""
<div class="container">
  <h1>Index Prețuri pe Rețea</h1>
  <p class="subtitle">
    Produse disponibile în 3+ rețele — index normalizat (100 = cel mai ieftin)
  </p>

  <div class="card">
    <div class="card-title">Clasament general</div>
    <div style="display:grid; grid-template-columns: 1fr 1fr; gap: 24px; align-items: start;">
      <div class="chart-box"><canvas id="indexChart"></canvas></div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Rețea</th><th>Index</th><th>Produse</th></tr></thead>
          <tbody>{overall_rows}</tbody>
        </table>
      </div>
    </div>
  </div>

  <div class="card">
    <div class="card-title">Pe categorii</div>
    <div class="tabs" id="catTabs">{cat_buttons}</div>
    <div class="chart-box" style="height:380px"><canvas id="catChart"></canvas></div>
  </div>
</div>"""

    scripts = f"""
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script>
const piData = {jdump(price_index)};
const byCat = {jdump(by_category)};

/* Overall chart */
new Chart(document.getElementById('indexChart'), {{
  type: 'bar',
  data: {{
    labels: piData.map(d => d.network),
    datasets: [{{
      data: piData.map(d => d.price_index),
      backgroundColor: piData.map(d => {{
        const i = d.price_index;
        return i<=110?'#22c55e':i<=130?'#84cc16':i<=150?'#eab308':i<=180?'#f97316':'#ef4444';
      }}),
      borderRadius: 4, barPercentage: 0.7,
    }}]
  }},
  options: {{
    indexAxis: 'y', responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ beginAtZero: true, grid: {{ color: '#f1f5f9' }} }},
      y: {{ grid: {{ display: false }} }}
    }}
  }}
}});

/* Category chart */
const catCtx = document.getElementById('catChart');
let catChart = null;
function renderCat(cat) {{
  const data = byCat[cat] || [];
  if (catChart) catChart.destroy();
  catChart = new Chart(catCtx, {{
    type: 'bar',
    data: {{
      labels: data.map(d => d.network),
      datasets: [{{
        data: data.map(d => d.price_index),
        backgroundColor: data.map(d => {{
          const i = d.price_index;
          return i<=110?'#22c55e':i<=130?'#84cc16':i<=150?'#eab308':i<=180?'#f97316':'#ef4444';
        }}),
        borderRadius: 4, barPercentage: 0.7,
      }}]
    }},
    options: {{
      indexAxis: 'y', responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ display: false }} }},
      scales: {{
        x: {{ beginAtZero: true, grid: {{ color: '#f1f5f9' }} }},
        y: {{ grid: {{ display: false }} }}
      }}
    }}
  }});
}}
document.getElementById('catTabs').addEventListener('click', e => {{
  if (!e.target.matches('.tab-btn')) return;
  document.querySelectorAll('#catTabs .tab-btn').forEach(b => b.classList.remove('active'));
  e.target.classList.add('active');
  renderCat(e.target.dataset.cat);
}});
renderCat(Object.keys(byCat).sort()[0]);
</script>"""

    return page_shell("Index Prețuri", "price-index.html", body, extra_scripts=scripts)


def gen_fuel(fuel_prices):
    """Fuel price leaderboard page."""
    # Group by fuel type
    by_fuel = {}
    for fp in fuel_prices:
        by_fuel.setdefault(fp["fuel"], []).append(fp)

    fuel_types = list(by_fuel.keys())

    # Tabs
    tabs = ""
    for i, ft in enumerate(fuel_types):
        cls = "tab-btn active" if i == 0 else "tab-btn"
        tabs += f'<button class="{cls}" data-fuel="{ft}">{ft}</button>'

    body = f"""
<div class="container">
  <h1>Carburanți — Clasament Prețuri</h1>
  <p class="subtitle">Prețuri medii pe rețea și tip de carburant (RON/litru)</p>

  <div class="card">
    <div class="tabs" id="fuelTabs">{tabs}</div>
    <div class="chart-box" style="height:320px;margin-bottom:20px"><canvas id="fuelChart"></canvas></div>
    <div class="table-wrap">
      <table id="fuelTable">
        <thead><tr><th>#</th><th>Rețea</th><th>Preț mediu</th><th>Min</th><th>Max</th><th>Stații</th></tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </div>
</div>"""

    scripts = f"""
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script>
const byFuel = {jdump(by_fuel)};
const fuelTypes = {jdump(fuel_types)};

const gasColors = {jdump(GAS_COLORS)};
function gColor(name) {{
  const u = name.toUpperCase();
  for (const [k,v] of Object.entries(gasColors)) {{ if (u.includes(k)) return v; }}
  return '#94a3b8';
}}

const chartCtx = document.getElementById('fuelChart');
let fuelChart = null;

function renderFuel(fuel) {{
  const data = byFuel[fuel] || [];
  if (fuelChart) fuelChart.destroy();
  fuelChart = new Chart(chartCtx, {{
    type: 'bar',
    data: {{
      labels: data.map(d => d.network),
      datasets: [{{
        data: data.map(d => d.avg_price),
        backgroundColor: data.map(d => gColor(d.network)),
        borderRadius: 4, barPercentage: 0.65,
      }}]
    }},
    options: {{
      indexAxis: 'y', responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ display: false }},
        tooltip: {{ callbacks: {{ label: c => c.parsed.x.toFixed(2) + ' RON' }} }}
      }},
      scales: {{
        x: {{ grid: {{ color: '#f1f5f9' }}, ticks: {{ callback: v => v.toFixed(2) }} }},
        y: {{ grid: {{ display: false }} }}
      }}
    }}
  }});

  // Table
  const tbody = document.querySelector('#fuelTable tbody');
  tbody.innerHTML = data.map((d, i) => {{
    const cheapest = i === 0;
    const style = cheapest ? 'font-weight:700;color:#16a34a' : '';
    return `<tr>
      <td>${{i+1}}</td>
      <td style="${{style}}">${{d.network}}</td>
      <td style="${{style}}">${{d.avg_price}} RON</td>
      <td>${{d.min_price}}</td><td>${{d.max_price}}</td>
      <td>${{d.stations}}</td>
    </tr>`;
  }}).join('');
}}

document.getElementById('fuelTabs').addEventListener('click', e => {{
  if (!e.target.matches('.tab-btn')) return;
  document.querySelectorAll('#fuelTabs .tab-btn').forEach(b => b.classList.remove('active'));
  e.target.classList.add('active');
  renderFuel(e.target.dataset.fuel);
}});
renderFuel(fuelTypes[0]);
</script>"""

    return page_shell("Carburanți", "fuel.html", body, extra_scripts=scripts)


def gen_pipeline(runs, coverage, summary):
    """Pipeline health & coverage page."""
    # Latest completed run
    completed = [r for r in runs if r["status"] == "completed"]
    last_run = completed[0] if completed else None

    # Run history rows
    run_rows = ""
    for r in runs:
        status_cls = {"completed": "badge-ok", "interrupted": "badge-warn",
                      "running": "badge-run"}.get(r["status"], "badge-err")
        run_rows += (
            f'<tr><td>{r["id"]}</td><td>{r["script"]}</td>'
            f'<td>{(r["started_at"] or "—")[:19]}</td>'
            f'<td>{(r["finished_at"] or "—")[:19]}</td>'
            f'<td><span class="badge {status_cls}">{r["status"]}</span></td>'
            f'<td>{r["uats_processed"] or "—"}</td>'
            f'<td>{r["records_written"] or "—"}</td></tr>'
        )

    # Coverage rows
    cov_rows = ""
    for c in coverage:
        total = c["total_stores"]
        covered = c["stores_with_prices"]
        pct = round(covered / total * 100) if total else 0
        color = "#22c55e" if pct >= 50 else "#eab308" if pct >= 20 else "#ef4444"
        cov_rows += (
            f'<tr><td style="font-weight:600">{c["network"]}</td>'
            f'<td>{covered} / {total}</td>'
            f'<td class="bar-cell">'
            f'<div class="bar-fill" style="width:{pct}%;background:{color}"></div>'
            f'<span class="bar-text">{pct}%</span></td>'
            f'<td>{c["products_covered"]:,}</td></tr>'
        )

    lr_records = last_run["records_written"] if last_run else "—"
    lr_uats = last_run["uats_processed"] if last_run else "—"
    lr_time = (last_run["finished_at"] or "—")[:19] if last_run else "—"

    body = f"""
<div class="container">
  <h1>Pipeline & Acoperire</h1>
  <p class="subtitle">Starea sistemului de colectare date</p>

  <div class="kpi-grid">
    <div class="kpi">
      <div class="kpi-value">{summary['prices']:,}</div>
      <div class="kpi-label">Total prețuri retail</div>
    </div>
    <div class="kpi">
      <div class="kpi-value">{summary['gas_prices']:,}</div>
      <div class="kpi-label">Total prețuri carburanți</div>
    </div>
    <div class="kpi">
      <div class="kpi-value">{lr_records}</div>
      <div class="kpi-label">Ultimul run (înregistrări)</div>
    </div>
    <div class="kpi">
      <div class="kpi-value">{lr_uats}</div>
      <div class="kpi-label">UAT-uri procesate (ultimul)</div>
    </div>
  </div>

  <div class="card">
    <div class="card-title">Acoperire pe rețea (ultima zi)</div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Rețea</th><th>Magazine cu prețuri</th><th>Acoperire</th><th>Produse</th></tr></thead>
        <tbody>{cov_rows}</tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <div class="card-title">Istoric rulări</div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>ID</th><th>Script</th><th>Start</th><th>Sfârșit</th><th>Status</th><th>UATs</th><th>Recs</th></tr></thead>
        <tbody>{run_rows}</tbody>
      </table>
    </div>
  </div>
</div>"""

    return page_shell("Pipeline", "pipeline.html", body)


def _fmt_date(d: str) -> str:
    """DD.MM.YYYY HH:MM -> DD.MM HH:MM for chart labels."""
    if not d:
        return d
    parts = d.split(" ")
    if len(parts) == 2:
        dm = ".".join(parts[0].split(".")[:2])
        return f"{dm} {parts[1]}"
    return d


def gen_trends(network_trends, category_trends, fuel_trends):
    """Time-series trend charts page."""
    all_dates = sorted(set(r["price_date"] for r in network_trends))
    n_dates = len(all_dates)
    display_dates = [_fmt_date(d) for d in all_dates]

    # Stable network order: alphabetical by median index (cheapest first)
    net_medians = {}
    for r in network_trends:
        net_medians.setdefault(r["network"], []).append(r["index_value"])
    all_networks = sorted(net_medians, key=lambda n: (sum(net_medians[n]) / len(net_medians[n])))

    all_cats = sorted(set(r["category"] for r in category_trends))
    all_fuel_types = sorted(set(r["fuel"] for r in fuel_trends))

    # Graceful degradation when data is thin
    not_enough = n_dates < 2

    if not_enough:
        body = """
<div class="container">
  <h1>Tendințe Prețuri</h1>
  <p class="subtitle">Evoluția prețurilor în timp — se actualizează zilnic</p>
  <div class="card" style="text-align:center;padding:60px 24px">
    <div style="font-size:48px;margin-bottom:16px">📊</div>
    <h2 style="color:var(--muted);font-weight:500;font-size:18px">Se colectează date</h2>
    <p style="color:var(--muted);margin-top:8px">
      Graficele de tendințe vor fi disponibile după acumularea câtorva zile de date.<br>
      Revino în curând!
    </p>
  </div>
</div>"""
        return page_shell("Tendințe", "trends.html", body)

    fuel_section = ""
    if fuel_trends:
        fuel_section = """
  <div class="card">
    <div class="card-title">Carburanți — Evoluție Prețuri (RON/L)</div>
    <div class="tabs" id="fuelTrendTabs"></div>
    <div class="chart-box" style="height:340px"><canvas id="fuelTrendChart"></canvas></div>
  </div>"""
    else:
        fuel_section = """
  <div class="card" style="opacity:.6">
    <div class="card-title">Carburanți — Evoluție Prețuri</div>
    <p style="color:var(--muted);font-size:13px">
      Date carburanți nu sunt încă disponibile în subset-ul CI. Vor apărea automat după configurarea pipeline-ului de gaz.
    </p>
  </div>"""

    body = f"""
<div class="container">
  <h1>Tendințe Prețuri</h1>
  <p class="subtitle">Evoluția prețurilor în timp — se actualizează zilnic</p>

  <div class="card">
    <div class="card-title">Index Prețuri pe Rețea în Timp</div>
    <p style="color:var(--muted);font-size:13px;margin-bottom:14px">
      100 = cea mai ieftină rețea din acea zi (produse comparabile disponibile în 3+ rețele)</p>
    <div class="chart-box" style="height:380px"><canvas id="networkTrendChart"></canvas></div>
  </div>

  <div class="card">
    <div class="card-title">Preț Mediu pe Categorie în Timp (RON)</div>
    <p style="color:var(--muted);font-size:13px;margin-bottom:14px">
      Prețul mediu al produselor per categorie</p>
    <div class="tabs" id="catTrendTabs"></div>
    <div class="chart-box" style="height:340px"><canvas id="catTrendChart"></canvas></div>
  </div>
{fuel_section}
</div>"""

    net_colors_js = {n: net_color(n) for n in all_networks}
    gas_colors_js = {r["network"]: net_color(r["network"], GAS_COLORS) for r in fuel_trends}

    scripts = f"""
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script>
const trendDates  = {jdump(display_dates)};
const allDates    = {jdump(all_dates)};
const netColors   = {jdump(net_colors_js)};
const gasColors   = {jdump(gas_colors_js)};
const allNetworks = {jdump(all_networks)};
const allCats     = {jdump(all_cats)};
const allFuelTypes= {jdump(all_fuel_types)};
const netTrendRaw = {jdump(network_trends)};
const catTrendRaw = {jdump(category_trends)};
const fuelTrendRaw= {jdump(fuel_trends)};

/* ── Network trend ───────────────────────────────────────────────── */
new Chart(document.getElementById('networkTrendChart'), {{
  type: 'line',
  data: {{
    labels: trendDates,
    datasets: allNetworks.map(net => {{
      const vals = allDates.map(d => {{
        const row = netTrendRaw.find(r => r.price_date===d && r.network===net);
        return row ? row.index_value : null;
      }});
      return {{
        label: net,
        data: vals,
        borderColor: netColors[net] || '#94a3b8',
        backgroundColor: (netColors[net] || '#94a3b8') + '20',
        tension: 0.3, spanGaps: true, pointRadius: 4,
        pointHoverRadius: 6, borderWidth: 2,
      }};
    }})
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    interaction: {{ mode: 'index', intersect: false }},
    plugins: {{
      legend: {{ position: 'bottom', labels: {{ boxWidth: 12, font: {{ size: 12 }} }} }},
      tooltip: {{ callbacks: {{ label: c => ` ${{c.dataset.label}}: ${{c.parsed.y}}` }} }}
    }},
    scales: {{
      x: {{ grid: {{ color: '#f1f5f9' }} }},
      y: {{
        grid: {{ color: '#f1f5f9' }},
        title: {{ display: true, text: 'Index (100 = cel mai ieftin)' }}
      }}
    }}
  }}
}});

/* ── Category trend ──────────────────────────────────────────────── */
const catCtx = document.getElementById('catTrendChart');
let catTrendChart = null;
function renderCatTrend(cat) {{
  const vals = allDates.map(d => {{
    const row = catTrendRaw.find(r => r.price_date===d && r.category===cat);
    return row ? row.avg_price : null;
  }});
  if (catTrendChart) catTrendChart.destroy();
  catTrendChart = new Chart(catCtx, {{
    type: 'line',
    data: {{
      labels: trendDates,
      datasets: [{{
        label: cat, data: vals,
        borderColor: '#2563eb', backgroundColor: '#2563eb20',
        tension: 0.3, spanGaps: true, pointRadius: 4, borderWidth: 2,
      }}]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ display: false }} }},
      scales: {{
        x: {{ grid: {{ color: '#f1f5f9' }} }},
        y: {{
          grid: {{ color: '#f1f5f9' }},
          ticks: {{ callback: v => v.toFixed(2) + ' RON' }},
          title: {{ display: true, text: 'Preț mediu (RON)' }}
        }}
      }}
    }}
  }});
}}
const catTabsEl = document.getElementById('catTrendTabs');
allCats.forEach((cat, i) => {{
  const btn = document.createElement('button');
  btn.className = 'tab-btn' + (i===0 ? ' active' : '');
  btn.dataset.cat = cat; btn.textContent = cat;
  catTabsEl.appendChild(btn);
}});
catTabsEl.addEventListener('click', e => {{
  if (!e.target.matches('.tab-btn')) return;
  catTabsEl.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  e.target.classList.add('active');
  renderCatTrend(e.target.dataset.cat);
}});
if (allCats.length) renderCatTrend(allCats[0]);

/* ── Fuel trend ──────────────────────────────────────────────────── */
const fuelTrendTabsEl = document.getElementById('fuelTrendTabs');
if (fuelTrendTabsEl && allFuelTypes.length) {{
  const fuelDates = [...new Set(fuelTrendRaw.map(r => r.price_date))].sort();
  const fuelDisplayDates = fuelDates.map(d => {{
    const p = d.split(' '); return p.length===2 ? p[0].split('.').slice(0,2).join('.')+' '+p[1] : d;
  }});
  const fuelNets = [...new Set(fuelTrendRaw.map(r => r.network))].sort();
  const fuelCtx = document.getElementById('fuelTrendChart');
  let fuelTrendChart = null;
  function renderFuelTrend(fuel) {{
    const datasets = fuelNets.map(net => {{
      const vals = fuelDates.map(d => {{
        const row = fuelTrendRaw.find(r => r.price_date===d && r.network===net && r.fuel===fuel);
        return row ? row.avg_price : null;
      }});
      return {{
        label: net, data: vals,
        borderColor: gasColors[net] || '#94a3b8',
        backgroundColor: (gasColors[net] || '#94a3b8') + '20',
        tension: 0.3, spanGaps: true, pointRadius: 4, borderWidth: 2,
      }};
    }});
    if (fuelTrendChart) fuelTrendChart.destroy();
    fuelTrendChart = new Chart(fuelCtx, {{
      type: 'line',
      data: {{ labels: fuelDisplayDates, datasets }},
      options: {{
        responsive: true, maintainAspectRatio: false,
        interaction: {{ mode: 'index', intersect: false }},
        plugins: {{
          legend: {{ position: 'bottom', labels: {{ boxWidth: 12, font: {{ size: 12 }} }} }},
          tooltip: {{ callbacks: {{ label: c => ` ${{c.dataset.label}}: ${{c.parsed.y?.toFixed(3)}} RON` }} }}
        }},
        scales: {{
          x: {{ grid: {{ color: '#f1f5f9' }} }},
          y: {{
            grid: {{ color: '#f1f5f9' }},
            ticks: {{ callback: v => v.toFixed(2) }},
            title: {{ display: true, text: 'RON/litru' }}
          }}
        }}
      }}
    }});
  }}
  allFuelTypes.forEach((ft, i) => {{
    const btn = document.createElement('button');
    btn.className = 'tab-btn' + (i===0 ? ' active' : '');
    btn.dataset.fuel = ft; btn.textContent = ft;
    fuelTrendTabsEl.appendChild(btn);
  }});
  fuelTrendTabsEl.addEventListener('click', e => {{
    if (!e.target.matches('.tab-btn')) return;
    fuelTrendTabsEl.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    e.target.classList.add('active');
    renderFuelTrend(e.target.dataset.fuel);
  }});
  renderFuelTrend(allFuelTypes[0]);
}}
</script>"""

    return page_shell("Tendințe", "trends.html", body, extra_scripts=scripts)


def gen_compare(compare_index):
    """Product price comparison page — data loaded on demand from per-product CSVs."""
    latest_date = compare_index.get("latest_date") or "—"
    products = compare_index.get("products", [])

    if not products:
        body = """
<div class="container">
  <h1>Comparare Produse</h1>
  <p class="subtitle">Prețuri comparate pe rețea</p>
  <div class="card" style="text-align:center;padding:60px 24px">
    <div style="font-size:48px;margin-bottom:16px">🔍</div>
    <h2 style="color:var(--muted);font-weight:500;font-size:18px">Nu există date disponibile</h2>
    <p style="color:var(--muted);margin-top:8px">Revino după prima rulare completă a pipeline-ului.</p>
  </div>
</div>"""
        return page_shell("Comparare", "compare.html", body)

    # Build dropdown HTML server-side (stable, no JS needed for the list)
    by_cat: dict = {}
    for p in products:
        by_cat.setdefault(p["cat"], []).append(p)

    options_html = ""
    for cat in sorted(by_cat.keys()):
        options_html += f'<optgroup label="{cat}">'
        for p in sorted(by_cat[cat], key=lambda x: x["name"]):
            options_html += f'<option value="{p["id"]}">{p["name"]}</option>'
        options_html += "</optgroup>"

    body = f"""
<div class="container">
  <h1>Comparare Produse</h1>
  <p class="subtitle">Prețuri comparate pe rețea — {latest_date}</p>

  <div class="card" style="margin-bottom:0">
    <div class="card-title">Selectează produsul</div>
    <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
      <select id="productSelect" style="flex:1;min-width:280px;max-width:620px;padding:9px 12px;
        border-radius:6px;border:1px solid var(--border);font-size:14px;
        background:var(--card);color:var(--text);cursor:pointer">
        {options_html}
      </select>
      <span id="loadingMsg" style="display:none;color:var(--muted);font-size:13px">Se încarcă…</span>
      <span id="errMsg" style="display:none;color:var(--danger);font-size:13px"></span>
    </div>
  </div>

  <div style="display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-top:24px">
    <div class="card">
      <div class="card-title" id="latestTitle">Prețuri pe rețea (ultima zi)</div>
      <div class="chart-box" style="height:320px"><canvas id="latestChart"></canvas></div>
    </div>
    <div class="card">
      <div class="card-title" id="trendTitle">Evoluție preț</div>
      <div class="chart-box" style="height:320px" id="trendBox">
        <canvas id="trendChart"></canvas>
      </div>
    </div>
  </div>

  <div class="card">
    <div class="card-title" id="tableTitle">Detalii pe rețea</div>
    <div class="table-wrap">
      <table id="compareTable">
        <thead>
          <tr><th>#</th><th>Rețea</th><th>Preț mediu</th><th>Min</th><th>Max</th><th>Magazine</th></tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
  </div>
</div>

<style>
@media (max-width:640px) {{
  #compareTable {{ display: block; overflow-x: auto; }}
  [style*="grid-template-columns:1fr 1fr"] {{ grid-template-columns: 1fr !important; }}
}}
</style>"""

    scripts = f"""
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script>
const netColors = {jdump(compare_index.get("net_colors", {}))};

/* ── CSV parser: two sections separated by a blank line ────────── */
function parseCSV(text) {{
  const parts = text.trim().split(/\\r?\\n\\r?\\n/);
  function parseSection(s) {{
    if (!s || !s.trim()) return [];
    const lines = s.trim().split(/\\r?\\n/);
    const keys  = lines[0].split(',');
    return lines.slice(1).filter(Boolean).map(line => {{
      const vals = line.split(',');
      return Object.fromEntries(keys.map((k, i) => [k, vals[i]]));
    }});
  }}
  return {{ latest: parseSection(parts[0] || ''), history: parseSection(parts[1] || '') }};
}}

/* ── Per-product fetch with in-memory cache ────────────────────── */
const cache = {{}};
async function loadProduct(pid) {{
  if (cache[pid]) return cache[pid];
  const res = await fetch(`data/products/${{pid}}.csv`);
  if (!res.ok) throw new Error(`HTTP ${{res.status}}`);
  cache[pid] = parseCSV(await res.text());
  return cache[pid];
}}

/* ── Chart helpers ─────────────────────────────────────────────── */
function fmtDate(d) {{
  if (!d) return d;
  const p = d.split(' ');
  return p.length === 2 ? p[0].split('.').slice(0,2).join('.') + ' ' + p[1] : d;
}}

let latestChart = null, trendChart = null;

function renderProduct(pid, name, data) {{
  const {{ latest, history }} = data;

  // Update titles
  document.getElementById('latestTitle').textContent = name + ' — prețuri pe rețea';
  document.getElementById('trendTitle').textContent  = name + ' — evoluție';
  document.getElementById('tableTitle').textContent  = name + ' — detalii pe rețea';

  /* Bar chart — latest prices (already sorted cheapest-first by server) */
  if (latestChart) latestChart.destroy();
  latestChart = new Chart(document.getElementById('latestChart'), {{
    type: 'bar',
    data: {{
      labels: latest.map(r => r.network),
      datasets: [{{
        label: 'Preț mediu (RON)',
        data: latest.map(r => +r.avg_price),
        backgroundColor: latest.map(r => netColors[r.network] || '#94a3b8'),
        borderRadius: 4, barPercentage: 0.7,
      }}]
    }},
    options: {{
      indexAxis: 'y', responsive: true, maintainAspectRatio: false,
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{ callbacks: {{ label: c => ' ' + c.parsed.x.toFixed(2) + ' RON' }} }}
      }},
      scales: {{
        x: {{ grid: {{ color: '#f1f5f9' }}, ticks: {{ callback: v => v.toFixed(2) }} }},
        y: {{ grid: {{ display: false }} }}
      }}
    }}
  }});

  /* Line chart — history */
  const allD  = [...new Set(history.map(r => r.price_date))].sort();
  const nets  = [...new Set(history.map(r => r.network))];
  const trendBox    = document.getElementById('trendBox');
  const trendCanvas = document.getElementById('trendChart');
  let noMsg = trendBox.querySelector('.no-trend');

  if (allD.length < 2) {{
    if (trendChart) {{ trendChart.destroy(); trendChart = null; }}
    trendCanvas.style.display = 'none';
    if (!noMsg) {{
      noMsg = document.createElement('div');
      noMsg.className = 'no-trend';
      noMsg.style.cssText = 'display:flex;align-items:center;justify-content:center;' +
        'height:100%;color:var(--muted);font-size:13px;text-align:center;padding:16px';
      trendBox.appendChild(noMsg);
    }}
    noMsg.textContent = 'Date insuficiente pentru tendință. Revino în câteva zile.';
  }} else {{
    trendCanvas.style.display = '';
    if (noMsg) noMsg.remove();
    if (trendChart) trendChart.destroy();
    trendChart = new Chart(trendCanvas, {{
      type: 'line',
      data: {{
        labels: allD.map(fmtDate),
        datasets: nets.map(net => {{
          const vals = allD.map(d => {{
            const r = history.find(r => r.network === net && r.price_date === d);
            return r ? +r.avg_price : null;
          }});
          return {{
            label: net, data: vals,
            borderColor: netColors[net] || '#94a3b8',
            backgroundColor: (netColors[net] || '#94a3b8') + '20',
            tension: 0.3, spanGaps: true, pointRadius: 4, borderWidth: 2,
          }};
        }})
      }},
      options: {{
        responsive: true, maintainAspectRatio: false,
        interaction: {{ mode: 'index', intersect: false }},
        plugins: {{
          legend: {{ position: 'bottom', labels: {{ boxWidth: 12, font: {{ size: 11 }} }} }},
          tooltip: {{ callbacks: {{
            label: c => ` ${{c.dataset.label}}: ${{c.parsed.y?.toFixed(2)}} RON`
          }} }}
        }},
        scales: {{
          x: {{ grid: {{ color: '#f1f5f9' }} }},
          y: {{
            grid: {{ color: '#f1f5f9' }},
            ticks: {{ callback: v => v.toFixed(2) }},
            title: {{ display: true, text: 'RON' }}
          }}
        }}
      }}
    }});
  }}

  /* Table */
  document.querySelector('#compareTable tbody').innerHTML = latest.map((r, i) => `
    <tr>
      <td>${{i+1}}</td>
      <td style="font-weight:${{i===0?700:400}};color:${{i===0?'var(--success)':''}}">
        ${{r.network}}</td>
      <td style="font-weight:${{i===0?700:400}};color:${{i===0?'var(--success)':''}}">
        ${{(+r.avg_price).toFixed(2)}} RON</td>
      <td>${{(+r.min_price).toFixed(2)}}</td>
      <td>${{(+r.max_price).toFixed(2)}}</td>
      <td>${{r.stores}}</td>
    </tr>`).join('');
}}

/* ── Dropdown handler ──────────────────────────────────────────── */
const sel     = document.getElementById('productSelect');
const loadMsg = document.getElementById('loadingMsg');
const errMsg  = document.getElementById('errMsg');

async function onSelect() {{
  const pid  = sel.value;
  const name = sel.options[sel.selectedIndex]?.text || '';
  loadMsg.style.display = '';
  errMsg.style.display  = 'none';
  sel.disabled = true;
  try {{
    const data = await loadProduct(pid);
    renderProduct(pid, name, data);
  }} catch(e) {{
    errMsg.textContent = 'Eroare la încărcare: ' + e.message;
    errMsg.style.display = '';
  }} finally {{
    loadMsg.style.display = 'none';
    sel.disabled = false;
  }}
}}

sel.addEventListener('change', onSelect);
if (sel.value) onSelect();
</script>"""

    return page_shell("Comparare", "compare.html", body, extra_scripts=scripts)


def gen_stores_map(stores):
    """Enhanced store map with network toggles."""
    # Build data
    network_counts = {}
    network_colors = {}
    for s in stores:
        net = s["network"] or "Unknown"
        network_counts[net] = network_counts.get(net, 0) + 1
        network_colors[net] = net_color(s["network"])

    order = sorted(network_counts, key=lambda n: (-network_counts[n], n == "Unknown", n))

    stores_json = []
    for s in stores:
        stores_json.append({
            "id": str(s["id"]), "name": s["name"], "addr": s["addr"] or "",
            "lat": s["lat"], "lon": s["lon"],
            "net": s["network"] or "Unknown", "c": net_color(s["network"]),
        })

    # Legend checkboxes
    legend_rows = ""
    for net in order:
        c = network_colors[net]
        legend_rows += (
            f'<label class="legend-row">'
            f'<input type="checkbox" checked data-net="{net}"> '
            f'<span class="dot" style="background:{c}"></span>'
            f'{net} <span class="cnt">({network_counts[net]})</span>'
            f'</label>'
        )

    return f"""<!DOCTYPE html>
<html lang="ro">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Hartă Magazine — Monitorul Prețurilor</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css"/>
<link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css"/>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
  #map {{ width: 100vw; height: 100vh; }}

  .top-bar {{ position: absolute; top: 0; left: 0; right: 0; z-index: 1000;
    background: rgba(15,23,42,.92); color: #fff; padding: 0 16px;
    display: flex; align-items: center; gap: 0; backdrop-filter: blur(8px); }}
  .top-bar .brand {{ font-weight: 700; font-size: 14px; padding: 10px 14px 10px 0;
    border-right: 1px solid rgba(255,255,255,.15); margin-right: 8px; }}
  .top-bar a {{ color: rgba(255,255,255,.7); padding: 10px 14px; font-size: 13px;
    text-decoration: none; font-weight: 500; }}
  .top-bar a:hover {{ color: #fff; }}
  .top-bar .count {{ margin-left: auto; font-size: 12px; color: rgba(255,255,255,.6);
    padding: 10px 0; }}

  #legend {{
    position: absolute; bottom: 30px; right: 10px; z-index: 1000;
    background: rgba(255,255,255,0.95); border-radius: 8px;
    padding: 12px 14px; box-shadow: 0 2px 8px rgba(0,0,0,.2);
    font-size: 12.5px; line-height: 1.5; max-height: 70vh; overflow-y: auto;
  }}
  #legend h4 {{ margin-bottom: 8px; font-size: 13px; font-weight: 700; }}
  .legend-row {{ display: flex; align-items: center; gap: 5px; cursor: pointer;
    padding: 1px 0; }}
  .legend-row input {{ margin: 0; cursor: pointer; }}
  .dot {{ width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }}
  .cnt {{ color: #777; font-size: 11px; }}
</style>
</head>
<body>
<div class="top-bar">
  <span class="brand">Monitorul Prețurilor</span>
  <a href="index.html">Dashboard</a>
  <a href="price-index.html">Index</a>
  <a href="trends.html">Tendințe</a>
  <a href="compare.html">Comparare</a>
  <a href="fuel.html">Carburanți</a>
  <a href="pipeline.html">Pipeline</a>
  <span class="count" id="visibleCount">{len(stores)} magazine</span>
</div>
<div id="map"></div>
<div id="legend">
  <h4>Rețele</h4>
  {legend_rows}
</div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js"></script>
<script>
const stores = {json.dumps(stores_json, ensure_ascii=False, separators=(",", ":"))};

const map = L.map('map', {{ zoomControl: false }}).setView([45.9, 24.97], 7);
L.control.zoom({{ position: 'bottomleft' }}).addTo(map);
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  attribution: '&copy; OpenStreetMap', maxZoom: 19
}}).addTo(map);

function circleIcon(color) {{
  return L.divIcon({{
    className: '',
    html: `<svg width="14" height="14" viewBox="0 0 14 14">
      <circle cx="7" cy="7" r="6" fill="${{color}}" stroke="#fff" stroke-width="1.5"/>
    </svg>`,
    iconSize: [14, 14], iconAnchor: [7, 7], popupAnchor: [0, -8]
  }});
}}

/* Build per-network marker layers */
const netLayers = {{}};
const clusters = L.markerClusterGroup({{ maxClusterRadius: 40 }});

for (const s of stores) {{
  const marker = L.marker([s.lat, s.lon], {{ icon: circleIcon(s.c) }});
  const addr = s.addr ? `<div style="color:#555;font-size:12px">${{s.addr}}</div>` : '';
  const netLabel = s.net !== 'Unknown'
    ? `<div style="margin-top:3px;font-size:12px;font-weight:600;color:${{s.c}}">${{s.net}}</div>`
    : '';
  marker.bindPopup(`<b>${{s.name}}</b>${{netLabel}}${{addr}}`, {{ maxWidth: 260 }});
  marker._netName = s.net;
  if (!netLayers[s.net]) netLayers[s.net] = [];
  netLayers[s.net].push(marker);
  clusters.addLayer(marker);
}}
map.addLayer(clusters);

/* Filter by network checkboxes */
function updateFilters() {{
  const checked = new Set();
  document.querySelectorAll('#legend input[type=checkbox]').forEach(cb => {{
    if (cb.checked) checked.add(cb.dataset.net);
  }});
  clusters.clearLayers();
  let count = 0;
  for (const [net, markers] of Object.entries(netLayers)) {{
    if (checked.has(net)) {{
      markers.forEach(m => clusters.addLayer(m));
      count += markers.length;
    }}
  }}
  document.getElementById('visibleCount').textContent = count.toLocaleString('ro') + ' magazine';
}}
document.getElementById('legend').addEventListener('change', updateFilters);
</script>
</body>
</html>"""


# ── Main ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate static site from prices.db")
    parser.add_argument("--db",  default=str(DB_PATH),  help="Path to prices.db")
    parser.add_argument("--out", default=str(OUT_DIR),   help="Output directory")
    args = parser.parse_args()

    db_path = Path(args.db)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)

    print("Loading data...")
    summary         = load_summary(conn)
    price_index     = load_price_index(conn)
    by_category     = load_price_index_by_category(conn)
    fuel_prices     = load_fuel_prices(conn)
    runs            = load_runs(conn)
    coverage        = load_coverage(conn)
    network_trends  = load_network_trends(conn)
    category_trends = load_category_trends(conn)
    fuel_trends     = load_fuel_trends(conn)
    compare_index   = load_compare_index(conn)
    stores          = load_stores(conn)

    print("Building compare data files...")
    n_products = build_compare_data_files(conn, out_dir)
    print(f"  data/products/   {n_products} CSV files")

    conn.close()

    pages = {
        "index.html":       gen_index(summary, price_index, fuel_prices),
        "price-index.html": gen_price_index(price_index, by_category),
        "trends.html":      gen_trends(network_trends, category_trends, fuel_trends),
        "compare.html":     gen_compare(compare_index),
        "fuel.html":        gen_fuel(fuel_prices),
        "pipeline.html":    gen_pipeline(runs, coverage, summary),
        "stores_map.html":  gen_stores_map(stores),
    }

    for name, html in pages.items():
        path = out_dir / name
        path.write_text(html, encoding="utf-8")
        size_kb = len(html.encode("utf-8")) / 1024
        print(f"  {name:<20} {size_kb:6.0f} KB")

    total_kb = sum(len(h.encode("utf-8")) for h in pages.values()) / 1024
    print(f"\nTotal: {total_kb:.0f} KB across {len(pages)} pages")


if __name__ == "__main__":
    main()
