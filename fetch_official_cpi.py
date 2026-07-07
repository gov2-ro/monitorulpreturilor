#!/usr/bin/env python3
"""Fetch official Romanian inflation (Eurostat HICP) into the official_cpi table.

The consumer site's Civic Inflation Index (inflatie.html) measures the cost of
curated baskets from real shelf prices. To make that credible it must be
anchored against the *official* figure. Eurostat publishes Romania's Harmonised
Index of Consumer Prices (HICP) via a clean JSON-stat API — no auth, no scraping
— and, crucially, publishes the current month ~2 weeks after month-end, which is
what lets our daily data nowcast ahead of it.

We pull two ECOICOP-v2 aggregates for Romania:
  TOTAL  → stored as coicop 'CP00'  (all-items headline inflation)
  CP01   → stored as coicop 'CP01'  (food & non-alcoholic beverages) — the
           comparable for our pantry ("camară") basket.

For each we store, per month:
  index_value   I25    — index level, base 2025=100
  rate_monthly  RCH_M  — month-over-month % (Eurostat-computed)
  rate_annual   RCH_A  — year-over-year %   (Eurostat-computed, the headline)

Dataset: prc_hicp_minr (HICP - ECOICOP v2 - indices and rates of change, monthly).
Note the dimension is named `coicop18`; the filter param must match.

Usage:
    python fetch_official_cpi.py
    python fetch_official_cpi.py --db data/prices.db --since 2025-01 --debug
"""

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

from db import ensure_official_cpi_table, upsert_official_cpi

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "prices.db"

BASE = ("https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"
        "prc_hicp_minr")

# Eurostat coicop18 code → our stored coicop label
COICOP_MAP = {"TOTAL": "CP00", "CP01": "CP01"}
# Eurostat unit code → official_cpi column
UNIT_MAP = {"I25": "index_value", "RCH_M": "rate_monthly", "RCH_A": "rate_annual"}


def fetch_json(since: str, debug: bool = False) -> dict:
    """GET the filtered JSON-stat payload for Romania, with one retry."""
    params = [
        ("format", "JSON"), ("lang", "EN"), ("geo", "RO"),
        ("coicop18", "TOTAL"), ("coicop18", "CP01"),
        ("unit", "I25"), ("unit", "RCH_M"), ("unit", "RCH_A"),
        ("sinceTimePeriod", since),
    ]
    last_err = None
    for attempt in (1, 2):
        try:
            r = requests.get(BASE, params=params, timeout=30)
            r.raise_for_status()
            if debug:
                print(f"  GET {r.url}\n  {len(r.content)} bytes, HTTP {r.status_code}")
            return r.json()
        except (requests.RequestException, ValueError) as e:
            last_err = e
            print(f"  fetch attempt {attempt} failed: {e}", file=sys.stderr)
    raise SystemExit(f"Eurostat fetch failed after retries: {last_err}")


def parse_jsonstat(doc: dict) -> dict:
    """Decode JSON-stat 2.0 into {(coicop_label, period): {col: value}}.

    Generic row-major linear-index decode — no hard-coded dimension positions,
    so it survives Eurostat reordering dimensions or adding categories.
    """
    ids = doc["id"]
    sizes = doc["size"]
    cats = {dim: doc["dimension"][dim]["category"]["index"] for dim in ids}
    # inverse maps: position → code, per dimension
    inv = {dim: {pos: code for code, pos in cats[dim].items()} for dim in ids}
    # row-major strides
    strides = [1] * len(sizes)
    for i in range(len(sizes) - 2, -1, -1):
        strides[i] = strides[i + 1] * sizes[i + 1]

    out: dict = {}
    for key, value in doc["value"].items():
        if value is None:
            continue
        lin = int(key)
        coords = {}
        for dim, stride, size in zip(ids, strides, sizes):
            coords[dim] = inv[dim][(lin // stride) % size]
        coi = COICOP_MAP.get(coords.get("coicop18"))
        col = UNIT_MAP.get(coords.get("unit"))
        if coi is None or col is None:
            continue
        rec = out.setdefault((coi, coords["time"]), {})
        rec[col] = value
    return out


def build():
    ap = argparse.ArgumentParser(description="Fetch official HICP inflation for Romania")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--since", default="2025-01",
                    help="earliest month to fetch (YYYY-MM), default 2025-01")
    ap.add_argument("--debug", action="store_true", help="verbose logging")
    args = ap.parse_args()

    doc = fetch_json(args.since, debug=args.debug)
    records = parse_jsonstat(doc)
    if not records:
        raise SystemExit("No official CPI records parsed — aborting (no write).")

    fetched_at = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(args.db, timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")
    ensure_official_cpi_table(conn)

    written = 0
    for (coi, period), rec in sorted(records.items()):
        upsert_official_cpi(
            conn, "HICP", coi, period,
            rec.get("index_value"), rec.get("rate_annual"), rec.get("rate_monthly"),
            fetched_at,
        )
        written += 1
        if args.debug:
            print(f"  {coi} {period}  idx={rec.get('index_value')}  "
                  f"MoM={rec.get('rate_monthly')}  YoY={rec.get('rate_annual')}")
    conn.commit()

    # Summary — latest published month per series
    def latest(coi):
        rows = [(p, r) for (c, p), r in records.items()
                if c == coi and r.get("rate_annual") is not None]
        return max(rows, default=None, key=lambda x: x[0])

    for coi, name in (("CP00", "all-items"), ("CP01", "food")):
        lp = latest(coi)
        if lp:
            p, r = lp
            print(f"  {name:<10} latest published {p}: "
                  f"YoY {r.get('rate_annual')}%  MoM {r.get('rate_monthly')}%")
    conn.close()
    print(f"official_cpi — {written} rows upserted into {args.db}")


if __name__ == "__main__":
    build()
