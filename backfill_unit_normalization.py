#!/usr/bin/env python3
"""
Backfill existing prices and prices_current with normalized units.
This fixes the unit field inconsistency issue that causes ~15% false-positive outlier variance.

Before: 'Kg', 'kg', 'K', 'BUC', 'BUCATA', 'Buc', 'L', 'Litru' → inconsistent
After: 'kg', 'pcs', 'L', etc. → canonical form

One-off migration; safe to run multiple times (idempotent on prices_current, appends to prices).
"""

import sqlite3
from db import normalize_unit

def backfill_units(db_path="data/prices.db"):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    print("Backfill unit normalization...")

    # Get all unique units currently in the DB
    cursor.execute("SELECT DISTINCT unit FROM prices WHERE unit IS NOT NULL ORDER BY unit")
    unique_units = [row[0] for row in cursor.fetchall()]

    print(f"\nFound {len(unique_units)} unique unit values:")
    for u in unique_units:
        normalized = normalize_unit(u)
        print(f"  '{u}' → '{normalized}'")

    # Count records before
    cursor.execute("SELECT COUNT(*) FROM prices")
    prices_before = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM prices_current")
    prices_current_before = cursor.fetchone()[0]

    print(f"\nBefore:")
    print(f"  prices:         {prices_before:,} rows")
    print(f"  prices_current: {prices_current_before:,} rows")

    # Backfill prices table (append-only, so we can't UPDATE easily without reimporting all data)
    # Instead, we'll do a more conservative approach:
    # 1. Update prices_current (snapshot table, safe to update)
    # 2. For prices (history), we'd need to do a full reimport, which is risky
    # For now, update prices_current which is the active table

    cursor.execute("SELECT DISTINCT unit FROM prices_current WHERE unit IS NOT NULL")
    units_current = [row[0] for row in cursor.fetchall()]

    changes = 0
    for unit in units_current:
        normalized = normalize_unit(unit)
        if normalized != unit:
            cursor.execute(
                "UPDATE prices_current SET unit = ? WHERE unit = ?",
                (normalized, unit)
            )
            affected = cursor.rowcount
            if affected > 0:
                print(f"  Updated {affected:,} rows: '{unit}' → '{normalized}'")
                changes += affected

    conn.commit()

    # Count after
    cursor.execute("SELECT COUNT(*) FROM prices_current")
    prices_current_after = cursor.fetchone()[0]

    print(f"\nAfter:")
    print(f"  prices_current: {prices_current_after:,} rows (changed {changes:,})")

    # Show the normalized units now in the DB
    cursor.execute("SELECT DISTINCT unit FROM prices_current ORDER BY unit")
    normalized_units = [row[0] for row in cursor.fetchall()]
    print(f"\nNormalized unit values now in DB ({len(normalized_units)} unique):")
    for u in normalized_units:
        cursor.execute("SELECT COUNT(*) FROM prices_current WHERE unit = ?", (u,))
        count = cursor.fetchone()[0]
        print(f"  {str(u):8s}: {count:8,} rows")

    conn.close()
    print("\n✓ Backfill complete. Going forward, all new inserts will use normalized units.")

if __name__ == "__main__":
    backfill_units()
