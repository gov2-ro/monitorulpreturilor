#!/usr/bin/env python3
"""Build docs/data/uats.geojson for the choropleth map.

Decodes config/geo/ro-uats.topojson (3175 Romanian UATs), joins with:
  - DB: store count, distinct consumer-network count per UAT
  - docs/data/baskets/camara.json: cheapest/priciest monthly cost per UAT
    (national values used as fallback when UAT has no basket data)

Keeps only UATs that have at least one retail store in our DB.

Output: docs/data/uats.geojson (~1 MB, uncompressed; served statically).
Properties per feature:
  siruta, name, n_stores, n_networks,
  basket_min_month, basket_max_month, basket_n_networks, basket_cheapest_net
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from networks import short, is_b2b  # noqa: E402

DEFAULT_DB = ROOT / "data" / "prices.db"
TOPO_PATH = ROOT / "config" / "geo" / "ro-uats.topojson"
BASKETS_DIR = ROOT / "docs" / "data" / "baskets"
DEFAULT_OUT = ROOT / "docs" / "data" / "uats.geojson"


# ── TopoJSON decode ──────────────────────────────────────────────────────

def _decode_arc(arc, scale, translate):
    pts, x, y = [], 0, 0
    for dx, dy in arc:
        x += dx; y += dy
        pts.append([x * scale[0] + translate[0], y * scale[1] + translate[1]])
    return pts


def _stitch(arc_refs, arcs):
    ring = []
    for ref in arc_refs:
        pts = arcs[ref] if ref >= 0 else arcs[~ref][::-1]
        ring.extend(pts[:-1] if ring else pts)
    return ring


def _geom_coords(g, arcs):
    t = g.get("type")
    if t == "Polygon":
        return {"type": "Polygon", "coordinates": [_stitch(r, arcs) for r in g["arcs"]]}
    if t == "MultiPolygon":
        return {"type": "MultiPolygon",
                "coordinates": [[_stitch(r, arcs) for r in poly] for poly in g["arcs"]]}
    return None


def decode_topojson(path, obj_name):
    """Decode a TopoJSON file → list of {siruta: int, geometry: {...}, nome: str}."""
    with open(path, encoding="utf-8") as f:
        topo = json.load(f)
    tr = topo.get("transform", {})
    scale = tr.get("scale", [1, 1])
    translate = tr.get("translate", [0, 0])
    arcs = [_decode_arc(a, scale, translate) for a in topo["arcs"]]

    out = []
    for g in topo["objects"][obj_name]["geometries"]:
        if g.get("type") is None:
            continue
        coords = _geom_coords(g, arcs)
        if not coords:
            continue
        props = g.get("properties", {})
        out.append({
            "siruta": int(props["siruta"]),
            "nome_topo": props.get("nume", ""),
            "geometry": coords,
        })
    return out


# ── DB lookups ───────────────────────────────────────────────────────────

def fetch_uat_stats(conn):
    """Return {uat_id: {name, n_stores, n_networks}} — consumer nets only.

    Queries stores directly (no join to uats table) so we cover all 835 UATs
    that have stores, including the 555 whose IDs are absent from the uats
    reference table. UAT names fall back to the topojson 'nome' field later.
    """
    # Total stores per UAT (all networks)
    store_counts = {r[0]: r[1] for r in conn.execute("""
        SELECT uat_id, COUNT(*) FROM stores
        WHERE uat_id IS NOT NULL GROUP BY uat_id
    """)}
    # Consumer network count per UAT (SELGROS excluded)
    b2b_ids = conn.execute(
        "SELECT id FROM retail_networks"
    ).fetchall()
    b2b_set = {r[0] for r in b2b_ids if is_b2b(r[0])}
    placeholders = ",".join("?" * len(b2b_set)) if b2b_set else "'__none__'"
    sql = f"""
        SELECT uat_id, COUNT(DISTINCT network_id)
        FROM stores
        WHERE uat_id IS NOT NULL AND network_id IS NOT NULL
          AND network_id NOT IN ({placeholders})
        GROUP BY uat_id
    """
    consumer_nets = {r[0]: r[1] for r in conn.execute(sql, list(b2b_set))}

    # UAT names from the uats reference table where available
    uat_names = {r[0]: r[1] for r in conn.execute("SELECT id, name FROM uats WHERE name IS NOT NULL")}

    return {
        uid: {
            "name": uat_names.get(uid, f"UAT {uid}"),
            "n_stores": store_counts[uid],
            "n_networks": consumer_nets.get(uid, 0),
        }
        for uid in store_counts
    }


def load_basket_stats(baskets_dir):
    """Return {uat_id_str: {min_month, max_month, n_networks, cheapest_net}}
    from camara basket per_uat data. Falls back to national if missing."""
    path = Path(baskets_dir) / "camara.json"
    if not path.exists():
        print("  WARNING: camara.json not found, skipping basket stats")
        return {}, None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    # National fallback
    nat = data.get("national", {})
    comparable = {nid: d for nid, d in nat.items() if d.get("comparable")}
    nat_min = min((d["cost_month"] for d in comparable.values()), default=None)
    nat_max = max((d["cost_month"] for d in comparable.values()), default=None)
    nat_cheapest = min(comparable.items(), key=lambda kv: kv[1]["cost_month"], default=(None, {}))[1].get("network")
    national_fallback = {
        "min_month": nat_min,
        "max_month": nat_max,
        "n_networks": len(comparable),
        "cheapest_net": nat_cheapest,
    }

    per_uat = {}
    for uat_id_str, by_nid in data.get("per_uat", {}).items():
        comp = {nid: d for nid, d in by_nid.items() if d.get("comparable")}
        if not comp:
            continue
        costs = [d["cost_month"] for d in comp.values()]
        min_c = min(costs)
        max_c = max(costs)
        cheapest_nid = min(comp.items(), key=lambda kv: kv[1]["cost_month"])[0]
        per_uat[int(uat_id_str)] = {
            "min_month": round(min_c, 2),
            "max_month": round(max_c, 2),
            "n_networks": len(comp),
            "cheapest_net": comp[cheapest_nid]["network"],
        }
    return per_uat, national_fallback


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Build uats.geojson for choropleth")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--baskets", default=str(BASKETS_DIR))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    print(f"Decoding {TOPO_PATH} …")
    features_topo = decode_topojson(TOPO_PATH, "UTRuriOAR3")
    print(f"  {len(features_topo)} UAT geometries decoded")

    conn = sqlite3.connect(args.db)
    uat_stats = fetch_uat_stats(conn)
    print(f"  {len(uat_stats)} UATs with stores in DB")

    basket_stats, nat_fallback = load_basket_stats(args.baskets)
    print(f"  {len(basket_stats)} UATs with basket data")

    features_out = []
    skipped = 0
    for feat in features_topo:
        sid = feat["siruta"]
        stats = uat_stats.get(sid)
        if not stats:
            skipped += 1
            continue  # only emit UATs with stores

        bk = basket_stats.get(sid, nat_fallback or {})
        # Prefer DB name; fall back to topojson nome if DB only has "UAT {sid}"
        name = stats["name"]
        if name == f"UAT {sid}" and feat.get("nome_topo"):
            import re
            name = feat["nome_topo"].title()
        props = {
            "siruta": sid,
            "name": name,
            "n_stores": stats["n_stores"],
            "n_networks": stats["n_networks"],
            "basket_min_month": bk.get("min_month"),
            "basket_max_month": bk.get("max_month"),
            "basket_n_networks": bk.get("n_networks", 0),
            "basket_cheapest_net": bk.get("cheapest_net"),
        }
        features_out.append({
            "type": "Feature",
            "geometry": feat["geometry"],
            "properties": props,
        })

    fc = {"type": "FeatureCollection", "features": features_out}
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(fc, f, ensure_ascii=False, separators=(",", ":"))
    size_kb = out_path.stat().st_size / 1024
    print(f"  {out_path.name} — {len(features_out)} features, {size_kb:.0f} KB  (skipped {skipped} no-store UATs)")


if __name__ == "__main__":
    main()
