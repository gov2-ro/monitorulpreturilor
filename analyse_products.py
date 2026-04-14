"""
Analyse product names and brands from the prices DB.

Outputs:
  data/brands.csv       — normalized brands with all raw variants and counts
  data/product_words.csv — most common words and bigrams in product names,
                           diacritic-insensitive grouping

Usage:
  python analyse_products.py
  python analyse_products.py --db path/to/prices.db --top 200
"""
import argparse
import csv
import re
import sys
import unicodedata
from collections import Counter, defaultdict

from db import init_db

# ── Romanian stopwords + noise tokens to exclude from word freq ──────────────
STOPWORDS = {
    "de", "cu", "la", "din", "si", "și", "sau", "pe", "in", "în",
    "cu", "fara", "fără", "pt", "pentru", "ale", "al", "cel", "cea",
    "un", "una", "o", "kg", "g", "l", "ml", "buc", "bucata", "bucată",
    "vrac", "felii", "feliata", "feliată", "feliat", "srl", "com",
    "pan", "fel", "pret", "preț", "set", "mix", "bio", "eco",
}

# Size/quantity patterns to strip from word lists (500g, 1kg, 2l, etc.)
RE_SIZE = re.compile(r"^\d+[\.,]?\d*\s*[gGkKlLmMcC][gGlL]?$")


def strip_diacritics(s: str) -> str:
    """Normalize Romanian diacritics to ASCII for grouping."""
    nfkd = unicodedata.normalize("NFD", s)
    return "".join(c for c in nfkd if unicodedata.category(c) != "Mn")


def normalize_key(s: str) -> str:
    """Uppercase, strip diacritics, remove non-alphanumeric for dedup key."""
    return re.sub(r"[^A-Z0-9]", "", strip_diacritics(s).upper())


def tokenize(name: str) -> list[str]:
    """Split product name into lowercase, diacritic-stripped tokens."""
    tokens = re.split(r"[\s/,.\-–()\[\]]+", name.strip())
    result = []
    for t in tokens:
        t = t.strip()
        if not t:
            continue
        t_norm = strip_diacritics(t).lower()
        if len(t_norm) < 3:
            continue
        if t_norm in STOPWORDS:
            continue
        if RE_SIZE.match(t_norm):
            continue
        if t_norm.replace(".", "").replace(",", "").isdigit():
            continue
        result.append(t_norm)
    return result


def canonical(variants: list[tuple[str, int]]) -> str:
    """Pick the most frequent raw variant as canonical form."""
    return max(variants, key=lambda x: x[1])[0]


# ── Brand analysis ────────────────────────────────────────────────────────────

def analyse_brands(conn, top: int) -> list[dict]:
    rows = conn.execute(
        "SELECT brand, COUNT(*) as cnt FROM prices "
        "WHERE brand IS NOT NULL AND brand != '' AND brand != '-' AND brand != '1' "
        "GROUP BY brand"
    ).fetchall()

    groups: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for brand, cnt in rows:
        key = normalize_key(brand)
        if key:
            groups[key].append((brand, cnt))

    results = []
    for key, variants in groups.items():
        total = sum(c for _, c in variants)
        canon = canonical(variants)
        variant_str = "; ".join(
            f"{b} ({c})" for b, c in sorted(variants, key=lambda x: -x[1])
        )
        results.append({
            "key": key,
            "canonical": canon,
            "total_prices": total,
            "variant_count": len(variants),
            "variants": variant_str,
        })

    results.sort(key=lambda x: -x["total_prices"])
    return results[:top]


# ── Product word/bigram analysis ──────────────────────────────────────────────

def analyse_words(conn, top: int) -> list[dict]:
    names = [row[0] for row in conn.execute("SELECT name FROM products").fetchall()]

    unigrams: Counter[str] = Counter()
    bigrams: Counter[str] = Counter()
    # Track original-cased variants per normalized token for canonical display
    uni_variants: dict[str, Counter[str]] = defaultdict(Counter)
    bi_variants: dict[str, Counter[str]] = defaultdict(Counter)

    for name in names:
        tokens = tokenize(name)
        # raw tokens for variant tracking (strip diacritics but keep case-ish)
        raw_tokens = [
            t for t in re.split(r"[\s/,.\-–()\[\]]+", name.strip()) if t.strip()
        ]
        raw_norm = []
        for t in raw_tokens:
            t_norm = strip_diacritics(t).lower()
            if (len(t_norm) >= 3 and t_norm not in STOPWORDS
                    and not RE_SIZE.match(t_norm)
                    and not t_norm.replace(".", "").replace(",", "").isdigit()):
                raw_norm.append((t_norm, t))

        for t_norm, t_raw in raw_norm:
            unigrams[t_norm] += 1
            uni_variants[t_norm][t_raw] += 1

        for i in range(len(raw_norm) - 1):
            a_norm, a_raw = raw_norm[i]
            b_norm, b_raw = raw_norm[i + 1]
            bi_key = f"{a_norm} {b_norm}"
            bi_raw = f"{a_raw} {b_raw}"
            bigrams[bi_key] += 1
            bi_variants[bi_key][bi_raw] += 1

    results = []
    for token, cnt in unigrams.most_common(top):
        canon = canonical(list(uni_variants[token].items()))
        results.append({"ngram": 1, "token": token, "canonical": canon, "count": cnt})

    for token, cnt in bigrams.most_common(top):
        canon = canonical(list(bi_variants[token].items()))
        results.append({"ngram": 2, "token": token, "canonical": canon, "count": cnt})

    results.sort(key=lambda x: (-x["count"], x["ngram"]))
    return results


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--db", default="data/prices.db")
    parser.add_argument("--top", type=int, default=150,
                        help="top N entries per output (default: 150)")
    args = parser.parse_args()

    conn = init_db(args.db)

    # Brands
    brands = analyse_brands(conn, args.top)
    out_brands = "data/brands.csv"
    with open(out_brands, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["canonical", "total_prices", "variant_count", "variants"], extrasaction="ignore")
        w.writeheader()
        w.writerows(brands)
    print(f"Brands → {out_brands}  ({len(brands)} entries)")

    # Product words
    words = analyse_words(conn, args.top)
    out_words = "data/product_words.csv"
    with open(out_words, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["ngram", "canonical", "count"], extrasaction="ignore")
        w.writeheader()
        w.writerows(words)
    print(f"Words  → {out_words}  ({len(words)} entries)")

    # Quick preview
    print("\n── Top 20 brands ──────────────────────────────────")
    for b in brands[:20]:
        dupes = f"  [{b['variant_count']} variants]" if b["variant_count"] > 1 else ""
        print(f"  {b['total_prices']:5d}  {b['canonical']}{dupes}")

    print("\n── Top 20 unigrams ────────────────────────────────")
    for w in [x for x in words if x["ngram"] == 1][:20]:
        print(f"  {w['count']:5d}  {w['canonical']}")

    print("\n── Top 20 bigrams ─────────────────────────────────")
    for w in [x for x in words if x["ngram"] == 2][:20]:
        print(f"  {w['count']:5d}  {w['canonical']}")

    conn.close()


if __name__ == "__main__":
    main()
