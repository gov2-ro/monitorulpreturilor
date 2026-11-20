#!/usr/bin/env python3
"""Archive + trim `prices` rows older than N days (VPS retention job).

Each run, in order:
  1. rollup  — fold basket-product rows older than the cutoff into `price_daily_min`
               (MIN with what is already there, so history from deleted rows is kept);
  2. export  — write the not-yet-archived old rows to immutable gzip SQL files, one per
               price_date month and run: <archive-dir>/prices-YYYY-MM.<run>.sql.gz, each
               verified (re-read, row count) and listed with its sha256 in MANIFEST.sha256;
  3. delete  — delete archived rows in small batches until --max-runtime.

Modes: --dry-run (default) only counts and estimates archive sizes; --export-only does
1+2; --apply does 1+2+3. Restore: `gzip -cd prices-*.sql.gz | sqlite3 some.db`
(files use INSERT OR IGNORE, so overlaps and re-runs are harmless).

Invariant kept in <archive-dir>/retention_state.json as {cutoff, max_id}: every row with
price_date < cutoff AND id <= max_id is archived. It only grows, so a run never re-exports
those rows, and a delete interrupted by --max-runtime is finished by the next run.

Note: deleting does not shrink prices.db; freed pages are reused by new rows. Shrinking
needs a rebuild-by-copy (docs/vps-db-retention.md).
"""

import argparse
import gzip
import hashlib
import io
import json
import os
import sqlite3
import sys
import time
from array import array
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
COLS = ("id, product_id, store_id, price, price_date, promo, brand, unit, "
        "retail_categ_id, retail_categ_name, fetched_at, last_checked_at")
CHUNK = 5000          # ids per SELECT / DELETE statement
DEBUG = False


def log(msg):
    print(f"{datetime.now().isoformat(timespec='seconds')} retention: {msg}", flush=True)


def dbg(msg):
    if DEBUG:
        log(f"[debug] {msg}")


def sql_lit(v):
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


def load_state(path):
    if path.exists():
        return json.loads(path.read_text())
    return {"cutoff": "", "max_id": 0}


def save_state(path, state):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    os.replace(tmp, path)


def basket_product_ids():
    cfg = json.load(open(ROOT / "config" / "baskets.json", encoding="utf-8"))
    return sorted({p for b in cfg["baskets"] for it in b["items"] for p in it["product_ids"]})


def candidate_ids(conn, cutoff, state, max_id):
    """Sorted ids of rows older than cutoff, not archived yet, plus per-month samples
    (index-only scan on idx_prices_date, which also carries price_date)."""
    ids, months, samples = array("q"), Counter(), {}
    for i, m in conn.execute(
        """SELECT id, substr(price_date, 1, 7) FROM prices
           WHERE price_date < ? AND id <= ? AND NOT (price_date < ? AND id <= ?)""",
        (cutoff, max_id, state["cutoff"], state["max_id"]),
    ):
        ids.append(i)
        months[m] += 1
        if months[m] % 50 == 1 and len(samples.setdefault(m, [])) < 2000:
            samples[m].append(i)                    # spread-out sample for the estimate
    return sorted(ids), months, samples


def fetch_rows(conn, ids):
    for k in range(0, len(ids), CHUNK):
        part = ids[k:k + CHUNK]
        ph = ",".join("?" * len(part))
        yield from conn.execute(f"SELECT {COLS} FROM prices WHERE id IN ({ph}) ORDER BY id", part)


