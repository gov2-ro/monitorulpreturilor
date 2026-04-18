"""Normalize the messy `prices.unit` column to canonical buckets.

The raw unit string varies wildly per network:
  - PROFI emits clean tokens: Kg, Litru, Buc., Buc, Grame
  - KAUFLAND / PENNY emit a single token (BUC / BUCATA) for every product
  - LIDL emits inline pack sizes: 500g, 1l, 4x2ml, 16buc, 132.8g
  - CARREFOUR / SUPECO use truncated codes: K, L, A
  - SELGROS (B2B, excluded from consumer comparisons) uses opaque 2-letter
    codes: BU, PC, CU, BO, PE, CV, CS, CB, CA, TU, DZ, BI

The unit field is a CATEGORICAL HINT (priced by weight / piece / volume),
not a denominator. Prices are pack prices regardless. Normalisation lets
downstream code:
  (a) avoid comparing weight to piece rows in spread analysis,
  (b) render readable unit labels in the UI.

`normalize_unit(raw)` returns one of: 'kg', 'L', 'buc', or None.
"""

import re

_KG_TOKENS = {"kg", "k", "kilogram", "grame", "g"}
_L_TOKENS = {"l", "litru", "ml"}
_BUC_TOKENS = {"buc", "bu", "bucata", "bucati", "pc", "pieces"}

# Inline pack-size like 500g, 1.8l, 4x2ml, 16buc, 132.8g
_INLINE = re.compile(r"^[\d.x]+\s*(kg|g|l|ml|buc)$", re.IGNORECASE)


def normalize_unit(raw):
    """Map a raw `prices.unit` value to 'kg', 'L', 'buc', or None."""
    if not raw:
        return None
    s = raw.strip().lower().rstrip(".")
    if not s:
        return None
    if s in _KG_TOKENS:
        return "kg"
    if s in _L_TOKENS:
        return "L"
    if s in _BUC_TOKENS:
        return "buc"
    m = _INLINE.match(s)
    if m:
        suf = m.group(1).lower()
        if suf in ("kg", "g"):
            return "kg"
        if suf in ("l", "ml"):
            return "L"
        if suf == "buc":
            return "buc"
    return None


def audit(db_path="data/prices.db"):
    """Print coverage stats: distinct raw units → bucket, with row counts."""
    import sqlite3
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT unit, COUNT(*) FROM prices GROUP BY unit ORDER BY COUNT(*) DESC"
    ).fetchall()
    buckets = {"kg": 0, "L": 0, "buc": 0, None: 0}
    unknown_units = []
    total = 0
    for raw, count in rows:
        b = normalize_unit(raw)
        buckets[b] = buckets.get(b, 0) + count
        total += count
        if b is None and count >= 100:
            unknown_units.append((raw, count))
    print(f"Total rows: {total:,}")
    for b, c in sorted(buckets.items(), key=lambda x: -x[1]):
        pct = 100 * c / total if total else 0
        print(f"  {str(b):>5}: {c:>10,}  ({pct:5.1f}%)")
    if unknown_units:
        print("\nUnknown buckets with ≥100 rows:")
        for raw, count in unknown_units:
            print(f"  {raw!r:>14}: {count:>8,}")


if __name__ == "__main__":
    audit()
