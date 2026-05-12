#!/usr/bin/env python3
"""audit_pipeline.py — daily data-quality audit.

Runs read-only checks against the DB and exits non-zero if any RED threshold is
breached. Outputs both a human-readable text trail and a JSON summary into
data/logs/ for forensic history.

Reuses signal loaders from generate_pipeline_report.py — keep thresholds in sync
unless intentionally diverging.

Usage:
    python audit_pipeline.py
    python audit_pipeline.py --db data/prices.db --out-dir data/logs

Exit codes:
    0 — all green
    1 — at least one RED check
    2 — usage / unexpected error
"""

import argparse
import json
import sqlite3
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

from generate_pipeline_report import (
    STALE_DAYS,
    compute_outlier_summary,
    load_store_freshness,
)

# Red thresholds — when breached, audit fails and triggers /fail ping.
STALE_PCT_RED = 10.0     # >10% of stores stale → red
ABANDONED_DAYS = 7       # any abandoned/error run in last N days → red
COVERAGE_GAP_DAYS = 7    # any network with no fresh prices in N days → red
FLAG_DRIFT_MULT = 3.0    # today's price_flags count > N× the 30-day median → red


def check_store_freshness(conn):
    rows = load_store_freshness(conn, stale_days=STALE_DAYS)
    total = len(rows)
    stale = sum(1 for r in rows if r["stale"])
    pct = round(100 * stale / total, 2) if total else 0.0
    red = pct > STALE_PCT_RED
    return {
        "name": "store_freshness",
        "red": red,
        "summary": f"{stale}/{total} stores stale (>{STALE_DAYS}d): {pct}%",
        "stale_pct": pct,
        "stale_count": stale,
        "total_stores": total,
        "threshold_pct": STALE_PCT_RED,
    }


def check_run_history(conn):
    rows = conn.execute(f"""
        SELECT id, script, status, started_at, finished_at, notes
        FROM runs
        WHERE status IN ('abandoned', 'error')
          AND (finished_at IS NULL OR finished_at >= datetime('now', '-{ABANDONED_DAYS} days'))
        ORDER BY id DESC
    """).fetchall()
    red = len(rows) > 0
    samples = [{"id": r[0], "script": r[1], "status": r[2], "notes": r[5]} for r in rows[:5]]
    return {
        "name": "run_history",
        "red": red,
        "summary": f"{len(rows)} abandoned/error run(s) in last {ABANDONED_DAYS}d",
        "bad_run_count": len(rows),
        "samples": samples,
        "window_days": ABANDONED_DAYS,
    }


def check_coverage_gaps(conn):
    rows = conn.execute(f"""
        SELECT n.id, n.name,
               COUNT(DISTINCT s.id) AS stores,
               MAX(pc.last_checked_at) AS latest
        FROM retail_networks n
        LEFT JOIN stores s ON s.network_id = n.id
        LEFT JOIN prices_current pc ON pc.store_id = s.id
        GROUP BY n.id, n.name
    """).fetchall()

    gaps = []
    cutoff = datetime.now(timezone.utc).timestamp() - COVERAGE_GAP_DAYS * 86400
    for net_id, net_name, stores, latest in rows:
        if not stores:
            continue  # network has no stores in DB
        ts = 0
        if latest:
            try:
                ts = datetime.fromisoformat(latest.replace("Z", "+00:00")).timestamp()
            except ValueError:
                ts = 0
        if ts < cutoff:
            age_d = (datetime.now(timezone.utc).timestamp() - ts) / 86400 if ts else None
            gaps.append({
                "network_id": net_id,
                "network": net_name,
                "stores": stores,
                "latest": latest,
                "age_days": round(age_d, 1) if age_d else None,
            })

    red = len(gaps) > 0
    return {
        "name": "coverage_gaps",
        "red": red,
        "summary": f"{len(gaps)} network(s) with no fresh prices in {COVERAGE_GAP_DAYS}d",
        "gaps": gaps,
        "window_days": COVERAGE_GAP_DAYS,
    }


def check_anomaly_drift(conn):
    """price_flags row count today vs 30-day median. Tolerates empty table."""
    today_count = conn.execute("""
        SELECT COUNT(*) FROM price_flags
        WHERE date(created_at) = date('now')
    """).fetchone()[0] if _table_exists(conn, "price_flags") else 0

    if not _table_exists(conn, "price_flags"):
        return {
            "name": "anomaly_drift",
            "red": False,
            "summary": "price_flags table not present — skipped",
            "today_count": 0,
            "baseline_median": None,
        }

    baseline = conn.execute("""
        SELECT date(created_at) AS d, COUNT(*) AS n
        FROM price_flags
        WHERE created_at >= date('now', '-30 days')
          AND date(created_at) < date('now')
        GROUP BY d
    """).fetchall()
    counts = [n for _, n in baseline]
    median = statistics.median(counts) if counts else 0
    threshold = median * FLAG_DRIFT_MULT if median else None
    red = bool(threshold) and today_count > threshold
    return {
        "name": "anomaly_drift",
        "red": red,
        "summary": (f"today={today_count}, 30d-median={median}"
                    + (f", red>{threshold}" if threshold else "")),
        "today_count": today_count,
        "baseline_median": median,
        "threshold": threshold,
    }


def _table_exists(conn, name):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def check_outliers(conn):
    """Reuses generate_pipeline_report's outlier loader. Informational only."""
    summary = compute_outlier_summary(conn)
    return {
        "name": "outlier_summary",
        "red": False,  # informational; outlier drift is covered by anomaly_drift
        "summary": (f"{summary['flagged_count']} outlier records "
                    f"({summary['flagged_pct']}% of {summary['total_records']})"),
        **summary,
    }


def run_audit(db_path, include_outliers=False):
    # Read-only connection so we don't block / aren't blocked by the live fetch's
    # write lock during the daily 06:00 audit window.
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        checks = [
            check_store_freshness(conn),
            check_run_history(conn),
            check_coverage_gaps(conn),
            check_anomaly_drift(conn),
        ]
        if include_outliers:
            checks.append(check_outliers(conn))
    finally:
        conn.close()

    any_red = any(c["red"] for c in checks)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overall": "RED" if any_red else "GREEN",
        "checks": checks,
    }


def write_outputs(report, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).date().isoformat()

    json_path = out_dir / f"audit-{day}.json"
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    txt_path = out_dir / f"audit-{day}.txt"
    lines = [
        f"PIPELINE AUDIT — {report['generated_at']}",
        f"OVERALL: {report['overall']}",
        "",
    ]
    for c in report["checks"]:
        marker = "RED " if c["red"] else "ok  "
        lines.append(f"  [{marker}] {c['name']}: {c['summary']}")
    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return txt_path, json_path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data/prices.db")
    ap.add_argument("--out-dir", default="data/logs")
    ap.add_argument("--include-outliers", action="store_true",
                    help="Also run the slow per-product outlier check (already covered "
                         "by generate_pipeline_report.py; off by default)")
    args = ap.parse_args()

    report = run_audit(args.db, include_outliers=args.include_outliers)
    txt_path, json_path = write_outputs(report, args.out_dir)

    print(f"AUDIT {report['overall']} — {txt_path}")
    for c in report["checks"]:
        marker = "RED " if c["red"] else "ok  "
        print(f"  [{marker}] {c['name']}: {c['summary']}")

    sys.exit(1 if report["overall"] == "RED" else 0)


if __name__ == "__main__":
    main()