def rollup(conn, cutoff, pids, apply):
    """MIN-merge raw basket rows older than cutoff into price_daily_min."""
    ph = ",".join("?" * len(pids))
    select = f"""
        SELECT pr.price_date, pr.product_id, s.network_id, MIN(pr.price)
        FROM prices pr JOIN stores s ON pr.store_id = s.id
        WHERE pr.product_id IN ({ph}) AND pr.price_date < ?
          AND s.network_id IS NOT NULL AND pr.price > 0
        GROUP BY pr.price_date, pr.product_id, s.network_id"""
    if not apply:
        n = conn.execute(f"SELECT COUNT(*) FROM ({select})", (*pids, cutoff)).fetchone()[0]
        log(f"rollup: would merge {n:,} (date, product, network) minima into price_daily_min")
        return
    cur = conn.execute(f"""
        INSERT INTO price_daily_min {select}
        ON CONFLICT(price_date, product_id, network_id)
        DO UPDATE SET min_price = MIN(min_price, excluded.min_price)""", (*pids, cutoff))
    conn.commit()
    log(f"rollup: merged {cur.rowcount:,} minima into price_daily_min")


def estimate(conn, months, samples):
    """Dry run: estimated gzip bytes per month, from a sample of rows."""
    sizes = {}
    for m, sample_ids in samples.items():
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6) as gz:
            for r in fetch_rows(conn, sorted(sample_ids)):
                gz.write(insert_stmt(r))
        sizes[m] = buf.tell() / len(sample_ids) * months[m]
    return sizes


def insert_stmt(row):
    return f"INSERT OR IGNORE INTO prices({COLS}) VALUES({','.join(map(sql_lit, row))});\n".encode()


def export(conn, ids, archive_dir, run_id):
    """Write per-month gzip files (.tmp), verify, fsync, rename. Returns {month: (path, rows)}."""
    schema = conn.execute("SELECT sql FROM sqlite_master WHERE name='prices'").fetchone()[0]
    header = (f"-- monitorulpreturilor prices archive, run {run_id}\n"
              f"{schema.replace('CREATE TABLE prices', 'CREATE TABLE IF NOT EXISTS prices', 1)};\n"
              "BEGIN;\n").encode()
    writers, counts = {}, Counter()
    try:
        for row in fetch_rows(conn, ids):
            m = row[4][:7]
            if m not in writers:
                p = archive_dir / f"prices-{m}.{run_id}.sql.gz.tmp"
                writers[m] = gzip.open(p, "wb", compresslevel=6)
                writers[m].write(header)
            writers[m].write(insert_stmt(row))
            counts[m] += 1
            if sum(counts.values()) % 500000 == 0:
                dbg(f"exported {sum(counts.values()):,} rows")
    finally:
        for gz in writers.values():
            gz.write(b"COMMIT;\n")
            gz.close()
    out = {}
    for m, n in sorted(counts.items()):
        tmp = archive_dir / f"prices-{m}.{run_id}.sql.gz.tmp"
        h, lines = hashlib.sha256(tmp.read_bytes()).hexdigest(), 0
        with gzip.open(tmp, "rb") as gz:            # full decompress = integrity check
            lines = sum(1 for ln in gz if ln.startswith(b"INSERT"))
        if lines != n:
            raise RuntimeError(f"{tmp}: {lines} INSERT lines, expected {n}")
        with open(tmp, "rb") as f:
            os.fsync(f.fileno())
        final = tmp.with_suffix("")                 # drop .tmp
        os.replace(tmp, final)
        with open(archive_dir / "MANIFEST.sha256", "a") as mf:
            mf.write(f"{h}  {final.name}\n")
        out[m] = (final, n)
        log(f"export: {final.name}  {n:,} rows  {final.stat().st_size / 1e6:.1f} MB  sha256 {h[:12]}…")
    return out


def delete(conn, cutoff, max_id, deadline):
    """Delete archived rows (price_date < cutoff, id <= max_id) in batches until deadline."""
    ids = array("q", (i for (i,) in conn.execute(
        "SELECT id FROM prices WHERE price_date < ? AND id <= ?",
        (cutoff, max_id))))
    ids = sorted(ids)
    log(f"delete: {len(ids):,} archived rows to delete")
    done, t0 = 0, time.monotonic()
    for k in range(0, len(ids), CHUNK):
        if time.monotonic() > deadline:
            log(f"delete: time budget reached, {len(ids) - done:,} rows left for the next run")
            break
        part = ids[k:k + CHUNK]
        conn.execute(f"DELETE FROM prices WHERE id IN ({','.join('?' * len(part))})", part)
        conn.commit()                               # short write txns: fetch slices interleave
        done += len(part)
        if done % 100000 < CHUNK:
            rate = done / max(time.monotonic() - t0, 1e-9)
            log(f"delete: {done:,}/{len(ids):,} ({rate:,.0f} rows/s)")
    return done


