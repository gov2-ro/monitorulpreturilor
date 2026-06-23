#!/usr/bin/env python3
"""
Analyze within-network price similarity to guide store sampling strategy.

For each network × store-type:
  - % products nationally uniform (<1% spread across stores on same date)
  - Spread distribution bucketed: 0-1%, 1-5%, 5-10%, >10%
  - 7-day rolling trend
  - Top sentinel stores (broadest product coverage + geographic spread)
  - Sampling tier recommendation: A (1-2 stores), B (5-10), C (full)

Usage:
  python analyze_price_similarity.py [--days 30] [--network CARREFOUR] [--debug]
  python analyze_price_similarity.py --output docs/price-similarity-2026-06-22.md
  python analyze_price_similarity.py --export-sentinels data/sentinel_stores.json
"""

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone

DB_PATH = "data/prices.db"

BUCKET_LABELS = ["<1%", "1-5%", "5-10%", ">10%"]


def bucket(spread_pct: float) -> int:
    if spread_pct < 1.0:
        return 0
    if spread_pct < 5.0:
        return 1
    if spread_pct < 10.0:
        return 2
    return 3


def tier(pct_under_1: float, pct_over_10: float) -> str:
    """
    Tier A: >80% products within 1% → 1-2 sentinel stores nationally sufficient.
    Tier B: 50-80% within 5% → 5-10 geographically distributed stores.
    Tier C: otherwise → full coverage needed.
    """
    if pct_under_1 >= 80.0:
        return "A"
    if (100.0 - pct_over_10) >= 50.0 and pct_under_1 >= 40.0:
        return "B"
    return "C"


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_uniformity(conn: sqlite3.Connection, since: str, network_filter: str | None, debug: bool):
    """
    Returns list of (network, type_name, product_id, fetch_date, n_stores, spread_pct).
    Uses fetched_at for date range (ISO format, sortable).
    Groups by calendar day of fetch to avoid cross-day pollution.
    """
    network_clause = "AND rn.name = :network" if network_filter else ""
    query = f"""
    SELECT
        rn.name                              AS network,
        COALESCE(s.type_name, 'Unknown')     AS type_name,
        p.product_id,
        DATE(p.fetched_at)                   AS fetch_date,
        COUNT(DISTINCT p.store_id)           AS n_stores,
        MIN(p.price)                         AS min_p,
        MAX(p.price)                         AS max_p,
        AVG(p.price)                         AS avg_p
    FROM prices p
    JOIN stores s  ON p.store_id  = s.id
    JOIN retail_networks rn ON s.network_id = rn.id
    WHERE p.price > 0
      AND p.fetched_at >= :since
      {network_clause}
    GROUP BY rn.name, type_name, p.product_id, fetch_date
    HAVING n_stores >= 3
    """
    params = {"since": since, "network": network_filter}
    if debug:
        print(f"[debug] uniformity query since={since} network={network_filter}")
    rows = conn.execute(query, params).fetchall()
    if debug:
        print(f"[debug] {len(rows):,} (network, type, product, date) groups returned")
    return rows


def fetch_sentinel_stores(conn: sqlite3.Connection, network: str, since: str, n_sentinels: int = 3):
    """
    Returns n_sentinels stores that are both high-coverage and geographically spread.
    Strategy: fetch top-20 by product coverage, then greedily pick the subset
    that maximises minimum pairwise distance (farthest-point selection).
    """
    query = """
    SELECT p.store_id, s.name, s.addr, s.lat, s.lon,
           COUNT(DISTINCT p.product_id) AS products_covered
    FROM prices p
    JOIN stores s ON p.store_id = s.id
    JOIN retail_networks rn ON s.network_id = rn.id
    WHERE rn.name = ?
      AND p.price > 0
      AND p.fetched_at >= ?
      AND s.lat IS NOT NULL AND s.lon IS NOT NULL
    GROUP BY p.store_id
    ORDER BY products_covered DESC
    LIMIT 20
    """
    candidates = conn.execute(query, (network, since)).fetchall()
    if len(candidates) <= n_sentinels:
        return candidates
    return _farthest_point_select(candidates, n_sentinels)


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    from math import radians, sin, cos, sqrt, atan2
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


