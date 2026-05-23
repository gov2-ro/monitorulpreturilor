"""One-time migration: normalize price_date / update_date to ISO YYYY-MM-DD HH:MM.

Tables:
  prices          (~20 M rows) — batched by id
  prices_current  (~15 M rows) — batched by rowid
  gas_prices      (~67 K rows) — single pass
  gas_stations    ( ~1.3K rows, update_date column) — single pass

Safe to re-run: the WHERE clause filters on DD.MM or DD/ prefix so already-ISO
rows are never touched.

Usage:
  python migrate_price_dates.py [--db data/prices.db] [--batch 500000] [--dry-run]
"""

import argparse
import sqlite3
import time


# SQL snippet — same transform works for both DD.MM.YYYY and DD/MM/YYYY
_TRANSFORM = (
    "substr({col}, 7, 4) || '-' || substr({col}, 4, 2) || '-' || "
    "substr({col}, 1, 2) || substr({col}, 11)"
)
_OLD_FORMAT = "substr({col}, 3, 1) IN ('.', '/')"


def _count_old(conn, table, col="price_date"):
    return conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE {_OLD_FORMAT.format(col=col)}"
    ).fetchone()[0]


def _migrate_batched(conn, table, id_col, col, batch_size, dry_run):
    """Batch UPDATE a large table by id_col ranges."""
    lo, hi = conn.execute(f"SELECT MIN({id_col}), MAX({id_col}) FROM {table}").fetchone()
    if lo is None:
        print(f"  {table}: empty, skipping.")
        return

    transform = _TRANSFORM.format(col=col)
    old_cond = _OLD_FORMAT.format(col=col)
    total_updated = 0
    batch_num = 0
    cursor = lo

    while cursor <= hi:
        batch_hi = cursor + batch_size - 1
        sql = (
            f"UPDATE {table} SET {col} = {transform} "
            f"WHERE {id_col} BETWEEN {cursor} AND {batch_hi} AND {old_cond}"
        )
        if dry_run:
            n = conn.execute(
                f"SELECT COUNT(*) FROM {table} "
                f"WHERE {id_col} BETWEEN {cursor} AND {batch_hi} AND {old_cond}"
            ).fetchone()[0]
        else:
            cur = conn.execute(sql)
            conn.commit()
            n = cur.rowcount
        total_updated += n
        batch_num += 1
        if n or batch_num % 20 == 0:
            print(f"  {table} batch {batch_num}: ids {cursor}–{batch_hi}, "
                  f"updated {n} rows (running total: {total_updated})", flush=True)
        cursor = batch_hi + 1

    print(f"  {table}: done — {total_updated} rows {'(dry-run)' if dry_run else 'updated'}.")


def _migrate_single(conn, table, col, dry_run):
    """Single-pass UPDATE for small tables."""
    transform = _TRANSFORM.format(col=col)
    old_cond = _OLD_FORMAT.format(col=col)
    sql = f"UPDATE {table} SET {col} = {transform} WHERE {old_cond}"
    if dry_run:
        n = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {old_cond}"
        ).fetchone()[0]
    else:
        cur = conn.execute(sql)
        conn.commit()
        n = cur.rowcount
    print(f"  {table}.{col}: {n} rows {'(dry-run)' if dry_run else 'updated'}.")


def main():
    parser = argparse.ArgumentParser(description="Normalize price_date to ISO format.")
    parser.add_argument("--db", default="data/prices.db")
    parser.add_argument("--batch", type=int, default=500_000, metavar="N",
                        help="rows per batch for large tables (default: 500000)")
    parser.add_argument("--dry-run", action="store_true",
                        help="count rows that would change without writing")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    print(f"DB: {args.db}  batch={args.batch}  dry_run={args.dry_run}")

    # Quick pre-check
    counts = {
        "prices": _count_old(conn, "prices"),
        "prices_current": _count_old(conn, "prices_current"),
        "gas_prices": _count_old(conn, "gas_prices"),
        "gas_stations (update_date)": conn.execute(
            f"SELECT COUNT(*) FROM gas_stations WHERE {_OLD_FORMAT.format(col='update_date')}"
        ).fetchone()[0],
    }
    print("Rows with non-ISO dates:")
    for t, n in counts.items():
        print(f"  {t}: {n:,}")
    print()

    if not any(counts.values()):
        print("Nothing to migrate — all dates already in ISO format.")
        conn.close()
        return

    t0 = time.monotonic()

    print("Migrating prices (batched by id)...")
    _migrate_batched(conn, "prices", "id", "price_date", args.batch, args.dry_run)

    print("\nMigrating prices_current (batched by rowid)...")
    _migrate_batched(conn, "prices_current", "rowid", "price_date", args.batch, args.dry_run)

    print("\nMigrating gas_prices...")
    _migrate_single(conn, "gas_prices", "price_date", args.dry_run)

    print("\nMigrating gas_stations.update_date...")
    _migrate_single(conn, "gas_stations", "update_date", args.dry_run)

    elapsed = int(time.monotonic() - t0)
    print(f"\nDone in {elapsed}s.")
    conn.close()


if __name__ == "__main__":
    main()