def main():
    global DEBUG
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=str(ROOT / "data" / "prices.db"))
    ap.add_argument("--archive-dir", default=str(ROOT / "data" / "archive"))
    ap.add_argument("--keep-days", type=int, default=45)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="count + estimate only (default)")
    mode.add_argument("--export-only", action="store_true", help="rollup + export, no delete")
    mode.add_argument("--apply", action="store_true", help="rollup + export + delete")
    ap.add_argument("--max-runtime", type=int, default=3600, help="seconds; delete stops after")
    ap.add_argument("--min-free-gb", type=float, default=2.0, help="on the archive filesystem")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()
    DEBUG = args.debug
    deadline = time.monotonic() + args.max_runtime
    writes = args.export_only or args.apply

    archive_dir = Path(args.archive_dir)
    state_path = archive_dir / "retention_state.json"
    cutoff = (date.today() - timedelta(days=args.keep_days)).isoformat()
    run_id = datetime.now().strftime("%Y%m%dT%H%M")
    mode_name = "apply" if args.apply else "export-only" if args.export_only else "dry-run"
    log(f"mode={mode_name} keep_days={args.keep_days} cutoff={cutoff} archive={archive_dir.resolve()}")

    if writes:
        archive_dir.mkdir(parents=True, exist_ok=True)
        st = os.statvfs(archive_dir)
        free_gb = st.f_bavail * st.f_frsize / 1e9
        if free_gb < args.min_free_gb:
            log(f"SKIP: {free_gb:.1f} GB free on archive fs (< {args.min_free_gb})")
            return 1
    lock = archive_dir / "retention.lock"
    if writes:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
        except FileExistsError:
            log(f"SKIP: lock {lock} exists (another run, or remove it if stale)")
            return 1

    try:
        uri = f"file:{args.db}" + ("" if writes else "?mode=ro")
        conn = sqlite3.connect(uri, uri=True, timeout=60)
        conn.execute("PRAGMA busy_timeout=60000")
        state = load_state(state_path)
        if state["cutoff"] > cutoff:
            cutoff = state["cutoff"]                # never move the cutoff backwards
        max_id = conn.execute("SELECT MAX(id) FROM prices").fetchone()[0] or 0
        dbg(f"state={state} max_id={max_id}")

        rollup(conn, cutoff, basket_product_ids(), apply=writes)

        t = time.monotonic()
        ids, months, samples = candidate_ids(conn, cutoff, state, max_id)
        log(f"export: {len(ids):,} rows older than {cutoff} not yet archived "
            f"(index scan {time.monotonic() - t:.0f}s)")

        if not writes:
            sizes = estimate(conn, months, samples)
            for m in sorted(months):
                log(f"  would archive prices-{m}.<run>.sql.gz  {months[m]:>11,} rows  ≈{sizes[m] / 1e6:7.1f} MB")
            log(f"  total {sum(months.values()):,} rows  ≈{sum(sizes.values()) / 1e6:.0f} MB; "
                f"then delete those rows plus any still pending from earlier runs")
            return 0

        if ids:
            export(conn, ids, archive_dir, run_id)
        save_state(state_path, {"cutoff": cutoff, "max_id": max_id,
                                "updated_at": datetime.now().isoformat(timespec="seconds")})
        if args.apply:
            delete(conn, cutoff, max_id, deadline)
        else:
            log("export-only: nothing deleted")
        return 0
    finally:
        if writes and lock.exists():
            lock.unlink()


if __name__ == "__main__":
    sys.exit(main())