def _farthest_point_select(candidates, n: int) -> list:
    """Greedy farthest-point: start with highest-coverage store, each next pick
    is the candidate furthest from all already-selected stores."""
    selected = [candidates[0]]
    remaining = list(candidates[1:])
    while len(selected) < n and remaining:
        best = max(remaining, key=lambda c: min(
            _haversine_km(c["lat"], c["lon"], s["lat"], s["lon"]) for s in selected
        ))
        selected.append(best)
        remaining.remove(best)
    return selected


def compute_network_stats(rows):
    """
    Returns dict: (network, type_name) → {
        "products": set of product_ids seen,
        "buckets": [count_b0, count_b1, count_b2, count_b3],  # per product×date
        "dates": set of fetch_dates,
        "daily": {fetch_date: [b0,b1,b2,b3]},
    }
    """
    stats = defaultdict(lambda: {
        "products": set(),
        "buckets": [0, 0, 0, 0],
        "dates": set(),
        "daily": defaultdict(lambda: [0, 0, 0, 0]),
    })

    for row in rows:
        key = (row["network"], row["type_name"])
        s = stats[key]
        avg_p = row["avg_p"]
        if avg_p <= 0:
            continue
        spread_pct = (row["max_p"] - row["min_p"]) / avg_p * 100.0
        b = bucket(spread_pct)
        s["products"].add(row["product_id"])
        s["buckets"][b] += 1
        s["dates"].add(row["fetch_date"])
        s["daily"][row["fetch_date"]][b] += 1

    return dict(stats)


def compute_weekly_trend(daily: dict) -> list[tuple[str, list[float]]]:
    """Aggregate daily bucket counts into weeks, return list of (week_label, [pct_b0..b3])."""
    if not daily:
        return []
    sorted_dates = sorted(daily.keys())
    # Group into ~7-day windows
    weeks = []
    window_start = sorted_dates[0]
    current_buckets = [0, 0, 0, 0]

    for date in sorted_dates:
        delta = (datetime.fromisoformat(date) - datetime.fromisoformat(window_start)).days
        if delta >= 7:
            total = sum(current_buckets)
            if total > 0:
                pcts = [b / total * 100 for b in current_buckets]
                weeks.append((f"{window_start}→{date}", pcts))
            window_start = date
            current_buckets = [0, 0, 0, 0]
        for i, v in enumerate(daily[date]):
            current_buckets[i] += v

    total = sum(current_buckets)
    if total > 0:
        pcts = [b / total * 100 for b in current_buckets]
        weeks.append((f"{window_start}…", pcts))

    return weeks


def fmt_pct(n: float) -> str:
    return f"{n:5.1f}%"


def print_summary_table(all_stats: dict, sentinels: dict, args):
    print()
    print("=" * 95)
    print(f"  PRICE SIMILARITY ANALYSIS  (last {args.days} days)")
    print("=" * 95)
    print(f"  {'Network':<26} {'Type':<28} {'Prods':>6}  {'<1%':>6} {'1-5%':>6} {'5-10%':>6} {'>10%':>6}  Tier")
    print("-" * 95)

    tier_groups = defaultdict(list)
    for (network, type_name), s in sorted(all_stats.items()):
        buckets = s["buckets"]
        total = sum(buckets)
        if total == 0:
            continue
        pcts = [b / total * 100 for b in buckets]
        t = tier(pcts[0], pcts[3])
        tier_groups[t].append((network, type_name))
        n_products = len(s["products"])
        print(f"  {network:<26} {type_name:<28} {n_products:>6}  "
              f"{fmt_pct(pcts[0])} {fmt_pct(pcts[1])} {fmt_pct(pcts[2])} {fmt_pct(pcts[3])}   {t}")

    print()
    print("  TIER GUIDE:")
    print("    A = 1-2 sentinel stores nationally sufficient  (>80% products within 1% spread)")
    print("    B = 5-10 geographically distributed stores     (50-80% products within 5%)")
    print("    C = full coverage needed                       (<50% within 5% or >20% over 10%)")

    if tier_groups.get("A"):
        print()
        print("  SENTINEL STORES FOR TIER-A NETWORKS (geo-diverse, high-coverage):")
        for network in sorted({n for n, _ in tier_groups["A"]}):
            rows = sentinels.get(network, [])
            if not rows:
                continue
            # compute pairwise spread
            if len(rows) >= 2:
                spread_km = max(
                    _haversine_km(a["lat"], a["lon"], b["lat"], b["lon"])
                    for i, a in enumerate(rows) for b in rows[i+1:]
                )
                spread_str = f"  (spread {spread_km:.0f} km)"
            else:
                spread_str = ""
            print(f"\n    {network}{spread_str}:")
            for r in rows:
                print(f"      store_id={r['store_id']:5d}  {r['name']:<40}  {r['products_covered']:4d} products  "
                      f"({r['lat']:.2f}°N, {r['lon']:.2f}°E)")

    print()
    print("=" * 95)


