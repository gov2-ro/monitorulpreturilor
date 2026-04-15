#!/usr/bin/env python3
"""
Generate docs/stores_map.html from data/prices.db.
Reads retail stores with network names and writes a self-contained Leaflet map.

Usage:
    python generate_map.py
    python generate_map.py --db data/prices.db --out docs/stores_map.html
"""

import argparse
import json
import sqlite3
from pathlib import Path

DB_PATH  = Path("data/prices.db")
OUT_PATH = Path("docs/stores_map.html")

# Color palette per network (by normalized name substring)
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
DEFAULT_COLOR = "#95a5a6"


def network_color(name: str) -> str:
    if not name:
        return DEFAULT_COLOR
    upper = name.upper()
    for key, color in NETWORK_COLORS.items():
        if key in upper:
            return color
    return DEFAULT_COLOR


def load_stores(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT s.id, s.name, s.addr, s.lat, s.lon,
               COALESCE(n.name, '') AS network
        FROM stores s
        LEFT JOIN retail_networks n ON s.network_id = n.id
        WHERE s.lat IS NOT NULL AND s.lon IS NOT NULL
          AND s.lat != 0 AND s.lon != 0
        ORDER BY n.name NULLS LAST, s.name
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def build_legend(stores: list[dict]) -> str:
    counts: dict[str, int] = {}
    colors: dict[str, str] = {}
    for s in stores:
        net = s["network"] or "Unknown"
        counts[net] = counts.get(net, 0) + 1
        colors[net] = network_color(s["network"])

    # Sort by count desc, Unknown last
    order = sorted(counts, key=lambda n: (-counts[n], n == "Unknown", n))
    rows = []
    for net in order:
        rows.append(
            f'<div class="legend-row">'
            f'<span class="dot" style="background:{colors[net]}"></span>'
            f'{net} <span class="cnt">({counts[net]})</span>'
            f'</div>'
        )
    return "\n".join(rows)


def build_stores_json(stores: list[dict]) -> str:
    out = []
    for s in stores:
        out.append({
            "id":      str(s["id"]),
            "name":    s["name"],
            "addr":    s["addr"] or "",
            "lat":     s["lat"],
            "lon":     s["lon"],
            "network": s["network"] or "Unknown",
            "color":   network_color(s["network"]),
        })
    return json.dumps(out, ensure_ascii=False, separators=(",", ":"))


HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="ro">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Monitorul Prețurilor — Harta Magazine</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css"/>
<link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css"/>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: sans-serif; }}
  #map {{ width: 100vw; height: 100vh; }}
  #legend {{
    position: absolute; bottom: 30px; right: 10px; z-index: 1000;
    background: rgba(255,255,255,0.93); border-radius: 6px;
    padding: 10px 14px; box-shadow: 0 1px 5px rgba(0,0,0,.3);
    font-size: 13px; line-height: 1.6;
  }}
  #legend h4 {{ margin-bottom: 6px; font-size: 13px; font-weight: 700; }}
  .legend-row {{ display: flex; align-items: center; gap: 6px; }}
  .dot {{ width: 11px; height: 11px; border-radius: 50%; flex-shrink: 0; }}
  .cnt {{ color: #777; font-size: 11px; }}
</style>
</head>
<body>
<div id="map"></div>
<div id="legend">
  <h4>Rețea</h4>
  {legend}
</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js"></script>
<script>
const stores = {stores_json};

const map = L.map('map').setView([45.9, 24.97], 7);
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  maxZoom: 19
}}).addTo(map);

function circleIcon(color) {{
  return L.divIcon({{
    className: '',
    html: `<svg width="14" height="14" viewBox="0 0 14 14">
      <circle cx="7" cy="7" r="6" fill="${{color}}" stroke="#fff" stroke-width="1.5"/>
    </svg>`,
    iconSize: [14, 14],
    iconAnchor: [7, 7],
    popupAnchor: [0, -8]
  }});
}}

const clusters = L.markerClusterGroup({{ maxClusterRadius: 40 }});

for (const s of stores) {{
  const marker = L.marker([s.lat, s.lon], {{ icon: circleIcon(s.color) }});
  const addr = s.addr ? `<div style="color:#555;font-size:12px">${{s.addr}}</div>` : '';
  const net  = s.network !== 'Unknown'
    ? `<div style="margin-top:3px;font-size:12px;font-weight:600;color:${{s.color}}">${{s.network}}</div>`
    : '';
  marker.bindPopup(`<b>${{s.name}}</b>${{net}}${{addr}}`, {{ maxWidth: 260 }});
  clusters.addLayer(marker);
}}

map.addLayer(clusters);
</script>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="Generate Leaflet stores map from DB")
    parser.add_argument("--db",  default=str(DB_PATH),  help="Path to prices.db")
    parser.add_argument("--out", default=str(OUT_PATH), help="Output HTML path")
    args = parser.parse_args()

    db_path  = Path(args.db)
    out_path = Path(args.out)

    print(f"Reading stores from {db_path} ...")
    stores = load_stores(db_path)
    print(f"  {len(stores)} stores loaded")

    legend      = build_legend(stores)
    stores_json = build_stores_json(stores)

    html = HTML_TEMPLATE.format(legend=legend, stores_json=stores_json)
    out_path.write_text(html, encoding="utf-8")
    print(f"Written → {out_path}  ({len(stores)} markers)")


if __name__ == "__main__":
    main()
