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
    """Average fuel price per network and product type per day.

    price_date from the API is a timestamp (e.g. '16/04/2026 14:16');
    truncate to 10 chars to group by calendar day.
    """
    return query(conn, """
        SELECT SUBSTR(gp.price_date, 1, 10) AS price_date,
               n.name AS network, gp2.name AS fuel,
               ROUND(AVG(gp.price), 3) AS avg_price
        FROM gas_prices gp
        JOIN gas_stations gs ON gp.station_id = gs.id
        JOIN gas_networks n ON gs.network_id = n.id
        JOIN gas_products gp2 ON gp.product_id = gp2.id
        WHERE gp.price > 0
          AND gp.price_date IS NOT NULL AND gp.price_date != ''
        GROUP BY SUBSTR(gp.price_date, 1, 10), n.id, gp2.id
        ORDER BY gp2.name, SUBSTR(gp.price_date, 1, 10), n.name
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


def load_analytics_data(conn):
    """Load all analytics view data for the analytics page."""
    return {
        "price_variability": query(conn, "SELECT * FROM v_price_variability"),
        "cross_network":     query(conn, "SELECT * FROM v_cross_network_spread LIMIT 500"),
        "popular_products":  query(conn, "SELECT * FROM v_product_popularity LIMIT 200"),
        "private_labels":    query(conn, "SELECT * FROM v_private_label_candidates LIMIT 100"),
        "stores_per_network": query(conn, "SELECT * FROM v_stores_per_network"),
        "price_freshness":   query(conn, "SELECT * FROM v_price_freshness LIMIT 30"),
        "products_no_prices": query(conn, "SELECT * FROM v_products_no_prices"),
    }


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


def load_gas_map_data(conn):
    """Gas stations with latest prices per fuel type for map."""
    rows = query(conn, """
        SELECT gs.id, gs.name, gs.addr, gs.lat, gs.lon,
               gn.name AS network,
               gpr.name AS fuel, gp.price, gp.price_date
        FROM gas_stations gs
        JOIN gas_networks gn ON gs.network_id = gn.id
        JOIN gas_prices gp ON gp.station_id = gs.id
        JOIN gas_products gpr ON gp.product_id = gpr.id
        WHERE (gp.product_id, gp.station_id, gp.price_date) IN (
            SELECT product_id, station_id, MAX(price_date)
            FROM gas_prices GROUP BY product_id, station_id
        )
        AND gs.lat IS NOT NULL AND gs.lon IS NOT NULL
        AND gs.lat != 0 AND gs.lon != 0
        ORDER BY gn.name, gs.name
    """)
    # Pivot into per-station dicts
    stations = {}
    for r in rows:
        sid = r["id"]
        if sid not in stations:
            stations[sid] = {
                "id": sid, "name": r["name"], "addr": r["addr"] or "",
                "lat": r["lat"], "lon": r["lon"],
                "network": r["network"], "prices": {}, "price_date": "",
            }
        stations[sid]["prices"][r["fuel"]] = r["price"]
        if r["price_date"] > stations[sid]["price_date"]:
            stations[sid]["price_date"] = r["price_date"]
    return list(stations.values())


def load_metodologie_stats(conn):
    """Extended stats for the Trust & Methodology page."""
    dates = [r["d"] for r in query(conn, "SELECT DISTINCT price_date AS d FROM prices ORDER BY d")]
    gas_dates = [r["d"] for r in query(conn, "SELECT DISTINCT price_date AS d FROM gas_prices ORDER BY d")]
    return {
        "price_dates": len(dates),
        "earliest_retail": dates[0] if dates else "—",
        "latest_retail": dates[-1] if dates else "—",
        "latest_gas": gas_dates[-1] if gas_dates else "—",
        "gas_dates": len(gas_dates),
        "stores_no_network": query(conn, "SELECT COUNT(*) AS n FROM stores WHERE network_id IS NULL")[0]["n"],
        "products_no_price_today": query(conn, """
            SELECT COUNT(*) AS n FROM products p
            WHERE NOT EXISTS (
              SELECT 1 FROM prices pr
              WHERE pr.product_id=p.id AND pr.price_date=(SELECT MAX(price_date) FROM prices)
            )
        """)[0]["n"],
        "uats_with_stores": query(conn, "SELECT COUNT(DISTINCT uat_id) AS n FROM stores WHERE uat_id IS NOT NULL")[0]["n"],
    }


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
    ("index.html",         "Acasă"),
    ("povesti.html",       "Povești"),
    ("tablou.html",        "Tablou"),
    ("__sep__",            ""),
    ("aproape.html",       "Aproape"),
    ("cos.html",           "Coșul"),
    ("harta.html",         "Hartă"),
    ("anomalii.html",      "Anomalii"),
    ("compare.html",       "Comparare"),
    ("fuel.html",          "Carburanți"),
    ("__sep__",            ""),
    ("date-deschise.html", "Date"),
    ("metodologie.html",   "Metodologie"),
]

# ── Romanian date helper ───────────────────────────────────────────────
_RO_DAYS   = ["luni", "marți", "miercuri", "joi", "vineri", "sâmbătă", "duminică"]
_RO_MONTHS = ["ianuarie", "februarie", "martie", "aprilie", "mai", "iunie",
              "iulie", "august", "septembrie", "octombrie", "noiembrie", "decembrie"]

def date_ro(d=None) -> str:
    import datetime
    if d is None:
        d = datetime.date.today()
    elif isinstance(d, str):
        d = datetime.date.fromisoformat(d)
    return f"{_RO_DAYS[d.weekday()]}, {d.day} {_RO_MONTHS[d.month - 1]} {d.year}"


def nav_html(active_page: str) -> str:
    parts = []
    for href, label in NAV_ITEMS:
        if href == "__sep__":
            parts.append('<span class="sep" aria-hidden="true"></span>')
            continue
        cls = ' class="active" aria-current="page"' if href == active_page else ""
        parts.append(f'<a href="{href}"{cls}>{label}</a>')
    return (
        '<nav class="nav" aria-label="Navigare principală">'
        + "".join(parts) +
        '</nav>'
    )


def _masthead(active_page: str) -> str:
    return f"""<header class="masthead">
  <div class="masthead-row">
    <a href="index.html" class="wordmark" aria-label="Monitorul Prețurilor — acasă">
      <span class="mark" aria-hidden="true"></span>Monitorul Prețurilor<sup>+</sup>
    </a>
    <span class="dateline"><span class="num">{date_ro()}</span><span class="dot">·</span>ediția zilei</span>
  </div>
  {nav_html(active_page)}
</header>"""


def _disclaimer() -> str:
    return """<div class="disclaimer" id="disclaimer">
  Acest site nu este un proiect oficial al Guvernului României. Date publice preluate de pe <a href="https://monitorulpreturilor.info/">monitorulpreturilor.info</a> (Consiliul Concurenței).
  <button class="close" type="button" aria-label="Închide" onclick="this.parentElement.style.display='none';try{localStorage.setItem('mp-dis','1')}catch(e){}">×</button>
</div>
<script>try{if(localStorage.getItem('mp-dis')==='1'){var d=document.getElementById('disclaimer');if(d)d.style.display='none';}}catch(e){}</script>"""


def _footer() -> str:
    return """<footer class="footer">
  <div class="footer-inner">
    <div class="about">
      <a href="index.html" class="wordmark"><span class="mark" aria-hidden="true"></span>Monitorul Prețurilor<sup>+</sup></a>
      <p>Monitorizare independentă a prețurilor de consum din România. Date publice, colectate zilnic, prezentate fără intermediari.</p>
    </div>
    <div>
      <h4>Citește</h4>
      <ul>
        <li><a href="index.html">Buletin</a></li>
        <li><a href="povesti.html">Povești</a></li>
        <li><a href="inflatie.html">Inflație</a></li>
      </ul>
    </div>
    <div>
      <h4>Instrumente</h4>
      <ul>
        <li><a href="aproape.html">Aproape de tine</a></li>
        <li><a href="cos.html">Coșul</a></li>
        <li><a href="harta.html">Hartă costuri</a></li>
        <li><a href="anomalii.html">Anomalii</a></li>
        <li><a href="categorii.html">Categorii</a></li>
        <li><a href="compare.html">Comparare</a></li>
        <li><a href="trends.html">Tendințe</a></li>
        <li><a href="fuel.html">Carburanți</a></li>
        <li><a href="stores_map.html">Hartă magazine</a></li>
        <li><a href="gas_map.html">Hartă carburanți</a></li>
        <li><a href="price-index.html">Index prețuri</a></li>
      </ul>
    </div>
    <div>
      <h4>Transparență</h4>
      <ul>
        <li><a href="tablou.html">Tablou de bord</a></li>
        <li><a href="date-deschise.html">Date deschise</a></li>
        <li><a href="metodologie.html">Metodologie</a></li>
        <li><a href="pipeline.html">Status pipeline</a></li>
        <li><a href="analytics.html">Analiză date</a></li>
        <li><a href="https://github.com/gov2-ro/monitorulpreturilor" rel="nofollow">Cod sursă</a></li>
      </ul>
    </div>
    <div class="credit">
      <span>Date sub <a href="https://creativecommons.org/licenses/by/4.0/" rel="nofollow">CC BY 4.0</a> · Cod sub licența MIT</span>
      <span>Actualizat automat zilnic · <a href="https://github.com/gov2-ro/monitorulpreturilor" rel="nofollow">contribuie</a></span>
    </div>
  </div>
</footer>"""


FONTS_HEAD = (
    '<link rel="preconnect" href="https://fonts.googleapis.com"/>'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>'
    '<link href="https://fonts.googleapis.com/css2?'
    'family=Fraunces:ital,opsz,wght,SOFT@0,9..144,300..700,0..100&'
    'family=IBM+Plex+Sans:wght@300;400;500;600;700&'
    'family=IBM+Plex+Mono:wght@400;500;600&'
    'display=swap" rel="stylesheet"/>'
)


def page_shell(title: str, active_page: str, body: str, extra_head: str = "",
               extra_scripts: str = "") -> str:
    return f"""<!DOCTYPE html>
<html lang="ro">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{title} — Monitorul Prețurilor</title>
<meta name="description" content="Monitorul prețurilor din România — date publice, comparații între rețele, analize zilnice."/>
{FONTS_HEAD}
<link rel="stylesheet" href="assets/app.css"/>
<link rel="icon" type="image/svg+xml" href="assets/logo.svg"/>
{extra_head}
</head>
<body>
<a href="#main" class="skip">Sări la conținut</a>
{_disclaimer()}
{_masthead(active_page)}
<main id="main">
{body}
</main>
{_footer()}
{extra_scripts}
</body>
</html>"""


def jdump(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


# ── Page generators ─────────────────────────────────────────────────────

def gen_tablou(summary, price_index, fuel_prices, fuel_trends):
    """Tablou de bord — dashboard for power users (formerly the homepage)."""
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
  <span class="eyebrow accent">Tablou de bord</span>
  <h1 style="font-family:var(--font-display);font-size:var(--step-4);margin:var(--s-3) 0 var(--s-2);letter-spacing:-0.02em;line-height:1.1">Toate datele, dintr-o privire</h1>
  <p class="subtitle" style="color:var(--ink-soft);max-width:var(--measure-read);margin-bottom:var(--s-6)">Indexul de preț al rețelelor retail, carburanții, și starea colectării. Date publice publicate de <a href="https://www.consiliulconcurentei.ro/" target="_blank" rel="nofollow">Consiliul Concurenței</a> prin <a href="https://monitorulpreturilor.info/" target="_blank" rel="nofollow">monitorulpreturilor.info</a>.</p>

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

    {"" if not fuel_trends else '''
    <div class="card" style="grid-column: 1 / -1;">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
        <span class="card-title" style="margin:0">Carburanți — Evoluție Prețuri (RON)</span>
        <div class="tabs" id="dashFuelTabs" style="margin:0"></div>
      </div>
      <div class="chart-box" style="height:300px"><canvas id="dashFuelChart"></canvas></div>
    </div>'''}
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

// ── Fuel trend chart ────────────────────────────────────────────────────
const fuelTrendRawDash = {jdump(fuel_trends)};
if (fuelTrendRawDash.length) {{
  const dashFuelTypes = [...new Set(fuelTrendRawDash.map(r => r.fuel))].sort();
  const dashFuelDates = [...new Set(fuelTrendRawDash.map(r => r.price_date))].sort();
  const dashFuelNets  = [...new Set(fuelTrendRawDash.map(r => r.network))].sort();
  const gasColsDash = {jdump({n: net_color(n, GAS_COLORS) for n in set(r["network"] for r in fuel_trends)})};

  const tabsEl = document.getElementById('dashFuelTabs');
  let dashFuelChart = null;

  function renderDashFuel(fuel) {{
    const datasets = dashFuelNets.map(net => {{
      const vals = dashFuelDates.map(d => {{
        const r = fuelTrendRawDash.find(x => x.price_date===d && x.network===net && x.fuel===fuel);
        return r ? r.avg_price : null;
      }});
      return {{
        label: net, data: vals, borderColor: gasColsDash[net] || '#94a3b8',
        backgroundColor: 'transparent', borderWidth: 2,
        pointRadius: 3, spanGaps: true, tension: 0.3,
      }};
    }});
    if (dashFuelChart) dashFuelChart.destroy();
    dashFuelChart = new Chart(document.getElementById('dashFuelChart'), {{
      type: 'line',
      data: {{ labels: dashFuelDates, datasets }},
      options: {{
        responsive: true, maintainAspectRatio: false,
        plugins: {{
          legend: {{ position: 'bottom', labels: {{ boxWidth: 12, font: {{ size: 11 }} }} }},
          tooltip: {{ callbacks: {{ label: c => c.dataset.label + ': ' + c.parsed.y?.toFixed(2) + ' RON' }} }}
        }},
        scales: {{
          x: {{ grid: {{ color: '#f1f5f9' }}, ticks: {{ font: {{ size: 11 }} }} }},
          y: {{ grid: {{ color: '#f1f5f9' }}, ticks: {{ callback: v => v.toFixed(2) }} }}
        }}
      }}
    }});
  }}

  dashFuelTypes.forEach((fuel, i) => {{
    const btn = document.createElement('button');
    btn.className = i === 0 ? 'tab-btn active' : 'tab-btn';
    btn.textContent = fuel;
    btn.onclick = () => {{
      tabsEl.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      renderDashFuel(fuel);
    }};
    tabsEl.appendChild(btn);
  }});
  renderDashFuel(dashFuelTypes[0]);
}}
</script>"""

    return page_shell("Tablou de bord", "tablou.html", body, extra_scripts=scripts)


def gen_index(summary, price_index, fuel_prices, fuel_trends):
    """Buletinul prețurilor — editorial homepage."""
    import datetime

    # ── Lead: cheapest vs. most expensive network ──────────────────────────
    if price_index:
        cheapest  = price_index[0]   # price_index sorted asc
        priciest  = price_index[-1]
        spread_pct = round(priciest["price_index"] - 100)
        n_products = cheapest["products"]
        n_networks = len(price_index)
        headline = (
            f'La <span class="num">{cheapest["network"]}</span>, '
            f'coșul costă cu '
            f'<strong style="color:var(--accent)" class="num">{spread_pct}%</strong> '
            f'mai puțin decât la {priciest["network"]}'
        )
        deck = (
            f'Comparăm {n_networks} rețele retail pe {n_products:,} produse '
            f'disponibile simultan. Indexul de preț arată abaterea față de cea mai ieftină rețea — '
            f'cu cât e mai mare, cu atât plătești mai mult pentru același coș.'
        )
        eyebrow_cat  = "INDEX PREȚURI REȚELE"
        cheapest_net = cheapest["network"]
        priciest_net = priciest["network"]
    else:
        headline     = "Monitorul prețurilor din România"
        deck         = "Date publice despre prețurile produselor alimentare și carburanților, colectate zilnic."
        eyebrow_cat  = "BULETIN ZILNIC"
        spread_pct   = 0
        cheapest_net = ""
        priciest_net = ""
        n_networks   = 0
        n_products   = 0

    latest_date = summary.get("latest_retail") or datetime.date.today().isoformat()

    # ── Stat tiles ─────────────────────────────────────────────────────────
    stat_tiles = [
        (cheapest_net or "—", "Cea mai ieftină rețea", "accent", None),
        (f'{summary["stores"]:,}', "Magazine monitorizate", "", None),
        (f'{summary["prices"]:,}', "Prețuri colectate", "", None),
        (f'{summary["uats"]:,}', "UAT-uri acoperite", "", None),
    ] if summary.get("uats") else [
        (cheapest_net or "—", "Cea mai ieftină rețea", "accent", None),
        (f'{summary["stores"]:,}', "Magazine monitorizate", "", None),
        (f'{summary["prices"]:,}', "Prețuri colectate", "", None),
        (f'{n_networks}', "Rețele comparate", "", None),
    ]

    stats_html = ""
    for val, lbl, extra_cls, sub in stat_tiles:
        val_cls = f"val {extra_cls}".strip()
        sub_html = f'<span class="sub">{sub}</span>' if sub else ""
        stats_html += f"""<div class="stat reveal">
  <div class="{val_cls}">{val}</div>
  <div class="lbl">{lbl}</div>{sub_html}
</div>"""

    # ── Hero chart: price index bars ───────────────────────────────────────
    # Build spread-chart rows (CSS grid, no canvas for hero)
    if price_index:
        max_idx = price_index[-1]["price_index"]
        min_idx = 100.0
        rng     = max(max_idx - min_idx, 1)
        spread_rows = ""
        for row in price_index:
            pct  = (row["price_index"] - min_idx) / rng * 100
            is_cheap = (row["network"] == cheapest_net)
            mark_cls  = "mark cheap" if is_cheap else "mark"
            spread_rows += f"""<div class="row">
  <div class="n">{row["network"]}</div>
  <div class="bar" style="--pct:{pct:.1f}%"><div class="{mark_cls}" style="left:{pct:.1f}%"></div></div>
  <div class="v">{row["price_index"]}</div>
</div>"""
        hero_chart = f"""<div class="spread-chart">{spread_rows}</div>
<p style="font-size:var(--step--1);color:var(--ink-softer);margin-top:var(--s-4)">100 = cea mai ieftină rețea · produse prezente în cel puțin 3 rețele · date din {latest_date}</p>"""
    else:
        hero_chart = ""

    # ── Story cards (linking to key tools as if editorial stories) ─────────
    story_cards = [
        ("COȘ DE CUMPĂRĂTURI", "Unde e cel mai ieftin coș complet?",
         "Comparăm coșul standard în toate rețelele retail din România.",
         "cos.html"),
        ("HARTĂ COSTURI", "Cum variază prețurile pe județe?",
         "Choropleth interactiv: costul mediu pe UAT pentru produse de bază.",
         "harta.html"),
        ("ANOMALII", "Ce produse au avut salturi de preț azi?",
         "Detectăm automat creșteri și scăderi bruște față de ziua precedentă.",
         "anomalii.html"),
    ]
    stories_html = ""
    for eyebrow, title, desc, href in story_cards:
        stories_html += f"""<a class="story reveal" href="{href}">
  <span class="eyebrow">{eyebrow}</span>
  <h3>{title}</h3>
  <p>{desc}</p>
  <span class="more">Explorează →</span>
</a>"""

    # ── Tool grid ──────────────────────────────────────────────────────────
    tools = [
        ("aproape.html",  "Retail",     "Aproape de tine",  "Magazine ieftine lângă tine"),
        ("cos.html",      "Retail",     "Coșul",            "Cel mai ieftin coș per rețea"),
        ("harta.html",    "Analiză",    "Hartă costuri",    "Variație geografică a prețurilor"),
        ("anomalii.html", "Analiză",    "Anomalii",         "Salturi bruște de preț azi"),
        ("compare.html",  "Instrumente","Comparare",        "Prețul unui produs în toate rețelele"),
        ("fuel.html",     "Carburanți", "Carburanți",       "Prețuri medii pe rețea și tip"),
    ]
    tools_html = ""
    for href, cat, name, desc in tools:
        tools_html += f"""<a class="tool reveal" href="{href}">
  <div class="k">{cat}</div>
  <div class="t">{name}</div>
  <div class="d">{desc}</div>
  <div class="arrow">→</div>
</a>"""

    # ── Compact strip ──────────────────────────────────────────────────────
    strip_html = f"""<div class="strip reveal">
  <div class="item"><div class="v">{summary['stores']:,}</div><div class="l">magazine</div></div>
  <div class="item"><div class="v">{summary['prices']:,}</div><div class="l">prețuri retail</div></div>
  <div class="item"><div class="v">{summary.get('gas_stations', 0):,}</div><div class="l">benzinării</div></div>
  <div class="item"><div class="v">{summary.get('gas_prices', 0):,}</div><div class="l">prețuri carburanți</div></div>
  <div class="spacer"></div>
  <a class="link" href="tablou.html">Tablou complet →</a>
</div>"""

    body = f"""
<div class="container">
  <article class="lede reveal">
    <span class="eyebrow accent">{eyebrow_cat} · {latest_date}</span>
    <h1>{headline}</h1>
    <p class="deck drop">{deck}</p>

    <div class="chart-block">
      <div class="title">Index de preț per rețea retail</div>
      {hero_chart}
      <div class="foot">
        <span class="label">Sursă</span>
        <a href="https://monitorulpreturilor.info/" rel="nofollow">monitorulpreturilor.info</a>
        · <a href="metodologie.html">Metodologie</a>
        · <a href="price-index.html">Date complete →</a>
      </div>
    </div>
  </article>

  <section aria-labelledby="s01">
    <div class="section-title">
      <h2 id="s01">În numere</h2>
    </div>
    <div class="stats">{stats_html}</div>
  </section>

  <section aria-labelledby="s02">
    <div class="section-title">
      <h2 id="s02">Povești</h2>
      <div class="after"><a href="povesti.html">toate →</a></div>
    </div>
    <div class="story-grid">{stories_html}</div>
  </section>

  <section aria-labelledby="s03">
    <div class="section-title">
      <h2 id="s03">Instrumente</h2>
    </div>
    <div class="tool-grid">{tools_html}</div>
  </section>

  <section aria-labelledby="s04">
    <div class="section-title">
      <h2 id="s04">Tablou rapid</h2>
    </div>
    {strip_html}
  </section>
</div>"""

    return page_shell("Buletinul prețurilor", "index.html", body)


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
        <thead><tr><th>#</th><th>Rețea</th><th>Preț mediu</th><th>Min</th><th>Max</th><th>Diferență</th><th>Stații</th></tr></thead>
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
      <td style="color:var(--muted)">${{(d.max_price - d.min_price).toFixed(2)}}</td>
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


def gen_analytics(data):
    """Analytics page — sortable tables for all analytical views."""

    TABS = [
        ("price_variability",  "Variabilitate prețuri",    "Spread intra-rețea pe produs (outlier-filtrat, ultima dată)",                   "price_variability.csv"),
        ("cross_network",      "Diferențe inter-rețea",    "Raport preț max/min între rețele per produs (excl. SELGROS, min. 2 rețele)",     "cross_network_spread.csv"),
        ("popular_products",   "Produse populare",         "Top 200 produse după acoperire în magazine și număr de înregistrări",            "popular_products.csv"),
        ("private_labels",     "Mărci proprii",            "Produse găsite într-o singură rețea (candidați mărci proprii)",                  "private_labels.csv"),
        ("stores_per_network", "Magazine per rețea",       "Numărul total de magazine per lanț de retail",                                   "stores_per_network.csv"),
        ("price_freshness",    "Prospețime date",          "Numărul de înregistrări, magazine și produse per dată de preț",                  "price_freshness.csv"),
        ("products_no_prices", "Produse fără prețuri",     "Produse din catalog care nu au nicio înregistrare de preț",                      "products_no_prices.csv"),
    ]

    tab_buttons = ""
    for i, (key, label, _desc, _csv) in enumerate(TABS):
        cls = "tab-btn active" if i == 0 else "tab-btn"
        tab_buttons += f'<button class="{cls}" data-tab="{key}">{label}</button>'

    body = f"""
<div class="container">
  <h1>Analiză Date</h1>
  <p class="subtitle">Tabele sortabile din interogările analitice — actualizate zilnic</p>

  <div class="card">
    <div class="tabs" id="analyticsTabs" style="margin-bottom:12px">{tab_buttons}</div>
    <div id="analyticsDesc" style="color:var(--muted);font-size:13px;margin-bottom:14px"></div>
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
      <span id="analyticsCount" style="font-size:13px;color:var(--muted)"></span>
      <a id="csvLink" href="#" download
         style="font-size:13px;padding:5px 12px;border-radius:6px;border:1px solid var(--border);
                color:var(--primary);background:var(--card);text-decoration:none">
        ↓ CSV
      </a>
    </div>
    <div class="table-wrap">
      <table id="analyticsTable">
        <thead id="analyticsHead"></thead>
        <tbody id="analyticsBody"></tbody>
      </table>
    </div>
  </div>
</div>"""

    tabs_meta = [{"key": key, "desc": desc, "csv": csv}
                 for key, _label, desc, csv in TABS]
    scripts = f"""
<script>
const analyticsData = {jdump({k: v for k, v in data.items()})};
const tabsMeta = {jdump(tabs_meta)};

/* ── Sorting state ─────────────────────────────────────────────────── */
let sortCol = -1, sortAsc = true;

function renderTable(key) {{
  const rows = analyticsData[key] || [];
  const meta = tabsMeta.find(t => t.key === key);
  if (!rows.length) {{
    document.getElementById('analyticsHead').innerHTML = '';
    document.getElementById('analyticsBody').innerHTML =
      '<tr><td colspan="99" style="text-align:center;color:var(--muted);padding:32px">Nu există date.</td></tr>';
    document.getElementById('analyticsCount').textContent = '0 rânduri';
    document.getElementById('analyticsDesc').textContent = meta?.desc || '';
    document.getElementById('csvLink').href = 'data/' + (meta?.csv || '');
    return;
  }}

  const cols = Object.keys(rows[0]);
  sortCol = -1; sortAsc = true;

  document.getElementById('analyticsDesc').textContent = meta?.desc || '';
  document.getElementById('csvLink').href = 'data/' + (meta?.csv || '');

  // Header
  document.getElementById('analyticsHead').innerHTML =
    '<tr>' + cols.map((c, i) =>
      `<th style="cursor:pointer;user-select:none" data-col="${{i}}"
           title="Click to sort">${{c}} <span class="sort-icon"></span></th>`
    ).join('') + '</tr>';

  // Detect numeric columns
  const isNum = cols.map(c => rows.every(r => r[c] === null || r[c] === '' || !isNaN(+r[c])));

  function bodyRows(data) {{
    return data.map(r => {{
      const cells = cols.map((c, ci) => {{
        const v = r[c];
        const fmt = (isNum[ci] && v !== null && v !== '')
          ? `<td style="text-align:right">${{(+v).toLocaleString('ro', {{maximumFractionDigits:2}})}}</td>`
          : `<td>${{v ?? ''}}</td>`;
        return fmt;
      }});
      return '<tr>' + cells.join('') + '</tr>';
    }}).join('');
  }}

  document.getElementById('analyticsBody').innerHTML = bodyRows(rows);
  document.getElementById('analyticsCount').textContent =
    rows.length.toLocaleString('ro') + ' rânduri';

  // Sort on header click
  document.getElementById('analyticsHead').addEventListener('click', e => {{
    const th = e.target.closest('th');
    if (!th) return;
    const ci = +th.dataset.col;
    if (sortCol === ci) sortAsc = !sortAsc; else {{ sortCol = ci; sortAsc = true; }}

    const sorted = [...rows].sort((a, b) => {{
      const av = a[cols[ci]], bv = b[cols[ci]];
      const an = +av, bn = +bv;
      const cmp = (!isNaN(an) && !isNaN(bn))
        ? an - bn
        : String(av ?? '').localeCompare(String(bv ?? ''), 'ro');
      return sortAsc ? cmp : -cmp;
    }});
    document.getElementById('analyticsBody').innerHTML = bodyRows(sorted);

    // Update sort icons
    document.querySelectorAll('#analyticsHead th').forEach((t, i) => {{
      t.querySelector('.sort-icon').textContent =
        i === sortCol ? (sortAsc ? ' ▲' : ' ▼') : '';
    }});
  }});
}}

/* ── Tab switching ─────────────────────────────────────────────────── */
const tabsEl = document.getElementById('analyticsTabs');
tabsEl.addEventListener('click', e => {{
  const btn = e.target.closest('.tab-btn');
  if (!btn) return;
  tabsEl.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  renderTable(btn.dataset.tab);
}});

// Render first tab on load
renderTable('{TABS[0][0]}');
</script>"""

    extra_head = """
<style>
#analyticsTable th:hover { color: var(--primary); }
#analyticsTable th .sort-icon { font-size: 10px; }
#analyticsTable td { white-space: nowrap; }
</style>"""

    return page_shell("Analiză", "analytics.html", body,
                      extra_head=extra_head, extra_scripts=scripts)


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
  <a href="gas_map.html">Hartă Carburanți</a>
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


def gen_gas_map(stations):
    """Interactive Leaflet map of gas stations with latest fuel prices per popup."""
    network_counts = {}
    network_colors = {}
    for s in stations:
        net = s["network"] or "Unknown"
        network_counts[net] = network_counts.get(net, 0) + 1
        network_colors[net] = net_color(s["network"], GAS_COLORS)

    order = sorted(network_counts, key=lambda n: (-network_counts[n], n == "Unknown", n))

    stations_json = []
    for s in stations:
        stations_json.append({
            "id": str(s["id"]), "name": s["name"], "addr": s["addr"],
            "lat": s["lat"], "lon": s["lon"],
            "net": s["network"] or "Unknown",
            "c": net_color(s["network"], GAS_COLORS),
            "prices": s["prices"],
            "date": s["price_date"][:10] if s["price_date"] else "",
        })

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

    # Nav links for full-page map (same pattern as stores_map)
    nav_links = (
        '<a href="index.html">Dashboard</a>'
        '<a href="price-index.html">Index</a>'
        '<a href="trends.html">Tendințe</a>'
        '<a href="fuel.html">Carburanți</a>'
        '<a href="pipeline.html">Pipeline</a>'
        '<a href="stores_map.html">Hartă Magazine</a>'
    )

    return f"""<!DOCTYPE html>
<html lang="ro">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Hartă Stații Carburanți — Monitorul Prețurilor+</title>
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

  .popup-prices {{ border-collapse: collapse; margin-top: 6px; width: 100%; font-size: 12px; }}
  .popup-prices td {{ padding: 1px 4px; }}
  .popup-prices td:last-child {{ text-align: right; font-weight: 600; }}
</style>
</head>
<body>
<div class="top-bar">
  <span class="brand">Monitorul Prețurilor<sup>+</sup></span>
  {nav_links}
  <span class="count" id="visibleCount">{len(stations)} stații</span>
</div>
<div id="map"></div>
<div id="legend">
  <h4>Rețele</h4>
  {legend_rows}
</div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js"></script>
<script>
const stations = {json.dumps(stations_json, ensure_ascii=False, separators=(",", ":"))};

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

function buildPopup(s) {{
  const addr = s.addr ? `<div style="color:#555;font-size:12px;margin-top:2px">${{s.addr}}</div>` : '';
  const netLabel = `<div style="margin-top:3px;font-size:12px;font-weight:600;color:${{s.c}}">${{s.net}}</div>`;
  let priceRows = '';
  for (const [fuel, price] of Object.entries(s.prices)) {{
    priceRows += `<tr><td>${{fuel}}</td><td>${{price.toFixed(2)}} RON</td></tr>`;
  }}
  const priceTable = priceRows
    ? `<table class="popup-prices">${{priceRows}}</table>`
    : '';
  const dateStr = s.date ? `<div style="color:#888;font-size:11px;margin-top:4px">${{s.date}}</div>` : '';
  return `<b>${{s.name}}</b>${{netLabel}}${{addr}}${{priceTable}}${{dateStr}}`;
}}

const netLayers = {{}};
const clusters = L.markerClusterGroup({{ maxClusterRadius: 40 }});

for (const s of stations) {{
  const marker = L.marker([s.lat, s.lon], {{ icon: circleIcon(s.c) }});
  marker.bindPopup(buildPopup(s), {{ maxWidth: 260 }});
  marker._netName = s.net;
  if (!netLayers[s.net]) netLayers[s.net] = [];
  netLayers[s.net].push(marker);
  clusters.addLayer(marker);
}}
map.addLayer(clusters);

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
  document.getElementById('visibleCount').textContent = count.toLocaleString('ro') + ' stații';
}}
document.getElementById('legend').addEventListener('change', updateFilters);
</script>
</body>
</html>"""


def gen_cos() -> str:
    """Coșul de Cămară — interactive basket calculator.

    Loads `docs/data/baskets/index.json` then a per-basket JSON on demand.
    No server-side data needs to be passed in: the page is purely a viewer
    over the JSON files emitted by `build_baskets.py`.
    """
    extra_head = """
<style>
.cos-controls { display: flex; flex-wrap: wrap; gap: 16px; align-items: end; margin-bottom: 20px; }
.cos-controls label { display: block; font-size: 11.5px; color: var(--muted);
                       text-transform: uppercase; letter-spacing: .3px; margin-bottom: 6px; }
.cos-controls select { padding: 8px 12px; border-radius: 6px; border: 1px solid var(--border);
                        background: var(--card); font-size: 14px; min-width: 240px; }
.cos-tabs { display: flex; flex-wrap: wrap; gap: 6px; }
.cos-tab { padding: 8px 16px; border-radius: 8px; border: 1px solid var(--border);
            background: var(--card); cursor: pointer; font-size: 13.5px; font-weight: 500;
            color: var(--text); transition: all .15s; }
.cos-tab:hover { border-color: var(--primary); color: var(--primary); }
.cos-tab.active { background: var(--primary); color: #fff; border-color: var(--primary); }
.cos-hero { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 20px; }
.cos-hero .kpi { padding: 22px 24px; }
.cos-hero .kpi-value.cheap { color: var(--success); }
.cos-hero .kpi-value.pricey { color: var(--danger); }
.cos-hero .kpi .delta { font-size: 13px; color: var(--muted); margin-top: 8px; }
.cos-rank-row { display: grid; grid-template-columns: 30px 100px 1fr 90px 60px; align-items: center;
                gap: 10px; padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 14px; }
.cos-rank-row:last-child { border-bottom: none; }
.cos-rank-row .pos { color: var(--muted); font-weight: 600; text-align: right; }
.cos-rank-row .net { font-weight: 600; }
.cos-rank-row .bar-wrap { background: #f1f5f9; border-radius: 4px; height: 22px; position: relative;
                          overflow: hidden; }
.cos-rank-row .bar-fill { position: absolute; left: 0; top: 0; bottom: 0; background: var(--primary-light);
                          border-radius: 4px; }
.cos-rank-row .bar-text { position: relative; z-index: 1; padding: 3px 10px; font-size: 13px;
                          font-weight: 600; }
.cos-rank-row .cov { color: var(--muted); font-size: 12px; text-align: right; }
.cos-rank-row.cheapest .net { color: var(--success); }
.cos-rank-row.pricey .net { color: var(--danger); }
.cos-rank-row.incomparable { opacity: .5; }
.cos-rank-row.incomparable .cov { color: var(--warning); }
.cos-items-table th:first-child { width: 40%; }
.cos-items-table .px-cell { text-align: right; font-variant-numeric: tabular-nums; }
.cos-items-table .px-cell.cheapest { color: var(--success); font-weight: 700; }
.cos-items-table .px-cell.missing { color: var(--muted); }
.cos-disclaimer { background: #fef3c7; color: #92400e; padding: 12px 16px; border-radius: 8px;
                   font-size: 13px; margin-bottom: 20px; }
.cos-as-of { font-size: 12px; color: var(--muted); margin-top: 8px; }
@media (max-width: 640px) {
  .cos-hero { grid-template-columns: 1fr; }
  .cos-rank-row { grid-template-columns: 24px 80px 1fr 70px; gap: 8px; font-size: 13px; }
  .cos-rank-row .cov { display: none; }
  .cos-controls select { min-width: 100%; }
  .cos-controls label { width: 100%; }
}
</style>
"""
    body = """
<div class="container">
  <h1>Coșul de Cămară</h1>
  <p class="subtitle">Cât plătești pe lună la fiecare rețea pentru același coș de produse stabile? Alege coșul, alege orașul, vezi unde economisești.</p>

  <div class="cos-disclaimer">
    <b>Notă:</b> Datele provin din monitorulpreturilor.info, care urmărește doar produsele <i>stabile</i> (făină, ulei, paste, conserve, cafea ș.a.). Nu include produse proaspete (lapte, ouă, lactate, carne, legume). Coșurile reflectă acest lucru.
  </div>

  <div class="card">
    <div class="cos-controls">
      <div>
        <label>Coș</label>
        <div class="cos-tabs" id="basket-tabs"></div>
      </div>
      <div>
        <label for="uat-select">Localitate</label>
        <select id="uat-select">
          <option value="__national">🇷🇴 Național (toate rețelele)</option>
        </select>
      </div>
    </div>
    <p id="basket-desc" class="subtitle" style="margin-bottom:0; font-size:13px;"></p>
    <div class="cos-as-of" id="cos-asof"></div>
  </div>

  <div class="cos-hero" id="cos-hero"></div>

  <div class="card">
    <div class="card-title">Clasament rețele</div>
    <div id="cos-rank"></div>
  </div>

  <div class="card" id="cos-items-card">
    <div class="card-title">Detaliu pe produse — preț cel mai mic la fiecare rețea (date naționale)</div>
    <div class="table-wrap"><table class="cos-items-table" id="cos-items-table"></table></div>
  </div>
</div>
"""
    extra_scripts = """
<script>
(function(){
  const FMT = new Intl.NumberFormat('ro-RO', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const fmt = n => (n == null) ? '—' : FMT.format(n) + ' lei';
  const basketCache = {};
  let index = null;
  let currentBasket = null;
  let currentUat = '__national';

  async function loadIndex(){
    const r = await fetch('data/baskets/index.json', {cache: 'no-cache'});
    return r.json();
  }
  async function loadBasket(id){
    if (basketCache[id]) return basketCache[id];
    const r = await fetch(`data/baskets/${id}.json`, {cache: 'no-cache'});
    const d = await r.json();
    basketCache[id] = d;
    return d;
  }

  function renderTabs(){
    const tabs = document.getElementById('basket-tabs');
    tabs.innerHTML = index.baskets.map(b =>
      `<button class="cos-tab ${b.id === currentBasket ? 'active' : ''}" data-id="${b.id}">${b.name_ro}</button>`
    ).join('');
    tabs.querySelectorAll('.cos-tab').forEach(btn => {
      btn.addEventListener('click', () => { currentBasket = btn.dataset.id; renderAll(); });
    });
  }

  function renderUatSelect(){
    const sel = document.getElementById('uat-select');
    const opts = ['<option value="__national">🇷🇴 Național (toate rețelele)</option>'];
    for (const u of index.uats) {
      opts.push(`<option value="${u.id}">${u.name}</option>`);
    }
    sel.innerHTML = opts.join('');
    sel.value = currentUat;
    sel.addEventListener('change', () => { currentUat = sel.value; renderAll(); });
  }

  function getNetworks(basket, uatKey){
    if (uatKey === '__national') return basket.national;
    return basket.per_uat[uatKey] || {};
  }

  function ranked(networks){
    return Object.values(networks)
      .filter(n => n.comparable)
      .sort((a,b) => a.cost_month - b.cost_month);
  }

  function renderHero(basket, networks){
    const r = ranked(networks);
    const hero = document.getElementById('cos-hero');
    if (r.length < 2) {
      hero.innerHTML = `<div class="kpi" style="grid-column: 1 / -1;">
        <div class="kpi-value" style="font-size:18px; color: var(--muted)">Date insuficiente pentru această localitate.</div>
        <div class="kpi-label">Încearcă altă localitate sau vezi datele naționale.</div></div>`;
      return;
    }
    const cheap = r[0], pricey = r[r.length-1];
    const delta = pricey.cost_month - cheap.cost_month;
    const deltaYr = delta * 12;
    hero.innerHTML = `
      <div class="kpi">
        <div class="kpi-label">Cel mai ieftin coș</div>
        <div class="kpi-value cheap">${cheap.network}</div>
        <div class="delta">${fmt(cheap.cost_month)}/lună &middot; ${fmt(cheap.cost_week)}/săpt.</div>
      </div>
      <div class="kpi">
        <div class="kpi-label">Diferență față de cel mai scump (${pricey.network})</div>
        <div class="kpi-value pricey">+${fmt(delta)}<span style="font-size:14px; color: var(--muted)">/lună</span></div>
        <div class="delta">≈ ${fmt(deltaYr)} pe an, cumpărând același coș la ${pricey.network} în loc de ${cheap.network}.</div>
      </div>`;
  }

  function renderRank(networks){
    const all = Object.values(networks).sort((a,b) => {
      if (a.comparable !== b.comparable) return a.comparable ? -1 : 1;
      return a.cost_month - b.cost_month;
    });
    const comparable = all.filter(n => n.comparable);
    const max = comparable.length ? Math.max(...comparable.map(n => n.cost_month)) : 1;
    const min = comparable.length ? Math.min(...comparable.map(n => n.cost_month)) : 0;
    const wrap = document.getElementById('cos-rank');
    let pos = 0;
    wrap.innerHTML = all.map(n => {
      const cls = !n.comparable ? 'incomparable' : (n.cost_month === min ? 'cheapest' : (n.cost_month === max ? 'pricey' : ''));
      if (n.comparable) pos++;
      const fill = n.comparable ? Math.max(2, (n.cost_month / max) * 100) : 0;
      const posLabel = n.comparable ? `#${pos}` : '—';
      return `<div class="cos-rank-row ${cls}">
        <div class="pos">${posLabel}</div>
        <div class="net">${n.network}</div>
        <div class="bar-wrap"><div class="bar-fill" style="width:${fill}%"></div>
          <div class="bar-text">${fmt(n.cost_month)}/lună</div></div>
        <div>${fmt(n.cost_week)}/sapt.</div>
        <div class="cov">${n.items_found}/${n.items_total}</div>
      </div>`;
    }).join('');
  }

  function renderItems(basket){
    // National per-item table — uses basket.national (which has items[]).
    const itemsTable = document.getElementById('cos-items-table');
    const networks = Object.values(basket.national).sort((a,b) => a.cost_month - b.cost_month);
    const itemLabels = basket.items.map(i => i.label);
    let html = '<thead><tr><th>Produs</th><th>Cant./săpt.</th>';
    for (const n of networks) html += `<th class="px-cell">${n.network}</th>`;
    html += '</tr></thead><tbody>';
    for (let i = 0; i < itemLabels.length; i++) {
      const prices = networks.map(n => n.items[i].price);
      const valid = prices.filter(p => p != null);
      const cheap = valid.length ? Math.min(...valid) : null;
      html += `<tr><td>${itemLabels[i]}</td><td class="px-cell">${basket.items[i].qty_per_week}</td>`;
      for (const p of prices) {
        if (p == null) html += '<td class="px-cell missing">—</td>';
        else if (p === cheap) html += `<td class="px-cell cheapest">${FMT.format(p)}</td>`;
        else html += `<td class="px-cell">${FMT.format(p)}</td>`;
      }
      html += '</tr>';
    }
    html += '</tbody>';
    itemsTable.innerHTML = html;
  }

  function renderAll(){
    const meta = index.baskets.find(b => b.id === currentBasket);
    document.getElementById('basket-desc').textContent = meta.description_ro;
    document.getElementById('cos-asof').textContent = `Date la zi: ${index.as_of}`;
    document.querySelectorAll('.cos-tab').forEach(b =>
      b.classList.toggle('active', b.dataset.id === currentBasket));
    loadBasket(currentBasket).then(basket => {
      const networks = getNetworks(basket, currentUat);
      renderHero(basket, networks);
      renderRank(networks);
      renderItems(basket);
      // Items table is national-only; when a UAT is picked, hint that the table is national.
      const card = document.getElementById('cos-items-card');
      const title = card.querySelector('.card-title');
      title.textContent = currentUat === '__national'
        ? 'Detaliu pe produse — preț cel mai mic la fiecare rețea (date naționale)'
        : 'Detaliu pe produse (date naționale — pentru localitatea selectată sunt afișate doar costurile totale)';
    });
  }

  loadIndex().then(idx => {
    index = idx;
    currentBasket = index.baskets[0].id;
    renderTabs();
    renderUatSelect();
    renderAll();
  }).catch(err => {
    document.querySelector('.container').insertAdjacentHTML('beforeend',
      `<div class="card" style="background:#fee2e2; color:#991b1b">Eroare la încărcarea datelor: ${err.message}</div>`);
  });
})();
</script>
"""
    return page_shell("Coșul de Cămară", "cos.html", body, extra_head, extra_scripts)


def gen_inflatie() -> str:
    """Civic CPI prototype — basket cost trend over available price dates.

    Loads docs/data/cpi.json (built by build_cpi.py). Very honest about
    shallow history; the chart skeleton fills in naturally over time.
    """
    extra_scripts = """
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script>
(function(){
  const FMT2 = new Intl.NumberFormat('ro-RO', {minimumFractionDigits:2, maximumFractionDigits:2});
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  const COLORS = ['#2563eb','#16a34a','#f59e0b','#ef4444'];
  const BASKET_COLORS = {camara:'#2563eb', student:'#16a34a', copt:'#f59e0b', sarbatori:'#ef4444'};

  fetch('data/cpi.json', {cache:'no-cache'})
    .then(r => r.json())
    .then(d => {
      // Caveat banner
      document.getElementById('cpi-caveat').textContent = d.caveat;
      document.getElementById('cpi-dates').textContent =
        `${d.n_dates} zile de date: ${d.first_date} → ${d.last_date}`;

      // Trend chart — cost_month per basket over dates
      // Use short date labels
      const labels = d.dates.map(dt => dt.slice(0,5)); // DD.MM

      const datasets = d.baskets.map((b, i) => ({
        label: b.name_ro,
        data: b.series.map(p => p.comparable ? p.cost_month : null),
        borderColor: COLORS[i],
        backgroundColor: COLORS[i] + '22',
        tension: 0.3,
        spanGaps: false,
        pointRadius: 4,
      }));

      new Chart(document.getElementById('cpi-chart'), {
        type: 'line',
        data: {labels, datasets},
        options: {
          responsive: true, maintainAspectRatio: false,
          plugins: {
            legend: {position:'top'},
            tooltip: {callbacks: {label: ctx => `${ctx.dataset.label}: ${FMT2.format(ctx.raw)} lei/lună`}},
          },
          scales: {
            y: {title: {display:true, text:'lei / lună'}, beginAtZero: false},
            x: {title: {display:true, text:'Dată'}},
          },
        },
      });

      // Product change table
      const tbody = document.getElementById('cpi-changes');
      for (const c of d.product_changes) {
        if (c.price_first == null && c.price_last == null) continue;
        const pct = c.change_pct;
        const dir = pct == null ? '' : pct > 0.5 ? '▲' : pct < -0.5 ? '▼' : '→';
        const color = pct == null ? '' : pct > 0.5 ? 'var(--danger)' : pct < -0.5 ? 'var(--success)' : 'var(--muted)';
        tbody.insertAdjacentHTML('beforeend', `<tr>
          <td>${esc(c.label)}</td>
          <td class="px-cell">${c.price_first != null ? FMT2.format(c.price_first) : '—'}</td>
          <td class="px-cell">${c.price_last  != null ? FMT2.format(c.price_last)  : '—'}</td>
          <td class="px-cell" style="color:${color};font-weight:600">${dir} ${pct != null ? pct + '%' : '—'}</td>
        </tr>`);
      }
    })
    .catch(err => {
      document.getElementById('cpi-chart-wrap').innerHTML =
        `<div style="padding:30px;color:#991b1b;background:#fee2e2;border-radius:8px">Eroare: ${esc(err.message)}</div>`;
    });
})();
</script>
"""
    body = """
<div class="container">
  <h1>Indice de Inflație Civică <span style="font-size:14px;background:#fef3c7;color:#92400e;padding:3px 10px;border-radius:10px;margin-left:8px;vertical-align:middle">PROTOTIP</span></h1>
  <p class="subtitle">Evoluția costului coșurilor de cumpărături în timp, calculată din prețurile reale de raft. Un CPI alternativ, deschis și verificabil.</p>

  <div class="cos-disclaimer" id="cpi-caveat" style="background:#fef9c3;color:#78350f"></div>
  <p style="font-size:12px;color:var(--muted);margin-bottom:20px" id="cpi-dates"></p>

  <div class="card">
    <div class="card-title">Cost lunar coș — evoluție zilnică</div>
    <p style="font-size:12px;color:var(--muted);margin:8px 0">Costul <em>celui mai ieftin</em> coș național (orice rețea), per zi de colectare. Variațiile zilnice reflectă parțial acoperirea diferită a magazinelor, nu doar schimbările de preț — în timp acest semnal se stabilizează.</p>
    <div id="cpi-chart-wrap" style="height:340px;position:relative;margin-top:12px">
      <canvas id="cpi-chart"></canvas>
    </div>
  </div>

  <div class="card">
    <div class="card-title">Variație preț per produs — prima zi vs ultima zi disponibilă</div>
    <p style="font-size:12px;color:var(--muted);margin:8px 0">Comparație cel mai mic preț național între prima și ultima dată disponibilă. Cu puține date, variațiile pot reflecta schimbări de acoperire, nu neapărat modificări de preț.</p>
    <div class="table-wrap">
      <table class="table">
        <thead><tr>
          <th>Produs</th>
          <th class="px-cell">Prima dată</th>
          <th class="px-cell">Ultima dată</th>
          <th class="px-cell">Variație</th>
        </tr></thead>
        <tbody id="cpi-changes"></tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <div class="card-title">Metodologie</div>
    <p style="font-size:14px;line-height:1.6">
      Urmărim costul total al fiecărui coș de produse stabile, folosind cel mai mic preț disponibil la orice rețea în ziua respectivă (after filtrare outlieri). Aceasta este o aproximare Laspeyres simplificată — cantitățile rămân fixe (definite în <code>config/baskets.json</code>), iar prețurile se actualizează zilnic.
      <br><br>
      <b>Limitare importantă:</b> cu {n} zile de date, variațiile zi-la-zi reflectă și fluctuații de acoperire (câte magazine au fost interogați azi vs ieri), nu doar modificări reale de preț. Semnalul devine robust după ~4 săptămâni de colectare consecventă. Până atunci, folosiți acest grafic ca <em>schelet</em>, nu ca indicator definitiv.
      <br><br>
      Detalii complete: <a href="metodologie.html">pagina de metodologie</a>.
    </p>
  </div>
</div>
"""
    return page_shell("Indice Inflație Civică", "inflatie.html", body, extra_scripts=extra_scripts)


def gen_povesti() -> str:
    """Povești cu Date — auto-generated narrative cards from today's data.

    Loads anomalies + basket + category data client-side and renders
    4-6 narrative story cards. No historical trends needed — each story
    is a snapshot insight from today's data.
    """
    extra_scripts = """
<script>
(function(){
  const FMT2 = new Intl.NumberFormat('ro-RO', {minimumFractionDigits:2, maximumFractionDigits:2});
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  function storyCard(icon, title, headline, detail, tag, linkHref, linkText) {
    return `<div class="story-card">
      <div class="story-icon">${icon}</div>
      <div class="story-tag">${esc(tag)}</div>
      <div class="story-title">${esc(title)}</div>
      <div class="story-headline">${headline}</div>
      <div class="story-detail">${esc(detail)}</div>
      ${linkHref ? `<a class="story-link" href="${linkHref}">${esc(linkText)} →</a>` : ''}
    </div>`;
  }

  function buildStories(anomalies, baskets, catIndex) {
    const el = document.getElementById('stories-grid');
    const asOf = anomalies.as_of;
    document.getElementById('stories-asof').textContent = `Date la ${asOf}`;
    const stories = [];

    // Story 1: biggest spread today
    const top = anomalies.items[0];
    if (top) {
      stories.push(storyCard('🔍',
        'Cel mai mare decalaj de preț azi',
        `<b>${esc(top.product)}</b>: <span style="color:var(--success)">${FMT2.format(top.cheapest.price)} lei</span> la ${esc(top.cheapest.network)} față de <span style="color:var(--danger)">${FMT2.format(top.priciest.price)} lei</span> la ${esc(top.priciest.network)}`,
        `Diferență de ${FMT2.format(top.save_lei)} lei — de ${top.ratio}× mai scump. Același produs, aceeași zi.`,
        'Anomalie', 'anomalii.html', 'Vezi toate anomaliile'
      ));
    }

    // Story 2: which network is cheapest most often
    const netTally = {};
    for (const it of anomalies.items) {
      const n = it.cheapest.network;
      netTally[n] = (netTally[n] || 0) + 1;
    }
    const sorted = Object.entries(netTally).sort((a,b)=>b[1]-a[1]);
    if (sorted.length >= 2) {
      const [topNet, topCnt] = sorted[0];
      const pct = Math.round(topCnt * 100 / anomalies.count);
      stories.push(storyCard('🏆',
        'Rețeaua cu cele mai mici prețuri azi',
        `<b>${esc(topNet)}</b> este cea mai ieftină la <span style="color:var(--success);font-weight:700">${topCnt} din ${anomalies.count}</span> produse cu spread semnificativ`,
        `${pct}% din produsele comparate azi au prețul minim la ${esc(topNet)}. Locul 2: ${esc(sorted[1][0])} (${sorted[1][1]} produse).`,
        'Rețele', 'anomalii.html?net=' + encodeURIComponent(topNet), 'Filtrează după rețea'
      ));
    }

    // Story 3: basket savings opportunity
    const cam = baskets.baskets.find(b => b.id === 'camara');
    if (cam) {
      const cheap = cam.national_cheapest_month;
      const pricey = cam.national_priciest_month;
      const net = cam.national_cheapest_network;
      const diff = pricey - cheap;
      if (cheap && pricey) {
        stories.push(storyCard('🛒',
          'Coșul de cămară: cât pierzi dacă alegi greșit?',
          `<b>+${FMT2.format(diff)} lei/lună</b> față de rețeaua ieftină`,
          `Cel mai ieftin coș de cămară la nivel național: ${FMT2.format(cheap)} lei/lună la ${esc(net)}. Cel mai scump: ${FMT2.format(pricey)} lei/lună. Diferența anuală: ${FMT2.format(diff * 12)} lei.`,
          'Coș', 'cos.html', 'Calculator coș'
        ));
      }
    }

    // Story 4: food deserts — from category index
    // Use anomaly data to find most-expensive category
    const catSpreads = {};
    for (const it of anomalies.items) {
      if (!it.category) continue;
      if (!catSpreads[it.category]) catSpreads[it.category] = {total:0, count:0, max:0, topProd:''};
      catSpreads[it.category].total += it.save_lei;
      catSpreads[it.category].count += 1;
      if (it.save_lei > catSpreads[it.category].max) {
        catSpreads[it.category].max = it.save_lei;
        catSpreads[it.category].topProd = it.product;
      }
    }
    const topCat = Object.entries(catSpreads).sort((a,b)=>b[1].total-a[1].total)[0];
    if (topCat) {
      const [catName, catData] = topCat;
      stories.push(storyCard('📦',
        'Categoria cu cele mai mari diferențe de preț',
        `<b>${esc(catName)}</b>: ${catData.count} produse cu spread total de <span style="color:var(--danger);font-weight:700">${FMT2.format(catData.total)} lei</span>`,
        `Cel mai mare decalaj în această categorie: ${esc(catData.topProd)} — diferență de ${FMT2.format(catData.max)} lei între rețele.`,
        'Categorii', 'categorii.html', 'Explorator categorii'
      ));
    }

    // Story 5: number of products with ratio > 3x
    const extremes = anomalies.items.filter(i => i.ratio >= 3);
    if (extremes.length) {
      stories.push(storyCard('⚠️',
        'Prețuri de 3× sau mai scumpe — același produs',
        `<b>${extremes.length} produse</b> au prețul de cel puțin <span style="color:var(--danger);font-weight:700">3× mai mare</span> într-o rețea față de alta`,
        `Cel mai extrem: ${esc(extremes[0].product)} — ${extremes[0].ratio}×, economie de ${FMT2.format(extremes[0].save_lei)} lei. Cumpărând toate 3× produsele la rețeaua ieftină se economisesc ${FMT2.format(extremes.reduce((s,x)=>s+x.save_lei,0))} lei.`,
        'Anomalii extreme', 'anomalii.html', 'Filtrează 3×+'
      ));
    }

    el.innerHTML = stories.join('');
  }

  Promise.all([
    fetch('data/anomalies_today.json', {cache:'no-cache'}).then(r=>r.json()),
    fetch('data/baskets/index.json', {cache:'no-cache'}).then(r=>r.json()),
    fetch('data/categories/index.json', {cache:'no-cache'}).then(r=>r.json()),
  ]).then(([anom, baskets, cats]) => {
    buildStories(anom, baskets, cats);
  }).catch(err => {
    document.getElementById('stories-grid').innerHTML =
      `<div style="padding:24px;color:#991b1b;background:#fee2e2;border-radius:8px">Eroare: ${esc(err.message)}</div>`;
  });
})();
</script>
"""
    extra_head = """
<style>
.stories-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px,1fr)); gap: 18px; }
.story-card { background: var(--card); border: 1px solid var(--border); border-radius: 12px;
              padding: 20px; display: flex; flex-direction: column; gap: 8px;
              transition: box-shadow .15s; }
.story-card:hover { box-shadow: 0 4px 20px rgba(0,0,0,.09); }
.story-icon { font-size: 28px; line-height: 1; }
.story-tag { font-size: 11px; text-transform: uppercase; letter-spacing: .5px;
             color: var(--primary); font-weight: 600; }
.story-title { font-size: 16px; font-weight: 700; line-height: 1.3; }
.story-headline { font-size: 14px; line-height: 1.5; }
.story-detail { font-size: 13px; color: var(--muted); line-height: 1.5; flex: 1; }
.story-link { font-size: 13px; color: var(--primary); text-decoration: none; font-weight: 600;
              margin-top: 4px; }
.story-link:hover { text-decoration: underline; }
@media (max-width: 640px) { .stories-grid { grid-template-columns: 1fr; } }
</style>
"""
    body = """
<div class="container">
  <h1>Povești cu Date</h1>
  <p class="subtitle">Cele mai importante insights din datele de azi — generate automat. Actualizate zilnic.</p>
  <p style="font-size:12px;color:var(--muted);margin-bottom:20px" id="stories-asof"></p>

  <div class="stories-grid" id="stories-grid">
    <div style="padding:24px;color:var(--muted)">Se încarcă...</div>
  </div>
</div>
"""
    return page_shell("Povești cu Date", "povesti.html", body, extra_head, extra_scripts)


def gen_metodologie(summary: dict, stats: dict) -> str:
    """Trust & Methodology — honest account of the data, its gaps, and how we compute things."""

    def _n(key):
        v = summary.get(key, stats.get(key, "—"))
        return f"{v:,}" if isinstance(v, int) else str(v)

    body = f"""
<div class="container">
  <h1>Metodologie & Transparență</h1>
  <p class="subtitle">Cum funcționează acest site, de unde vin datele, ce știm că lipsește și cum calculăm fiecare indicator.</p>

  <div class="cos-disclaimer">
    <b>Notă:</b> Acesta <b>nu este un proiect oficial guvernamental</b>. Datele provin din API-ul public <a href="https://monitorulpreturilor.info" target="_blank">monitorulpreturilor.info</a>, operat de Autoritatea Națională pentru Protecția Consumatorilor (ANPC). Codul sursă și datele brute sunt publice; metodologia e complet deschisă.
  </div>

  <!-- ── Live snapshot ── -->
  <div class="card">
    <div class="card-title">Starea datelor — snapshot curent</div>
    <div style="display:grid; grid-template-columns: repeat(auto-fill, minmax(180px,1fr)); gap:14px; margin-top:12px">
      {''.join(f'<div class="kpi" style="padding:16px"><div class="kpi-label">{label}</div><div class="kpi-value" style="font-size:22px">{val}</div></div>' for label, val in [
        ("Produse urmărite", _n("products")),
        ("Magazine", _n("stores")),
        ("Rețele retail", _n("retail_networks")),
        ("Localități cu magazine", str(stats["uats_with_stores"])),
        ("Înregistrări prețuri", _n("prices")),
        ("Date distincte retail", str(stats["price_dates"])),
        ("Ultima actualizare retail", stats["latest_retail"]),
        ("Stații carburanți", _n("gas_stations")),
        ("Date distincte carburanți", str(stats["gas_dates"])),
        ("Ultima actualizare carburanți", stats["latest_gas"]),
      ])}
    </div>
  </div>

  <!-- ── Data source ── -->
  <div class="card">
    <div class="card-title">Sursa datelor</div>
    <p style="margin:12px 0 8px">API-ul <a href="https://monitorulpreturilor.info" target="_blank">monitorulpreturilor.info</a> (ANPC) expune prețuri de raft raportate de retaileri prin sistemul național de monitorizare. Accesăm două endpoint-uri:</p>
    <table class="table" style="margin-top:8px">
      <thead><tr><th>Endpoint</th><th>Ce returnează</th><th>Limite</th></tr></thead>
      <tbody>
        <tr><td><code>GetStoresForProductsByLatLon</code></td><td>Prețuri retail per produs × magazin, în jurul unui punct geografic</td><td>Buffer max 5.000 m; max 50 magazine per cerere</td></tr>
        <tr><td><code>GetGasItemsByUat</code></td><td>Prețuri carburanți per UAT × tip combustibil</td><td>Un produs per cerere; API returnează 500 dacă nu există date</td></tr>
      </tbody>
    </table>
    <p style="margin-top:12px; font-size:13px; color:var(--muted)">Colectăm retail zilnic (~04:00 UTC). Carburanții se actualizează zilnic per UAT. Datele de referință (rețele, UAT-uri, produse) se reîmprospătează săptămânal.</p>
  </div>

  <!-- ── Known gaps ── -->
  <div class="card">
    <div class="card-title">Limitări cunoscute</div>
    <div style="display:flex; flex-direction:column; gap:12px; margin-top:8px">
      <div style="padding:12px; background:#fef9c3; border-radius:8px; font-size:13px">
        <b>Produse proaspete lipsesc.</b> API-ul urmărește doar produse stabile (făină, ulei, paste, cafea, conserve etc.). Carne, ouă, lactate, legume, fructe <b>nu sunt acoperite</b>. Coșurile de pe site reflectă explicit acest lucru (etichetate "cămară").
      </div>
      <div style="padding:12px; background:#fef9c3; border-radius:8px; font-size:13px">
        <b>{stats["stores_no_network"]:,} magazine fără rețea identificată</b> ({stats["stores_no_network"] * 100 // summary.get("stores", 1)}% din total). Aceste magazine apar în hartă și în statistici de acoperire, dar <b>nu sunt incluse în comparații pe rețea</b> (nu știm la ce rețea aparțin).
      </div>
      <div style="padding:12px; background:#fef9c3; border-radius:8px; font-size:13px">
        <b>{stats["products_no_price_today"]:,} produse fără preț azi</b> (din {_n("products")} urmărite). Acoperirea nu e 100% — depinde de ce magazine au fost interogați azi și dacă produsul e în stoc.
      </div>
      <div style="padding:12px; background:#fef9c3; border-radius:8px; font-size:13px">
        <b>Istoric retail scurt — {stats["price_dates"]} zile distincte.</b> Indicii de inflație și tendințele pe termen lung vor deveni semnificative odată cu acumularea de date. Carburanții au {stats["gas_dates"]} zile de istoric.
      </div>
      <div style="padding:12px; background:#fef9c3; border-radius:8px; font-size:13px">
        <b>Limita API de 50 magazine per cerere</b> înseamnă că nu toate magazinele dintr-o localitate mare (ex. București) sunt acoperite zilnic. Folosim un sistem de clustering spatial pentru a maximiza diversitatea.
      </div>
    </div>
  </div>

  <!-- ── Methodology ── -->
  <div class="card">
    <div class="card-title">Cum calculăm indicatorii</div>
    <div style="display:flex; flex-direction:column; gap:16px; margin-top:8px; font-size:14px">

      <div>
        <b>Coșul de cămară (cos.html)</b>
        <p style="margin-top:6px">Pentru fiecare (coș × localitate × rețea): alegem cel mai ieftin produs substitut disponibil din lista de alternative, înmulțim cu cantitatea săptămânală, sumăm și convertim lunar (× 52/12 = 4,333 săptămâni/lună). O rețea e <em>comparabilă</em> dacă are prețuri pentru cel puțin 50% din articolele coșului. Prețuri evident eronate filtrate: excludem orice preț sub 30% sau peste 300% din mediana cross-rețea pentru acel produs.</p>
      </div>

      <div>
        <b>Anomalii de preț (anomalii.html)</b>
        <p style="margin-top:6px">Pentru fiecare produs cu prețuri în ≥2 rețele azi: calculăm minimul per rețea, aplicăm același filtru de outlieri (0,30–3,0× mediană), calculăm ratio = max/min. Afișăm produsele cu ratio ≥ 1,5 (cel puțin 50% mai scump la rețeaua cea mai scumpă față de cea mai ieftină).</p>
      </div>

      <div>
        <b>Exploratorul de categorii (categorii.html)</b>
        <p style="margin-top:6px">Același calcul de spread ca anomaliile, grupat pe categoria de produs (nivel 2 din arborele ANPC). Liderul de rețea = câte produse din categorie sunt cel mai ieftin la fiecare rețea.</p>
      </div>

      <div>
        <b>Harta costurilor (harta.html)</b>
        <p style="margin-top:6px">Poligoanele UAT provin din <code>config/geo/ro-uats.topojson</code> (date ANCPI, 3.175 unități administrativ-teritoriale, join pe codul SIRUTA). Costul coșului per UAT = cel mai ieftin coș comparabil la orice rețea prezentă în UAT. Localitățile fără date locale afișează estimarea națională.</p>
      </div>

      <div>
        <b>Indexul de prețuri pe rețea (price-index.html)</b>
        <p style="margin-top:6px">Pentru produse prezente în ≥3 rețele: calculăm prețul mediu per rețea, împărțit la prețul minim cross-rețea × 100. Index = 100 înseamnă că rețeaua e cea mai ieftină la acel produs; 120 = 20% mai scump față de cel mai ieftin.</p>
      </div>

    </div>
  </div>

  <!-- ── Code & license ── -->
  <div class="card">
    <div class="card-title">Cod sursă & licență</div>
    <p style="margin:12px 0 8px; font-size:14px">Codul este open-source pe GitHub. Datele exportate sunt disponibile pe pagina <a href="date-deschise.html">Date Deschise</a> sub licență <b>CC BY 4.0</b> — poți redistribui și folosi cu atribuire.</p>
    <p style="font-size:13px; color:var(--muted)">Jurnalul de activitate complet: <a href="pipeline.html">Pipeline & Activitate</a>. Probleme cunoscute și planuri viitoare: <a href="pipeline.html#backlog">Backlog</a>.</p>
  </div>

</div>
"""
    return page_shell("Metodologie & Transparență", "metodologie.html", body)


def gen_date_deschise(summary: dict, stats: dict) -> str:
    """Open Data Hub — downloadable datasets with schema and freshness."""
    import os

    def _size(path):
        try:
            b = os.path.getsize(path)
            return f"{b/1024:.0f} KB" if b < 1_000_000 else f"{b/1_000_000:.1f} MB"
        except OSError:
            return "—"

    docs = "docs"
    datasets = [
        {
            "title": "Anomalii de preț — azi",
            "file": "data/anomalies_today.json",
            "format": "JSON",
            "desc": "Produse cu ratio preț-max/preț-min ≥ 1,5 între rețele, pentru data curentă. Câmpuri: product_id, product, category, brand, unit, cheapest{network,price}, priciest{network,price}, ratio, save_lei, save_pct, by_network[].",
            "updated": stats["latest_retail"],
        },
        {
            "title": "Coșuri de cumpărături — index",
            "file": "data/baskets/index.json",
            "format": "JSON",
            "desc": "Metadata pentru cele 4 coșuri curate (cămară, student, copt, sărbători): cost minim/maxim lunar național, rețea ieftină, nr. UAT-uri acoperite.",
            "updated": stats["latest_retail"],
        },
        {
            "title": "Coșul de Cămară — detaliu",
            "file": "data/baskets/camara.json",
            "format": "JSON",
            "desc": "Cost săptămânal/lunar per rețea, național și per UAT. Include drill-down per produs cu prețul ales și produsul substitut.",
            "updated": stats["latest_retail"],
        },
        {
            "title": "Harta UAT-urilor — GeoJSON",
            "file": "data/uats.geojson",
            "format": "GeoJSON",
            "desc": "Poligoane UAT pentru localitățile cu magazine în baza de date. Proprietăți: siruta, name, n_stores, n_networks, basket_min_month, basket_max_month, basket_cheapest_net.",
            "updated": stats["latest_retail"],
        },
        {
            "title": "Categorii — index",
            "file": "data/categories/index.json",
            "format": "JSON",
            "desc": "Lista categoriilor de produse cu statistici de spread: nr. produse comparabile, cel mai mare ratio, economii totale posibile, top-3 rețele ieftine.",
            "updated": stats["latest_retail"],
        },
        {
            "title": "Variabilitate prețuri pe rețea",
            "file": "data/cross_network_spread.csv",
            "format": "CSV",
            "desc": "Per produs: preț minim/maxim pe rețea, spread (lei), ratio. Toate prețurile istorice (nu doar azi). Coloane: product_id, product, min_net_price, max_net_price, networks, spread, ratio.",
            "updated": stats["latest_retail"],
        },
        {
            "title": "Index prețuri pe rețea",
            "file": "data/price_variability.csv",
            "format": "CSV",
            "desc": "Variabilitate medie a prețului per produs × rețea. Baza pentru graficul Index Prețuri.",
            "updated": stats["latest_retail"],
        },
        {
            "title": "Magazine per rețea",
            "file": "data/stores_per_network.csv",
            "format": "CSV",
            "desc": "Câte magazine are fiecare rețea în baza de date, cu nr. de UAT-uri acoperite.",
            "updated": stats["latest_retail"],
        },
        {
            "title": "Candidați marcă privată",
            "file": "data/private_labels.csv",
            "format": "CSV",
            "desc": "Produse identificate ca potențial marcă privată (vândute exclusiv sau majoritar la o singură rețea). Coloane: product, network, stores.",
            "updated": stats["latest_retail"],
        },
    ]

    cards = []
    for d in datasets:
        full_path = os.path.join(docs, d["file"])
        size = _size(full_path)
        href = d["file"]
        cards.append(f"""
        <div class="card" style="display:grid; grid-template-columns: 1fr auto; gap:12px; align-items:start">
          <div>
            <div style="font-weight:700; font-size:15px; margin-bottom:4px">{d["title"]}</div>
            <div style="font-size:12px; margin-bottom:8px">
              <span style="background:#dbeafe;color:#1e40af;padding:2px 8px;border-radius:10px;font-weight:600;margin-right:6px">{d["format"]}</span>
              <span style="color:var(--muted)">{size} &middot; Actualizat: {d["updated"]}</span>
            </div>
            <div style="font-size:13px; color:var(--muted); line-height:1.5">{d["desc"]}</div>
          </div>
          <div style="text-align:right; white-space:nowrap">
            <a href="{href}" download style="display:inline-block;padding:8px 16px;background:var(--primary);color:#fff;border-radius:6px;font-size:13px;font-weight:600;text-decoration:none">↓ Descarcă</a>
          </div>
        </div>""")

    n_retail = f"{summary.get('prices', 0):,}"
    n_gas = f"{summary.get('gas_prices', 0):,}"

    body = f"""
<div class="container">
  <h1>Date Deschise</h1>
  <p class="subtitle">Toate seturile de date generate de acest proiect, disponibile pentru descărcare liberă. Licență CC BY 4.0 — redistribuie cu atribuire.</p>

  <div class="cos-disclaimer">
    <b>Licență:</b> <a href="https://creativecommons.org/licenses/by/4.0/" target="_blank">Creative Commons Attribution 4.0 International (CC BY 4.0)</a>. Poți folosi, redistribui și adapta datele cu condiția să menționezi sursa: <em>monitorulpreturilor.info (ANPC) via monitorul-preturilor Civic Dashboard</em>.
  </div>

  <div class="anom-summary" style="display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:20px">
    <div class="kpi">
      <div class="kpi-label">Înregistrări prețuri retail</div>
      <div class="kpi-value">{n_retail}</div>
      <div class="kpi-trend" style="font-size:12px;color:var(--muted)">Ultima actualizare: {stats["latest_retail"]}</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Înregistrări prețuri carburanți</div>
      <div class="kpi-value">{n_gas}</div>
      <div class="kpi-trend" style="font-size:12px;color:var(--muted)">Ultima actualizare: {stats["latest_gas"]}</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Seturi de date disponibile</div>
      <div class="kpi-value">{len(datasets)}</div>
      <div class="kpi-trend" style="font-size:12px;color:var(--muted)">Actualizate zilnic automat</div>
    </div>
  </div>

  <p style="font-size:13px;color:var(--muted);margin-bottom:16px">
    Notă: baza de date SQLite completă (<code>prices.db</code>, ~{_size("data/prices.db")}) nu e distribuită direct din cauza dimensiunii, dar codul de colectare e public și poate fi rulat local. Datele de mai jos acoperă toți indicatorii calculați de site.
  </p>

  {"".join(cards)}

</div>
"""
    return page_shell("Date Deschise", "date-deschise.html", body)


def gen_harta() -> str:
    """Harta Costuri — choropleth map of Romania UATs.

    Uses MapLibre GL JS + docs/data/uats.geojson (built by build_uat_geojson.py).
    Two layers: number of retail networks (food-desert detection) and cheapest
    monthly basket cost (Coșul de Cămară). Click a UAT polygon for details.
    """
    extra_head = """
<link rel="stylesheet" href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css">
<script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>
<style>
#map-wrap { position: relative; height: 580px; border-radius: 10px; overflow: hidden;
            border: 1px solid var(--border); margin-bottom: 16px; }
#map { width: 100%; height: 100%; }
.map-controls { position: absolute; top: 12px; left: 12px; z-index: 10;
                background: var(--card); border-radius: 8px; padding: 10px 14px;
                box-shadow: 0 2px 12px rgba(0,0,0,.15); font-size: 13px; }
.map-controls label { display: flex; align-items: center; gap: 8px; margin-bottom: 4px;
                      cursor: pointer; font-weight: 500; }
.map-controls label:last-child { margin-bottom: 0; }
.map-controls input[type=radio] { accent-color: var(--primary); }
#map-panel { position: absolute; bottom: 12px; right: 12px; z-index: 10;
             background: var(--card); border-radius: 10px; padding: 14px 16px;
             box-shadow: 0 2px 16px rgba(0,0,0,.15); width: 240px;
             font-size: 13px; display: none; }
#map-panel .panel-name { font-weight: 700; font-size: 14px; margin-bottom: 8px;
                          border-bottom: 1px solid var(--border); padding-bottom: 6px; }
#map-panel .panel-row { display: flex; justify-content: space-between; padding: 3px 0; }
#map-panel .panel-val { font-weight: 600; }
#map-panel .close-btn { position: absolute; top: 8px; right: 10px; cursor: pointer;
                         color: var(--muted); font-size: 16px; line-height: 1; }
#map-legend { position: absolute; bottom: 12px; left: 12px; z-index: 10;
              background: var(--card); border-radius: 8px; padding: 10px 14px;
              box-shadow: 0 2px 12px rgba(0,0,0,.15); font-size: 12px; }
#map-legend .leg-title { font-weight: 600; margin-bottom: 6px; }
.leg-row { display: flex; align-items: center; gap: 6px; margin-bottom: 3px; }
.leg-swatch { width: 16px; height: 16px; border-radius: 3px; flex-shrink: 0; }
.harta-summary { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin-bottom: 20px; }
@media (max-width: 640px) {
  #map-wrap { height: 420px; }
  .harta-summary { grid-template-columns: 1fr; }
  #map-panel { width: 200px; font-size: 12px; }
  .map-controls { font-size: 12px; padding: 8px 10px; }
}
</style>
"""
    body = """
<div class="container">
  <h1>Harta Costurilor</h1>
  <p class="subtitle">Unde în România există concurență între rețele și unde un singur retailer domină? Cât costă coșul de cămară pe localitate?</p>

  <div class="harta-summary" id="harta-summary"></div>

  <div id="map-wrap">
    <div id="map"></div>

    <div class="map-controls">
      <label><input type="radio" name="layer" value="networks" checked> Rețele prezente</label>
      <label><input type="radio" name="layer" value="basket"> Coș lunar (lei)</label>
      <label><input type="radio" name="layer" value="stores"> Nr. magazine</label>
    </div>

    <div id="map-legend"></div>

    <div id="map-panel">
      <div class="close-btn" id="panel-close">×</div>
      <div class="panel-name" id="panel-name"></div>
      <div id="panel-body"></div>
    </div>
  </div>

  <div class="cos-disclaimer" style="margin-top:0">
    <b>Notă:</b> Coșul de cămară (Profi/Kaufland/etc.) se referă la produse stabile urmărite de API. Localitățile fără date de coș afișează estimarea națională. Rețele = retaileri cu magazine identificate; magazinele fără rețea identificată nu sunt numărate.
  </div>
</div>
"""
    extra_scripts = """
<script>
(function(){
  const FMT = new Intl.NumberFormat('ro-RO', {minimumFractionDigits:0, maximumFractionDigits:0});
  const FMT2 = new Intl.NumberFormat('ro-RO', {minimumFractionDigits:2, maximumFractionDigits:2});

  // Layer configs: colors for 0..N steps
  const LAYERS = {
    networks: {
      title: 'Rețele prezente',
      prop: 'n_networks',
      steps: [0, 1, 2, 3, 4, 5],
      colors: ['#e5e7eb','#fca5a5','#fb923c','#facc15','#86efac','#22c55e'],
      labels: ['Neidentificate','1 rețea','2','3','4','5+'],
    },
    basket: {
      title: 'Coș cămară / lună (lei)',
      prop: 'basket_min_month',
      steps: [0, 200, 260, 300, 330, 360],
      colors: ['#e5e7eb','#22c55e','#86efac','#facc15','#fb923c','#ef4444'],
      labels: ['Fără date','< 200','200–260','260–300','300–330','> 330'],
    },
    stores: {
      title: 'Număr magazine',
      prop: 'n_stores',
      steps: [0, 1, 3, 10, 30, 100],
      colors: ['#e5e7eb','#bfdbfe','#93c5fd','#3b82f6','#1d4ed8','#1e3a8a'],
      labels: ['0','1–2','3–9','10–29','30–99','100+'],
    },
  };

  function colorExpr(layer) {
    const {prop, steps, colors} = LAYERS[layer];
    // MapLibre step expression: ['step', ['get', prop], default, v1, c1, v2, c2, ...]
    const expr = ['step', ['coalesce', ['get', prop], 0], colors[0]];
    for (let i = 1; i < steps.length; i++) {
      expr.push(steps[i], colors[i]);
    }
    return expr;
  }

  function renderLegend(layer) {
    const cfg = LAYERS[layer];
    const el = document.getElementById('map-legend');
    el.innerHTML = `<div class="leg-title">${cfg.title}</div>` +
      cfg.colors.map((c, i) =>
        `<div class="leg-row"><div class="leg-swatch" style="background:${c}"></div>${cfg.labels[i]}</div>`
      ).join('');
  }

  function renderSummary(features) {
    const props = features.map(f => f.properties);
    const withNets = props.filter(p => p.n_networks >= 1).length;
    const deserts = props.filter(p => p.n_networks === 1).length;
    const basketProps = props.filter(p => p.basket_min_month && p.basket_min_month > 0);
    const cheapest = basketProps.length ? Math.min(...basketProps.map(p => p.basket_min_month)) : null;
    const cheapestUat = cheapest ? basketProps.find(p => p.basket_min_month === cheapest) : null;
    document.getElementById('harta-summary').innerHTML = `
      <div class="kpi">
        <div class="kpi-label">Localități cu rețele identificate</div>
        <div class="kpi-value">${withNets}</div>
        <div class="kpi-trend" style="font-size:12px;color:var(--muted)">din ${props.length} cu magazine</div>
      </div>
      <div class="kpi">
        <div class="kpi-label">Localități cu o singură rețea</div>
        <div class="kpi-value" style="color:var(--danger)">${deserts}</div>
        <div class="kpi-trend" style="font-size:12px;color:var(--muted)">fără concurență locală</div>
      </div>
      <div class="kpi">
        <div class="kpi-label">Coș mai ieftin — localitate</div>
        <div class="kpi-value" style="color:var(--success)">${cheapest ? FMT2.format(cheapest) + ' lei' : '—'}</div>
        <div class="kpi-trend" style="font-size:12px;color:var(--muted)">${cheapestUat ? cheapestUat.name : ''}</div>
      </div>`;
  }

  function showPanel(props) {
    const p = document.getElementById('map-panel');
    document.getElementById('panel-name').textContent = props.name;
    document.getElementById('panel-body').innerHTML = `
      <div class="panel-row"><span>Rețele</span><span class="panel-val">${props.n_networks || '—'}</span></div>
      <div class="panel-row"><span>Magazine</span><span class="panel-val">${props.n_stores}</span></div>
      <div class="panel-row"><span>Coș minim/lună</span><span class="panel-val">${props.basket_min_month ? FMT2.format(props.basket_min_month) + ' lei' : '—'}</span></div>
      <div class="panel-row"><span>Coș maxim/lună</span><span class="panel-val">${props.basket_max_month ? FMT2.format(props.basket_max_month) + ' lei' : '—'}</span></div>
      <div class="panel-row"><span>Rețea ieftină</span><span class="panel-val">${props.basket_cheapest_net || '—'}</span></div>`;
    p.style.display = 'block';
  }

  fetch('data/uats.geojson', {cache:'no-cache'})
    .then(r => r.json())
    .then(geojson => {
      renderSummary(geojson.features);

      const map = new maplibregl.Map({
        container: 'map',
        style: 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json',
        center: [25.0, 45.8],
        zoom: 6.2,
        minZoom: 5,
        maxZoom: 14,
      });

      map.addControl(new maplibregl.NavigationControl(), 'top-right');

      map.on('load', () => {
        map.addSource('uats', {type: 'geojson', data: geojson});

        map.addLayer({
          id: 'uats-fill',
          type: 'fill',
          source: 'uats',
          paint: {
            'fill-color': colorExpr('networks'),
            'fill-opacity': 0.75,
          },
        });

        map.addLayer({
          id: 'uats-outline',
          type: 'line',
          source: 'uats',
          paint: {
            'line-color': '#94a3b8',
            'line-width': ['interpolate', ['linear'], ['zoom'], 5, 0.3, 10, 1],
            'line-opacity': 0.6,
          },
        });

        map.addLayer({
          id: 'uats-hover',
          type: 'fill',
          source: 'uats',
          paint: {
            'fill-color': '#0ea5e9',
            'fill-opacity': ['case', ['boolean', ['feature-state', 'hover'], false], 0.3, 0],
          },
        });

        renderLegend('networks');

        // Layer toggle
        document.querySelectorAll('input[name=layer]').forEach(radio => {
          radio.addEventListener('change', () => {
            const layer = radio.value;
            map.setPaintProperty('uats-fill', 'fill-color', colorExpr(layer));
            renderLegend(layer);
          });
        });

        // Hover highlight
        let hoveredId = null;
        map.on('mousemove', 'uats-fill', e => {
          if (e.features.length === 0) return;
          if (hoveredId !== null) map.setFeatureState({source:'uats', id: hoveredId}, {hover: false});
          hoveredId = e.features[0].id;
          map.setFeatureState({source:'uats', id: hoveredId}, {hover: true});
          map.getCanvas().style.cursor = 'pointer';
        });
        map.on('mouseleave', 'uats-fill', () => {
          if (hoveredId !== null) map.setFeatureState({source:'uats', id: hoveredId}, {hover: false});
          hoveredId = null;
          map.getCanvas().style.cursor = '';
        });

        // Click → panel
        map.on('click', 'uats-fill', e => {
          if (e.features.length) showPanel(e.features[0].properties);
        });

        document.getElementById('panel-close').addEventListener('click', () => {
          document.getElementById('map-panel').style.display = 'none';
        });
      });
    })
    .catch(err => {
      document.getElementById('map-wrap').innerHTML =
        `<div style="padding:40px;color:#991b1b;background:#fee2e2;border-radius:10px">Eroare la încărcarea hărții: ${err.message}</div>`;
    });
})();
</script>
"""
    return page_shell("Harta Costurilor", "harta.html", body, extra_head, extra_scripts)


def gen_categorii() -> str:
    """Category Explorer — per-category product spread ranking.

    Loads docs/data/categories/index.json then per-category JSON on demand.
    Each product is ranked by cross-network price ratio so users can instantly
    see where savings are largest within a product type.
    """
    extra_head = """
<style>
.cat-tabs { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 20px; }
.cat-tab { padding: 8px 16px; border-radius: 8px; border: 1px solid var(--border);
           background: var(--card); cursor: pointer; font-size: 13.5px; font-weight: 500;
           color: var(--text); transition: all .15s; }
.cat-tab:hover { border-color: var(--primary); color: var(--primary); }
.cat-tab.active { background: var(--primary); color: #fff; border-color: var(--primary); }
.cat-tab .badge { font-size: 11px; opacity: .75; margin-left: 5px; }
.cat-summary { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin-bottom: 20px; }
.cat-leader { display: flex; flex-direction: column; gap: 6px; }
.cat-leader-row { display: grid; grid-template-columns: 90px 1fr 40px; align-items: center; gap: 10px; font-size: 13px; }
.cat-leader-row .net { font-weight: 600; }
.cat-leader-row .bar-wrap { background: #f1f5f9; border-radius: 4px; height: 18px; position: relative; overflow: hidden; }
.cat-leader-row .bar-fill { position: absolute; left: 0; top: 0; bottom: 0; background: var(--primary-light); border-radius: 4px; }
.cat-leader-row .cnt { color: var(--muted); text-align: right; }
.cat-controls { display: flex; flex-wrap: wrap; gap: 12px; align-items: end; margin-bottom: 16px; }
.cat-controls .ctl { display: flex; flex-direction: column; }
.cat-controls label { font-size: 11.5px; color: var(--muted); text-transform: uppercase; letter-spacing: .3px; margin-bottom: 6px; }
.cat-controls input, .cat-controls select { padding: 8px 12px; border-radius: 6px; border: 1px solid var(--border); background: var(--card); font-size: 14px; }
.cat-controls .meta { font-size: 12px; color: var(--muted); margin-left: auto; }
.cat-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 12px; }
.cat-card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; }
.cat-card .product { font-weight: 600; font-size: 14px; margin-bottom: 6px; line-height: 1.3; }
.cat-card .flow { font-size: 13px; margin-bottom: 6px; }
.cat-card .flow .net-cheap { color: var(--success); font-weight: 600; }
.cat-card .flow .net-pricey { color: var(--danger); font-weight: 600; }
.cat-card .flow .arrow { color: var(--muted); margin: 0 5px; }
.cat-card .stats { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.cat-card .ratio-badge { display: inline-block; background: var(--primary-light); color: var(--primary); padding: 2px 9px; border-radius: 10px; font-size: 12px; font-weight: 600; }
.cat-card .save-badge { color: var(--success); font-size: 13px; font-weight: 600; }
.cat-card details { margin-top: 8px; }
.cat-card details summary { cursor: pointer; font-size: 12px; color: var(--muted); list-style: none; }
.cat-card details summary::before { content: "▸ "; }
.cat-card details[open] summary::before { content: "▾ "; }
.cat-card .by-net { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 6px; }
.cat-card .by-net .chip { font-size: 11.5px; padding: 2px 8px; border-radius: 10px; background: #f1f5f9; }
.cat-card .by-net .chip.cheap { background: #dcfce7; color: #166534; font-weight: 600; }
.cat-card .by-net .chip.pricey { background: #fee2e2; color: #991b1b; font-weight: 600; }
.cat-card a.compare-link { font-size: 12px; color: var(--primary); text-decoration: none; margin-left: 6px; }
.cat-card a.compare-link:hover { text-decoration: underline; }
.cat-pager { display: flex; justify-content: center; margin-top: 20px; }
.cat-pager button { padding: 8px 18px; border-radius: 6px; border: 1px solid var(--border); background: var(--card); cursor: pointer; font-size: 13.5px; }
.cat-pager button:hover { border-color: var(--primary); color: var(--primary); }
@media (max-width: 640px) {
  .cat-summary { grid-template-columns: 1fr; }
  .cat-grid { grid-template-columns: 1fr; }
  .cat-controls .ctl { width: 100%; }
  .cat-controls input, .cat-controls select { width: 100%; box-sizing: border-box; }
}
</style>
"""
    body = """
<div class="container">
  <h1>Explorator Categorii</h1>
  <p class="subtitle">Produse din aceeași categorie, prețuri diferite în funcție de rețea. Alege categoria, sortează după economii.</p>

  <div class="cat-tabs" id="cat-tabs"></div>

  <div class="cat-summary" id="cat-summary"></div>

  <div class="card">
    <div class="card-title">Rețeaua cea mai ieftină — câte produse din această categorie</div>
    <div class="cat-leader" id="cat-leader"></div>
  </div>

  <div class="card">
    <div class="cat-controls">
      <div class="ctl">
        <label for="cat-search">Caută produs</label>
        <input id="cat-search" type="search" placeholder="ex. ulei, cafea..." autocomplete="off">
      </div>
      <div class="ctl">
        <label for="cat-sort">Sortare</label>
        <select id="cat-sort">
          <option value="ratio">Diferență maximă (×)</option>
          <option value="save_lei">Economii (lei)</option>
          <option value="save_pct">Economii (%)</option>
        </select>
      </div>
      <div class="ctl">
        <label for="cat-min-net">Rețele min.</label>
        <select id="cat-min-net">
          <option value="2">2+</option>
          <option value="3">3+</option>
          <option value="4">4+</option>
          <option value="5">5+</option>
        </select>
      </div>
      <div class="meta" id="cat-meta"></div>
    </div>
    <div class="cat-grid" id="cat-grid"></div>
  </div>
  <div class="cat-pager"><button id="cat-more" style="display:none">Arată mai multe</button></div>
</div>
"""
    extra_scripts = """
<script>
(function(){
  const FMT = new Intl.NumberFormat('ro-RO', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const PAGE = 24;
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  let INDEX = null;
  let catCache = {};
  let current = null;
  let filtered = [];
  let shown = 0;

  async function loadIndex() {
    const r = await fetch('data/categories/index.json', {cache:'no-cache'});
    return r.json();
  }
  async function loadCat(id) {
    if (catCache[id]) return catCache[id];
    const r = await fetch(`data/categories/${id}.json`, {cache:'no-cache'});
    catCache[id] = await r.json();
    return catCache[id];
  }

  function renderTabs() {
    const el = document.getElementById('cat-tabs');
    el.innerHTML = INDEX.categories.map(c =>
      `<button class="cat-tab ${c.id === current ? 'active' : ''}" data-id="${c.id}">
        ${esc(c.name)}<span class="badge">${c.products_with_spread}</span>
      </button>`
    ).join('');
    el.querySelectorAll('.cat-tab').forEach(btn => {
      btn.addEventListener('click', () => { current = +btn.dataset.id; renderAll(); });
    });
  }

  function renderSummary(cat) {
    const el = document.getElementById('cat-summary');
    const topItem = cat.items[0];
    const totalSave = cat.items.reduce((s, x) => s + x.save_lei, 0);
    el.innerHTML = `
      <div class="kpi">
        <div class="kpi-label">Produse comparabile</div>
        <div class="kpi-value">${cat.products_with_spread}</div>
        <div class="kpi-trend" style="font-size:12px;color:var(--muted)">din ${cat.products_total} urmărite</div>
      </div>
      <div class="kpi">
        <div class="kpi-label">Cea mai mare diferență</div>
        <div class="kpi-value">${topItem ? topItem.ratio + '×' : '—'}</div>
        <div class="kpi-trend" style="font-size:12px;color:var(--muted)">${topItem ? esc(topItem.product) : ''}</div>
      </div>
      <div class="kpi">
        <div class="kpi-label">Economii totale posibile</div>
        <div class="kpi-value" style="color:var(--success)">${FMT.format(totalSave)} lei</div>
        <div class="kpi-trend" style="font-size:12px;color:var(--muted)">suma diferențelor pentru toate produsele</div>
      </div>`;
  }

  function renderLeader(cat) {
    const el = document.getElementById('cat-leader');
    if (!cat.leaderboard.length) { el.innerHTML = '<p style="color:var(--muted);font-size:13px">Insuficiente date.</p>'; return; }
    const max = cat.leaderboard[0].cheapest_count;
    el.innerHTML = cat.leaderboard.map(({network, cheapest_count}) => `
      <div class="cat-leader-row">
        <div class="net">${esc(network)}</div>
        <div class="bar-wrap">
          <div class="bar-fill" style="width:${Math.max(2, cheapest_count/max*100)}%"></div>
        </div>
        <div class="cnt">${cheapest_count}</div>
      </div>`).join('');
  }

  function applyFilters(cat) {
    const q = document.getElementById('cat-search').value.trim().toLowerCase();
    const sort = document.getElementById('cat-sort').value;
    const minNet = +document.getElementById('cat-min-net').value;
    filtered = cat.items.filter(it => {
      if (it.n_networks < minNet) return false;
      if (q && it.product.toLowerCase().indexOf(q) === -1) return false;
      return true;
    });
    filtered.sort((a, b) => b[sort] - a[sort]);
    shown = 0;
    document.getElementById('cat-grid').innerHTML = '';
    renderMore();
    document.getElementById('cat-meta').textContent = `${filtered.length} produs(e)`;
  }

  function renderCard(it) {
    const chips = it.by_network.map(([n, p]) => {
      let cls = n === it.cheapest.network ? 'cheap' : (n === it.priciest.network ? 'pricey' : '');
      return `<span class="chip ${cls}">${esc(n)} ${FMT.format(p)}</span>`;
    }).join('');
    return `<div class="cat-card">
      <div class="product">${esc(it.product)}
        <a class="compare-link" href="compare.html?pid=${it.product_id}" title="Comparare">↗</a>
      </div>
      <div class="flow">
        <span class="net-cheap">${esc(it.cheapest.network)} ${FMT.format(it.cheapest.price)} lei</span>
        <span class="arrow">→</span>
        <span class="net-pricey">${esc(it.priciest.network)} ${FMT.format(it.priciest.price)} lei</span>
      </div>
      <div class="stats">
        <span class="ratio-badge">${it.ratio}×</span>
        <span class="save-badge">+${FMT.format(it.save_lei)} lei</span>
        <span style="font-size:12px;color:var(--muted)">${it.n_networks} rețele</span>
      </div>
      <details>
        <summary>Toate rețelele (${it.by_network.length})</summary>
        <div class="by-net">${chips}</div>
      </details>
    </div>`;
  }

  function renderMore() {
    const next = filtered.slice(shown, shown + PAGE);
    const grid = document.getElementById('cat-grid');
    if (shown === 0 && next.length === 0) {
      grid.innerHTML = '<div style="padding:24px;color:var(--muted);text-align:center">Niciun produs găsit.</div>';
      document.getElementById('cat-more').style.display = 'none';
      return;
    }
    grid.insertAdjacentHTML('beforeend', next.map(renderCard).join(''));
    shown += next.length;
    document.getElementById('cat-more').style.display = shown < filtered.length ? '' : 'none';
  }

  function renderAll() {
    // Update tab active state
    document.querySelectorAll('.cat-tab').forEach(b =>
      b.classList.toggle('active', +b.dataset.id === current));
    loadCat(current).then(cat => {
      renderSummary(cat);
      renderLeader(cat);
      applyFilters(cat);
    });
  }

  ['cat-search','cat-sort','cat-min-net'].forEach(id => {
    const ev = id === 'cat-search' ? 'input' : 'change';
    document.getElementById(id).addEventListener(ev, () => {
      if (!current) return;
      loadCat(current).then(cat => applyFilters(cat));
    });
  });
  document.getElementById('cat-more').addEventListener('click', renderMore);

  loadIndex()
    .then(idx => {
      INDEX = idx;
      current = INDEX.categories[0]?.id;
      renderTabs();
      renderAll();
    })
    .catch(err => {
      document.querySelector('.container').insertAdjacentHTML('beforeend',
        `<div class="card" style="background:#fee2e2;color:#991b1b">Eroare: ${esc(err.message)}</div>`);
    });
})();
</script>
"""
    return page_shell("Explorator Categorii", "categorii.html", body, extra_head, extra_scripts)


def gen_anomalii() -> str:
    """Anomalii — daily feed of products whose price varies sharply across networks.

    Loads `docs/data/anomalies_today.json` (built by build_anomalies.py).
    Pure client-side filtering and rendering; no server data passed in.
    """
    extra_head = """
<style>
.anom-summary { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin-bottom: 20px; }
.anom-controls { display: flex; flex-wrap: wrap; gap: 12px; align-items: end; margin-bottom: 16px; }
.anom-controls .ctl { display: flex; flex-direction: column; }
.anom-controls label { font-size: 11.5px; color: var(--muted);
                        text-transform: uppercase; letter-spacing: .3px; margin-bottom: 6px; }
.anom-controls input, .anom-controls select {
  padding: 8px 12px; border-radius: 6px; border: 1px solid var(--border);
  background: var(--card); font-size: 14px; min-width: 180px;
}
.anom-controls .meta { font-size: 12px; color: var(--muted); margin-left: auto; }
.anom-list { display: grid; grid-template-columns: 1fr; gap: 12px; }
.anom-card { background: var(--card); border: 1px solid var(--border); border-radius: 10px;
              padding: 16px 18px; display: grid; grid-template-columns: 1fr 220px; gap: 18px;
              align-items: center; transition: box-shadow .15s, transform .15s; }
.anom-card:hover { box-shadow: 0 4px 18px rgba(0,0,0,.08); }
.anom-card .product { font-weight: 600; font-size: 15px; line-height: 1.3; margin-bottom: 4px; }
.anom-card .meta { font-size: 12px; color: var(--muted); }
.anom-card .meta .pill { display: inline-block; background: #f1f5f9; padding: 2px 8px;
                          border-radius: 10px; margin-right: 4px; }
.anom-card .flow { margin-top: 10px; font-size: 13.5px; }
.anom-card .flow .net-cheap { color: var(--success); font-weight: 600; }
.anom-card .flow .net-pricey { color: var(--danger); font-weight: 600; }
.anom-card .flow .arrow { color: var(--muted); margin: 0 6px; }
.anom-savings { text-align: right; }
.anom-savings .save-lei { font-size: 22px; font-weight: 700; color: var(--success); }
.anom-savings .save-pct { font-size: 13px; color: var(--muted); margin-top: 2px; }
.anom-savings .ratio { display: inline-block; background: var(--primary-light); color: var(--primary);
                        padding: 3px 10px; border-radius: 12px; font-size: 12px; font-weight: 600;
                        margin-top: 6px; }
.anom-card details { margin-top: 10px; grid-column: 1 / -1; }
.anom-card details summary { cursor: pointer; font-size: 12.5px; color: var(--muted);
                              padding: 4px 0; list-style: none; }
.anom-card details summary::before { content: "▸ "; }
.anom-card details[open] summary::before { content: "▾ "; }
.anom-card .by-net { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.anom-card .by-net .chip { font-size: 12px; padding: 3px 10px; border-radius: 12px;
                            background: #f1f5f9; }
.anom-card .by-net .chip.cheap { background: #dcfce7; color: #166534; font-weight: 600; }
.anom-card .by-net .chip.pricey { background: #fee2e2; color: #991b1b; font-weight: 600; }
.anom-card a.compare-link { font-size: 12px; color: var(--primary); text-decoration: none;
                             margin-left: 10px; }
.anom-card a.compare-link:hover { text-decoration: underline; }
.anom-empty { text-align: center; padding: 40px; color: var(--muted); }
.anom-pager { display: flex; justify-content: center; margin-top: 20px; }
.anom-pager button { padding: 8px 18px; border-radius: 6px; border: 1px solid var(--border);
                      background: var(--card); cursor: pointer; font-size: 13.5px; }
.anom-pager button:hover { border-color: var(--primary); color: var(--primary); }
@media (max-width: 640px) {
  .anom-summary { grid-template-columns: 1fr; }
  .anom-card { grid-template-columns: 1fr; }
  .anom-savings { text-align: left; display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
  .anom-controls input, .anom-controls select { min-width: 100%; }
  .anom-controls .ctl { width: 100%; }
}
</style>
"""
    body = """
<div class="container">
  <h1>Anomalii de preț</h1>
  <p class="subtitle">Aceleași produse, prețuri foarte diferite în funcție de rețea. Cumpără-le de unde sunt cele mai ieftine.</p>

  <div class="cos-disclaimer">
    <b>Notă:</b> Date la zi din monitorulpreturilor.info. Comparăm <i>același produs</i> (același cod) între rețele, exclusiv consumatorii (SELGROS exclus). Prețuri evident eronate filtrate (peste 3× sau sub 0.3× din mediană).
  </div>

  <div class="anom-summary" id="anom-summary"></div>

  <div class="card">
    <div class="anom-controls">
      <div class="ctl">
        <label for="anom-search">Caută produs / brand</label>
        <input id="anom-search" type="search" placeholder="ex. Lavazza, ulei, paste..." autocomplete="off">
      </div>
      <div class="ctl">
        <label for="anom-cat">Categorie</label>
        <select id="anom-cat"><option value="">Toate</option></select>
      </div>
      <div class="ctl">
        <label for="anom-cheap">Cel mai ieftin la</label>
        <select id="anom-cheap"><option value="">Orice rețea</option></select>
      </div>
      <div class="ctl">
        <label for="anom-min">Diferență minimă</label>
        <select id="anom-min">
          <option value="1.5">1.5× (50%+)</option>
          <option value="2">2× (100%+)</option>
          <option value="3">3× (200%+)</option>
        </select>
      </div>
      <div class="meta" id="anom-meta"></div>
    </div>
  </div>

  <div class="anom-list" id="anom-list"></div>
  <div class="anom-pager"><button id="anom-more" style="display:none">Arată mai multe</button></div>
</div>
"""
    extra_scripts = """
<script>
(function(){
  const FMT = new Intl.NumberFormat('ro-RO', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const PAGE_SIZE = 30;
  let DATA = null;
  let filtered = [];
  let shown = 0;

  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[c]));

  function renderSummary(){
    const items = DATA.items;
    const top = items[0];
    const totalSave = items.slice(0, 10).reduce((s, x) => s + x.save_lei, 0);
    const elem = document.getElementById('anom-summary');
    elem.innerHTML = `
      <div class="kpi">
        <div class="kpi-label">Anomalii detectate azi</div>
        <div class="kpi-value">${items.length}</div>
        <div class="kpi-trend" style="font-size:12px;color:var(--muted)">Date la ${esc(DATA.as_of)}</div>
      </div>
      <div class="kpi">
        <div class="kpi-label">Cea mai mare diferență</div>
        <div class="kpi-value">${top ? top.ratio + '×' : '—'}</div>
        <div class="kpi-trend" style="font-size:12px;color:var(--muted)">${top ? esc(top.product) : ''}</div>
      </div>
      <div class="kpi">
        <div class="kpi-label">Economii potențiale (top 10)</div>
        <div class="kpi-value" style="color:var(--success)">${FMT.format(totalSave)} lei</div>
        <div class="kpi-trend" style="font-size:12px;color:var(--muted)">cumpărând la rețeaua ieftină</div>
      </div>`;
  }

  function populateFilters(){
    const cats = Array.from(new Set(DATA.items.map(x => x.category).filter(Boolean))).sort();
    const cs = document.getElementById('anom-cat');
    for (const c of cats) {
      const o = document.createElement('option');
      o.value = c; o.textContent = c;
      cs.appendChild(o);
    }
    const nets = Array.from(new Set(DATA.items.map(x => x.cheapest.network))).sort();
    const ns = document.getElementById('anom-cheap');
    for (const n of nets) {
      const o = document.createElement('option');
      o.value = n; o.textContent = n;
      ns.appendChild(o);
    }
  }

  function applyFilters(){
    const q = document.getElementById('anom-search').value.trim().toLowerCase();
    const cat = document.getElementById('anom-cat').value;
    const cheap = document.getElementById('anom-cheap').value;
    const minR = parseFloat(document.getElementById('anom-min').value);
    filtered = DATA.items.filter(it => {
      if (it.ratio < minR) return false;
      if (cat && it.category !== cat) return false;
      if (cheap && it.cheapest.network !== cheap) return false;
      if (q) {
        const hay = ((it.product || '') + ' ' + (it.brand || '')).toLowerCase();
        if (hay.indexOf(q) === -1) return false;
      }
      return true;
    });
    shown = 0;
    document.getElementById('anom-list').innerHTML = '';
    renderMore();
    document.getElementById('anom-meta').textContent =
      `${filtered.length} produs(e) găsit(e)`;
  }

  function renderCard(it){
    const meta = [];
    if (it.brand) meta.push(`<span class="pill">${esc(it.brand)}</span>`);
    if (it.unit) meta.push(`<span class="pill">${esc(it.unit)}</span>`);
    if (it.category) meta.push(esc(it.category));
    const chips = it.by_network.map(([n, p]) => {
      let cls = '';
      if (n === it.cheapest.network) cls = 'cheap';
      else if (n === it.priciest.network) cls = 'pricey';
      return `<span class="chip ${cls}">${esc(n)} ${FMT.format(p)}</span>`;
    }).join('');
    return `<div class="anom-card">
      <div>
        <div class="product">${esc(it.product)}
          <a class="compare-link" href="compare.html?pid=${it.product_id}" title="Vezi în Comparare">↗</a>
        </div>
        <div class="meta">${meta.join(' ')}</div>
        <div class="flow">
          <span class="net-cheap">${esc(it.cheapest.network)} ${FMT.format(it.cheapest.price)} lei</span>
          <span class="arrow">→</span>
          <span class="net-pricey">${esc(it.priciest.network)} ${FMT.format(it.priciest.price)} lei</span>
        </div>
        <details>
          <summary>Toate rețelele (${it.by_network.length})</summary>
          <div class="by-net">${chips}</div>
        </details>
      </div>
      <div class="anom-savings">
        <div class="save-lei">+${FMT.format(it.save_lei)} lei</div>
        <div class="save-pct">${it.save_pct}% mai scump</div>
        <div><span class="ratio">${it.ratio}×</span></div>
      </div>
    </div>`;
  }

  function renderMore(){
    const next = filtered.slice(shown, shown + PAGE_SIZE);
    const list = document.getElementById('anom-list');
    if (shown === 0 && next.length === 0) {
      list.innerHTML = '<div class="anom-empty">Niciun rezultat. Schimbă filtrele.</div>';
      document.getElementById('anom-more').style.display = 'none';
      return;
    }
    list.insertAdjacentHTML('beforeend', next.map(renderCard).join(''));
    shown += next.length;
    document.getElementById('anom-more').style.display =
      shown < filtered.length ? '' : 'none';
  }

  fetch('data/anomalies_today.json', {cache: 'no-cache'})
    .then(r => r.json())
    .then(d => {
      DATA = d;
      renderSummary();
      populateFilters();
      ['anom-search','anom-cat','anom-cheap','anom-min'].forEach(id => {
        const ev = id === 'anom-search' ? 'input' : 'change';
        document.getElementById(id).addEventListener(ev, applyFilters);
      });
      document.getElementById('anom-more').addEventListener('click', renderMore);
      applyFilters();
    })
    .catch(err => {
      document.querySelector('.container').insertAdjacentHTML('beforeend',
        `<div class="card" style="background:#fee2e2; color:#991b1b">Eroare la încărcarea datelor: ${esc(err.message)}</div>`);
    });
})();
</script>
"""
    return page_shell("Anomalii de preț", "anomalii.html", body, extra_head, extra_scripts)


# ── Aproape de tine ──────────────────────────────────────────────────────

def gen_aproape() -> str:
    extra_head = """
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<style>
#map { height: 480px; border-radius: 8px; margin: 16px 0; }
.location-panel { display:flex; gap:12px; flex-wrap:wrap; align-items:flex-end; margin-bottom:16px; }
.location-panel label { font-size:13px; font-weight:600; display:block; margin-bottom:4px; color:#555; }
.location-panel input[type=number], .location-panel input[type=text] {
  border:1px solid #ccc; border-radius:6px; padding:8px 10px; font-size:14px; width:130px;
}
.location-panel button {
  padding:9px 18px; border:none; border-radius:6px; cursor:pointer; font-size:14px; font-weight:600;
}
#btn-geo { background:#1976d2; color:#fff; }
#btn-geo:hover { background:#1565c0; }
#btn-manual { background:#43a047; color:#fff; }
#btn-manual:hover { background:#388e3c; }
#status-msg { font-size:13px; color:#666; margin-top:6px; min-height:18px; }
.results-section { margin-top:20px; }
.results-section h3 { margin:0 0 12px; font-size:16px; }
.store-list { display:grid; grid-template-columns:repeat(auto-fill,minmax(300px,1fr)); gap:12px; }
.store-card {
  background:#fff; border:1px solid #e0e0e0; border-radius:8px;
  padding:14px; box-shadow:0 1px 3px rgba(0,0,0,.06);
  display:flex; flex-direction:column; gap:4px;
}
.store-card .sname { font-weight:700; font-size:14px; }
.store-card .snet  { font-size:12px; color:#888; }
.store-card .sdist { font-size:13px; color:#1976d2; font-weight:600; }
.store-card .saddr { font-size:12px; color:#666; }
.store-card .sbask { font-size:13px; color:#2e7d32; font-weight:600; margin-top:4px; }
.store-card .sbask span { font-weight:400; color:#555; }
.net-badge {
  display:inline-block; padding:2px 8px; border-radius:12px; font-size:11px;
  font-weight:700; color:#fff; background:#555; margin-bottom:4px;
}
.filter-bar { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:14px; align-items:center; }
.filter-bar label { font-size:13px; color:#555; }
.filter-bar select { border:1px solid #ccc; border-radius:6px; padding:6px 10px; font-size:13px; }
#radius-val { font-weight:700; color:#1976d2; }
.pin-me { width:18px; height:18px; background:#e53935; border-radius:50%; border:3px solid #fff;
  box-shadow:0 0 0 2px #e53935; display:inline-block; }
</style>"""

    extra_scripts = """
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const DATA_URL = 'data/stores_index.json';
const FIELDS = ['id','name','addr','lat','lon','network','uat_id','uat_name','basket_min_month'];

let map, userMarker, storeLayer;
let allStores = [];
let userLat = null, userLon = null;

// ── Haversine distance (km) ─────────────────────────────────────────────
function haversine(lat1, lon1, lat2, lon2) {
  const R = 6371, dLat = (lat2-lat1)*Math.PI/180, dLon = (lon2-lon1)*Math.PI/180;
  const a = Math.sin(dLat/2)**2 + Math.cos(lat1*Math.PI/180)*Math.cos(lat2*Math.PI/180)*Math.sin(dLon/2)**2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
}

// ── Map init ────────────────────────────────────────────────────────────
function initMap(lat, lon) {
  if (!map) {
    map = L.map('map').setView([lat, lon], 13);
    L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
      attribution: '© OpenStreetMap, © CARTO', maxZoom: 19
    }).addTo(map);
    storeLayer = L.layerGroup().addTo(map);
  } else {
    map.setView([lat, lon], 13);
  }
}

function placeUserPin(lat, lon) {
  if (userMarker) map.removeLayer(userMarker);
  userMarker = L.circleMarker([lat, lon], {
    radius: 10, color: '#e53935', fillColor: '#e53935', fillOpacity: 0.9, weight: 3
  }).addTo(map).bindPopup('<b>Locația ta</b>').openPopup();
}

// ── Render results ──────────────────────────────────────────────────────
const NET_COLORS = {
  'Lidl':'#0050aa','Kaufland':'#cc0000','Carrefour':'#004a97',
  'Auchan':'#e2001a','Penny':'#cc0000','Profi':'#e8000d',
  'Mega Image':'#007d3e','Cora':'#003087','Supeco':'#007e36',
};

function render(stores, radiusKm, netFilter) {
  storeLayer.clearLayers();
  const list = document.getElementById('store-list');
  const countEl = document.getElementById('result-count');
  list.innerHTML = '';

  const filtered = stores.filter(s => {
    if (s._dist > radiusKm) return false;
    if (netFilter && s.network !== netFilter) return false;
    return true;
  });

  filtered.slice(0, 200).forEach(s => {
    const color = NET_COLORS[s.network] || '#555';
    L.circleMarker([s.lat, s.lon], {
      radius: 7, color, fillColor: color, fillOpacity: 0.8, weight: 2
    }).addTo(storeLayer).bindPopup(
      `<b>${s.name}</b><br>${s.network}<br>${s.addr}<br><i>${s._dist.toFixed(1)} km</i>`
    );
    const bask = s.basket_min_month
      ? `<div class="sbask">~${s.basket_min_month} lei/lună <span>(coș camara, cel mai ieftin)</span></div>`
      : '';
    list.innerHTML += `
      <div class="store-card">
        <div><span class="net-badge" style="background:${color}">${s.network}</span></div>
        <div class="sname">${s.name}</div>
        <div class="saddr">${s.addr || s.uat_name}</div>
        <div class="sdist">${s._dist.toFixed(1)} km</div>
        ${bask}
      </div>`;
  });

  const showing = Math.min(filtered.length, 200);
  countEl.textContent = showing === 0
    ? 'Niciun magazin în raza selectată.'
    : `${showing} magazine${filtered.length > 200 ? ` (din ${filtered.length})` : ''} în ${radiusKm} km`;
}

function updateResults() {
  if (userLat === null) return;
  const radiusKm = parseFloat(document.getElementById('radius').value) || 5;
  const netFilter = document.getElementById('net-filter').value;
  document.getElementById('radius-val').textContent = radiusKm + ' km';

  const withDist = allStores.map(s => ({...s, _dist: haversine(userLat, userLon, s.lat, s.lon)}));
  withDist.sort((a, b) => a._dist - b._dist);

  // Populate network filter once
  const netSel = document.getElementById('net-filter');
  if (netSel.options.length <= 1) {
    const nets = [...new Set(allStores.map(s => s.network))].sort();
    nets.forEach(n => netSel.innerHTML += `<option value="${n}">${n}</option>`);
  }

  render(withDist, radiusKm, netFilter);
}

function setLocation(lat, lon, label) {
  userLat = lat; userLon = lon;
  document.getElementById('status-msg').textContent = `📍 ${label}`;
  initMap(lat, lon);
  placeUserPin(lat, lon);
  updateResults();
}

// ── Geolocation ─────────────────────────────────────────────────────────
document.getElementById('btn-geo').addEventListener('click', () => {
  if (!navigator.geolocation) {
    document.getElementById('status-msg').textContent = 'Geolocation not available in this browser.';
    return;
  }
  document.getElementById('status-msg').textContent = 'Se determină locația…';
  navigator.geolocation.getCurrentPosition(
    pos => setLocation(pos.coords.latitude, pos.coords.longitude,
      `GPS (${pos.coords.latitude.toFixed(4)}, ${pos.coords.longitude.toFixed(4)})`),
    err => {
      document.getElementById('status-msg').textContent =
        'Nu s-a putut determina locația. Încearcă coordonatele manuale.';
    },
    { timeout: 10000 }
  );
});

// ── Manual coordinates ──────────────────────────────────────────────────
document.getElementById('btn-manual').addEventListener('click', () => {
  const lat = parseFloat(document.getElementById('inp-lat').value);
  const lon = parseFloat(document.getElementById('inp-lon').value);
  if (isNaN(lat) || isNaN(lon) || lat < 43 || lat > 48.5 || lon < 20 || lon > 30) {
    document.getElementById('status-msg').textContent =
      'Coordonate invalide. România: lat 43–48.5, lon 20–30.';
    return;
  }
  setLocation(lat, lon, `Manual (${lat.toFixed(4)}, ${lon.toFixed(4)})`);
});

document.getElementById('inp-lat').addEventListener('keydown', e => {
  if (e.key === 'Enter') document.getElementById('btn-manual').click();
});
document.getElementById('inp-lon').addEventListener('keydown', e => {
  if (e.key === 'Enter') document.getElementById('btn-manual').click();
});

// ── Filters ─────────────────────────────────────────────────────────────
document.getElementById('radius').addEventListener('input', updateResults);
document.getElementById('net-filter').addEventListener('change', updateResults);

// ── Load data ───────────────────────────────────────────────────────────
fetch(DATA_URL).then(r => r.json()).then(data => {
  allStores = data.stores.map(arr => {
    const obj = {};
    data.fields.forEach((f, i) => obj[f] = arr[i]);
    return obj;
  });
  document.getElementById('status-msg').textContent =
    `${allStores.length} magazine încărcate. Folosiți butonul GPS sau introduceți coordonatele manual.`;
}).catch(err => {
  document.getElementById('status-msg').textContent = 'Eroare la încărcarea datelor.';
  console.error(err);
});
</script>"""

    body = """
<div class="container" style="max-width:1100px;margin:0 auto;padding:20px">
  <h1 style="font-size:22px;margin-bottom:4px">Aproape de tine</h1>
  <p style="color:#666;margin-bottom:20px;font-size:14px">
    Magazine din apropiere, ordonate după distanță. Alege raza și rețeaua preferată.
    Costul coșului de cumpărături este calculat pentru cel mai ieftin furnizor din zona ta (UAT).
  </p>

  <div class="location-panel">
    <div>
      <label>Detectare automată</label>
      <button id="btn-geo">📍 Folosește GPS-ul</button>
    </div>
    <div style="display:flex;align-items:center;padding:0 8px;color:#aaa;font-size:18px;align-self:flex-end;padding-bottom:8px">sau</div>
    <div>
      <label>Latitudine</label>
      <input type="number" id="inp-lat" placeholder="44.4268" step="0.0001" min="43" max="48.5" value="44.4268"/>
    </div>
    <div>
      <label>Longitudine</label>
      <input type="number" id="inp-lon" placeholder="26.1025" step="0.0001" min="20" max="30" value="26.1025"/>
    </div>
    <div>
      <button id="btn-manual">Caută</button>
    </div>
  </div>
  <div id="status-msg">Se încarcă datele…</div>

  <div id="map"></div>

  <div class="filter-bar" style="margin-top:8px">
    <div>
      <label>Raza: <span id="radius-val">5 km</span></label>
      <input type="range" id="radius" min="1" max="50" value="5" step="1" style="width:160px;vertical-align:middle"/>
    </div>
    <div>
      <label>Rețea: </label>
      <select id="net-filter"><option value="">Toate</option></select>
    </div>
  </div>

  <div class="results-section">
    <div id="result-count" style="font-size:13px;color:#666;margin-bottom:10px"></div>
    <div class="store-list" id="store-list"></div>
  </div>
</div>"""

    return page_shell("Aproape de tine", "aproape.html", body, extra_head, extra_scripts)


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
    analytics_data  = load_analytics_data(conn)
    stores          = load_stores(conn)
    gas_stations    = load_gas_map_data(conn)
    metod_stats     = load_metodologie_stats(conn)

    print("Building compare data files...")
    n_products = build_compare_data_files(conn, out_dir)
    print(f"  data/products/   {n_products} CSV files")

    conn.close()

    pages = {
        "index.html":       gen_index(summary, price_index, fuel_prices, fuel_trends),
        "tablou.html":      gen_tablou(summary, price_index, fuel_prices, fuel_trends),
        "cos.html":         gen_cos(),
        "anomalii.html":    gen_anomalii(),
        "categorii.html":   gen_categorii(),
        "harta.html":       gen_harta(),
        "price-index.html": gen_price_index(price_index, by_category),
        "trends.html":      gen_trends(network_trends, category_trends, fuel_trends),
        "compare.html":     gen_compare(compare_index),
        "fuel.html":        gen_fuel(fuel_prices),
        "analytics.html":   gen_analytics(analytics_data),
        "pipeline.html":    gen_pipeline(runs, coverage, summary),
        "stores_map.html":  gen_stores_map(stores),
        "gas_map.html":     gen_gas_map(gas_stations),
        "inflatie.html":    gen_inflatie(),
        "povesti.html":     gen_povesti(),
        "metodologie.html": gen_metodologie(summary, metod_stats),
        "date-deschise.html": gen_date_deschise(summary, metod_stats),
        "aproape.html":       gen_aproape(),
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