def print_trend(all_stats: dict, args):
    if not args.trend:
        return
    print()
    print("=" * 95)
    print("  WEEKLY TREND (% products per spread bucket)")
    print("=" * 95)
    for (network, type_name), s in sorted(all_stats.items()):
        weeks = compute_weekly_trend(s["daily"])
        if len(weeks) < 2:
            continue
        print(f"\n  {network} / {type_name}")
        print(f"    {'Week':<28}  {'<1%':>6} {'1-5%':>6} {'5-10%':>6} {'>10%':>6}")
        for label, pcts in weeks:
            print(f"    {label:<28}  {fmt_pct(pcts[0])} {fmt_pct(pcts[1])} {fmt_pct(pcts[2])} {fmt_pct(pcts[3])}")


def build_markdown(all_stats: dict, sentinels: dict, args, since: str) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"# Price Similarity Analysis — {today}",
        "",
        f"**Period:** last {args.days} days (fetched_at ≥ {since[:10]})",
        f"**Scope:** products with ≥3 stores on the same date in the same network×type",
        "",
        "## Network Uniformity Summary",
        "",
        "| Network | Type | Products | <1% | 1–5% | 5–10% | >10% | Tier |",
        "|---|---|---:|---:|---:|---:|---:|:---:|",
    ]

    tier_groups = defaultdict(list)
    for (network, type_name), s in sorted(all_stats.items()):
        buckets = s["buckets"]
        total = sum(buckets)
        if total == 0:
            continue
        pcts = [b / total * 100 for b in buckets]
        t = tier(pcts[0], pcts[3])
        tier_groups[t].append((network, type_name))
        n_products = len(s["products"])
        lines.append(
            f"| {network} | {type_name} | {n_products:,} | "
            f"{pcts[0]:.1f}% | {pcts[1]:.1f}% | {pcts[2]:.1f}% | {pcts[3]:.1f}% | **{t}** |"
        )

    lines += [
        "",
        "## Tier Definitions",
        "",
        "| Tier | Criterion | Sampling strategy |",
        "|:---:|---|---|",
        "| **A** | >80% products within 1% spread | 1–2 sentinel stores nationally |",
        "| **B** | 50–80% products within 5% spread | 5–10 stores geographically distributed |",
        "| **C** | <50% within 5% or >20% over 10% | Full coverage — current behavior |",
        "",
        "## Sampling Recommendations",
        "",
    ]

    for t in ["A", "B", "C"]:
        entries = sorted(set(tier_groups[t]))
        if not entries:
            continue
        lines.append(f"### Tier {t}")
        for network, type_name in entries:
            rows = sentinels.get(network, [])
            label = f"{network} / {type_name}"
            if rows and t == "A":
                if len(rows) >= 2:
                    spread_km = max(
                        _haversine_km(a["lat"], a["lon"], b["lat"], b["lon"])
                        for i, a in enumerate(rows) for b in rows[i+1:]
                    )
                    lines.append(f"\n**{label}** — sentinel stores (spread {spread_km:.0f} km):")
                else:
                    lines.append(f"\n**{label}** — sentinel stores:")
                lines.append("")
                lines.append("| store_id | Name | Products | Lat | Lon |")
                lines.append("|---:|---|---:|---:|---:|")
                for r in rows:
                    lines.append(f"| {r['store_id']} | {r['name']} | {r['products_covered']:,} | {r['lat']:.3f} | {r['lon']:.3f} |")
            else:
                lines.append(f"- **{label}**")
        lines.append("")

    lines += [
        "## Methodology",
        "",
        "- Spread % = `(max_price − min_price) / avg_price × 100` across stores",
        "- Computed per (product, network, store_type, fetch_date) group with ≥3 stores",
        "- Sentinel stores ranked by distinct product coverage in the period",
        f"- Generated by `analyze_price_similarity.py --days {args.days}`",
    ]

    return "\n".join(lines) + "\n"


def export_sentinels(all_stats: dict, sentinels: dict, conn: sqlite3.Connection,
                     output_path: str, debug: bool):
    """Write sentinel_stores.json for fetch_prices.py to consume.

    Only includes networks where ALL store types are Tier A — mixed-type networks
    (e.g. AUCHAN with Hypermarket=A and S&D=B) are excluded to avoid propagating
    wrong prices across incompatible store formats.
    """
    # network_name → network_id mapping from DB
    name_to_id = {
        row[0]: row[1]
        for row in conn.execute("SELECT name, id FROM retail_networks")
    }

    # Collect per-network tiers and best sentinel set (from first Tier-A type entry)
    from collections import defaultdict as _dd
    network_type_tiers: dict = _dd(list)   # net_id → [tier, ...]
    network_first_sentinels: dict = {}     # net_id → {network_name, sentinel_ids}

    for (network, type_name), s in sorted(all_stats.items()):
        buckets = s["buckets"]
        total = sum(buckets)
        if total == 0:
            continue
        pcts = [b / total * 100 for b in buckets]
        t = tier(pcts[0], pcts[3])
        net_id = name_to_id.get(network)
        if not net_id:
            if debug:
                print(f"[debug] No network_id for {network!r} — skipping")
            continue
        network_type_tiers[net_id].append(t)
        if t == "A" and net_id not in network_first_sentinels:
            rows = sentinels.get(network, [])
            if rows:
                network_first_sentinels[net_id] = {
                    "network_name": network,
                    "sentinel_ids": [r["store_id"] for r in rows],
                }

    result = {}
    skipped = []
    for net_id, tiers_list in network_type_tiers.items():
        if all(t == "A" for t in tiers_list) and net_id in network_first_sentinels:
            cfg = network_first_sentinels[net_id]
            result[net_id] = {"tier": "A", **cfg}
        elif any(t == "A" for t in tiers_list):
            cfg = network_first_sentinels.get(net_id, {})
            skipped.append(f"{cfg.get('network_name', net_id)} (mixed types: {tiers_list})")

    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    print(f"\n[+] Sentinel config written to {output_path}")
    print(f"    {len(result)} network(s) included (all types Tier A):")
    for net_id, cfg in result.items():
        print(f"    {net_id:30s}  {cfg['network_name']}: {cfg['sentinel_ids']}")
    if skipped:
        print(f"\n    Excluded (mixed types — would propagate wrong prices):")
        for s in skipped:
            print(f"    {s}")


def main():
    parser = argparse.ArgumentParser(description="Analyze within-network price similarity")
    parser.add_argument("--days", type=int, default=30, help="Look back N days (default: 30)")
    parser.add_argument("--network", help="Filter to a single network (e.g. CARREFOUR)")
    parser.add_argument("--output", help="Write markdown report to this path")
    parser.add_argument("--export-sentinels", metavar="PATH",
                        help="Write sentinel_stores.json for fetch_prices.py "
                             "(e.g. data/sentinel_stores.json)")
    parser.add_argument("--trend", action="store_true", help="Print weekly trend breakdown")
    parser.add_argument("--debug", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    since = (datetime.now(timezone.utc) - timedelta(days=args.days)).strftime("%Y-%m-%d")

    conn = connect(DB_PATH)

    print(f"[+] Querying prices since {since} (last {args.days} days)…", flush=True)
    rows = fetch_uniformity(conn, since, args.network, args.debug)

    if not rows:
        print("No data found for the given filters.")
        conn.close()
        return

    all_stats = compute_network_stats(rows)

    # Fetch sentinels for all networks (or just the filtered one)
    sentinels = {}
    networks_to_scan = [args.network] if args.network else sorted({k[0] for k in all_stats})
    for network in networks_to_scan:
        sentinels[network] = fetch_sentinel_stores(conn, network, since)

    print_summary_table(all_stats, sentinels, args)
    print_trend(all_stats, args)

    if args.output:
        md = build_markdown(all_stats, sentinels, args, since)
        with open(args.output, "w") as f:
            f.write(md)
        print(f"\n[+] Report written to {args.output}")

    if args.export_sentinels:
        export_sentinels(all_stats, sentinels, conn, args.export_sentinels, args.debug)

    conn.close()


if __name__ == "__main__":
    main()
