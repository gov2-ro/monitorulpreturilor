"""
Analyse product names and brands from the prices DB.

Outputs:
  data/brands.csv              — normalized brands with all raw variants, counts,
                                 and parent categories they appear in
  data/product_words.csv       — most common words and bigrams in product names,
                                 diacritic-insensitive grouping, with parent categories
  data/category_anomalies.csv  — products whose brand is dominant in one category
                                 but the product itself is assigned to a different one

Usage:
  python analyse_products.py
  python analyse_products.py --db path/to/prices.db --top 200
  python analyse_products.py --anomaly-threshold 0.85
"""
import argparse
import csv
import re
import unicodedata
from collections import Counter, defaultdict

from db import init_db

# ── Romanian stopwords + noise tokens to exclude from word freq ──────────────
STOPWORDS = {
    "de", "cu", "la", "din", "si", "și", "sau", "pe", "in", "în",
    "fara", "fără", "pt", "pentru", "ale", "al", "cel", "cea",
    "un", "una", "o", "kg", "g", "l", "ml", "buc", "bucata", "bucată",
    "vrac", "felii", "feliata", "feliată", "feliat", "srl", "com",
    "pan", "fel", "pret", "preț", "set", "mix", "bio", "eco",
}

# Size/quantity patterns to strip (500g, 1kg, 2l, etc.)
RE_SIZE = re.compile(r"^\d+[\.,]?\d*\s*[gGkKlLmMcC][gGlL]?$")


def strip_diacritics(s: str) -> str:
    nfkd = unicodedata.normalize("NFD", s)
    return "".join(c for c in nfkd if unicodedata.category(c) != "Mn")


def normalize_key(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", strip_diacritics(s).upper())


def is_noise(t_norm: str) -> bool:
    return (
        len(t_norm) < 3
        or t_norm in STOPWORDS
        or RE_SIZE.match(t_norm) is not None
        or t_norm.replace(".", "").replace(",", "").isdigit()
    )


def canonical(variants: list[tuple[str, int]]) -> str:
    return max(variants, key=lambda x: x[1])[0]


# ── Load category lookup: id → (name, parent_name) ───────────────────────────

def load_categories(conn) -> dict[int, tuple[str, str | None]]:
    """Returns {cat_id: (cat_name, parent_name_or_None)}."""
    rows = conn.execute("SELECT id, name, parent_id FROM categories").fetchall()
    by_id = {r[0]: (r[1], r[2]) for r in rows}
    result = {}
    for cat_id, (name, parent_id) in by_id.items():
        parent_name = by_id[parent_id][0] if parent_id and parent_id in by_id else None
        result[cat_id] = (name, parent_name)
    return result


def top_level(cat_id: int, cat_lookup: dict) -> str | None:
    """Return the top-level category name for a given category id."""
    if cat_id not in cat_lookup:
        return None
    name, parent_name = cat_lookup[cat_id]
    return parent_name if parent_name else name


# ── Brand analysis ────────────────────────────────────────────────────────────

def analyse_brands(conn, cat_lookup: dict, top: int) -> list[dict]:
    rows = conn.execute(
        """SELECT pr.brand, p.categ_id, COUNT(*) as cnt
           FROM prices pr
           JOIN products p ON pr.product_id = p.id
           WHERE pr.brand IS NOT NULL AND pr.brand != ''
             AND pr.brand != '-' AND pr.brand != '1'
           GROUP BY pr.brand, p.categ_id"""
    ).fetchall()

    groups: dict[str, dict] = defaultdict(lambda: {"variants": defaultdict(int), "cats": set()})
    for brand, categ_id, cnt in rows:
        key = normalize_key(brand)
        if not key:
            continue
        groups[key]["variants"][brand] += cnt
        cat = top_level(categ_id, cat_lookup) if categ_id else None
        if cat:
            groups[key]["cats"].add(cat)

    results = []
    for key, data in groups.items():
        variants = list(data["variants"].items())
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
            "categories": ", ".join(sorted(data["cats"])),
        })

    results.sort(key=lambda x: -x["total_prices"])
    return results[:top]


# ── Product word/bigram analysis ──────────────────────────────────────────────

def analyse_words(conn, cat_lookup: dict, top: int) -> list[dict]:
    rows = conn.execute("SELECT name, categ_id FROM products").fetchall()

    unigrams: Counter[str] = Counter()
    bigrams: Counter[str] = Counter()
    uni_variants: dict[str, Counter[str]] = defaultdict(Counter)
    bi_variants: dict[str, Counter[str]] = defaultdict(Counter)
    uni_cats: dict[str, set[str]] = defaultdict(set)
    bi_cats: dict[str, set[str]] = defaultdict(set)

    for name, categ_id in rows:
        cat = top_level(categ_id, cat_lookup) if categ_id else None

        raw_tokens = [t for t in re.split(r"[\s/,.\-–()\[\]]+", name.strip()) if t.strip()]
        raw_norm = []
        for t in raw_tokens:
            t_norm = strip_diacritics(t).lower()
            if not is_noise(t_norm):
                raw_norm.append((t_norm, t))

        for t_norm, t_raw in raw_norm:
            unigrams[t_norm] += 1
            uni_variants[t_norm][t_raw] += 1
            if cat:
                uni_cats[t_norm].add(cat)

        for i in range(len(raw_norm) - 1):
            a_norm, a_raw = raw_norm[i]
            b_norm, b_raw = raw_norm[i + 1]
            bi_key = f"{a_norm} {b_norm}"
            bi_raw = f"{a_raw} {b_raw}"
            bigrams[bi_key] += 1
            bi_variants[bi_key][bi_raw] += 1
            if cat:
                bi_cats[bi_key].add(cat)

    results = []
    for token, cnt in unigrams.most_common(top):
        canon = canonical(list(uni_variants[token].items()))
        results.append({
            "ngram": 1, "token": token, "canonical": canon, "count": cnt,
            "categories": ", ".join(sorted(uni_cats[token])),
        })
    for token, cnt in bigrams.most_common(top):
        canon = canonical(list(bi_variants[token].items()))
        results.append({
            "ngram": 2, "token": token, "canonical": canon, "count": cnt,
            "categories": ", ".join(sorted(bi_cats[token])),
        })

    results.sort(key=lambda x: (-x["count"], x["ngram"]))
    return results


# ── Category anomaly detection ────────────────────────────────────────────────

def detect_category_anomalies(conn, cat_lookup: dict, threshold: float = 0.80) -> list[dict]:
    """
    For each brand, compute its category distribution across all products.
    If one top-level category accounts for >= threshold of the brand's products,
    flag any product from that brand assigned to a different category.

    Returns rows sorted by brand canonical name, then product name.
    """
    # brand_key → {top_level_cat → product count}
    brand_cat_counts: dict[str, Counter[str]] = defaultdict(Counter)
    # brand_key → canonical brand name
    brand_canon: dict[str, str] = {}

    rows = conn.execute(
        """SELECT pr.brand, p.id, p.name, p.categ_id
           FROM prices pr
           JOIN products p ON pr.product_id = p.id
           WHERE pr.brand IS NOT NULL AND pr.brand != ''
             AND pr.brand != '-' AND pr.brand != '1'
           GROUP BY pr.brand, p.id"""
    ).fetchall()

    # First pass: build brand → category counts and canonical names
    brand_variants: dict[str, Counter[str]] = defaultdict(Counter)
    for brand, prod_id, prod_name, categ_id in rows:
        key = normalize_key(brand)
        if not key:
            continue
        brand_variants[key][brand] += 1
        cat = top_level(categ_id, cat_lookup) if categ_id else None
        if cat:
            brand_cat_counts[key][cat] += 1

    for key, variants in brand_variants.items():
        brand_canon[key] = canonical(list(variants.items()))

    # Second pass: flag products whose category doesn't match brand's dominant
    anomalies = []
    for brand, prod_id, prod_name, categ_id in rows:
        key = normalize_key(brand)
        if not key or key not in brand_cat_counts:
            continue
        cat_counts = brand_cat_counts[key]
        total = sum(cat_counts.values())
        if total < 5:  # too few products to make a meaningful judgement
            continue
        dominant_cat, dominant_cnt = cat_counts.most_common(1)[0]
        dominant_share = dominant_cnt / total
        if dominant_share < threshold:
            continue  # brand is legitimately spread across categories

        prod_cat = top_level(categ_id, cat_lookup) if categ_id else None
        if prod_cat and prod_cat != dominant_cat:
            anomalies.append({
                "brand": brand_canon[key],
                "product_id": prod_id,
                "product_name": prod_name,
                "product_category": prod_cat,
                "dominant_brand_category": dominant_cat,
                "dominant_share_pct": round(dominant_share * 100, 1),
            })

    anomalies.sort(key=lambda x: (x["brand"], x["product_name"]))
    return anomalies




def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--db", default="data/prices.db")
    parser.add_argument("--top", type=int, default=150,
                        help="top N entries per output (default: 150)")
    parser.add_argument("--anomaly-threshold", type=float, default=0.80,
                        help="min share for brand's dominant category to trigger anomaly flag (default: 0.80)")
    args = parser.parse_args()

    conn = init_db(args.db)
    cat_lookup = load_categories(conn)

    # Brands
    brands = analyse_brands(conn, cat_lookup, args.top)
    out_brands = "data/brands.csv"
    with open(out_brands, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["canonical", "total_prices", "variant_count", "variants", "categories"],
            extrasaction="ignore",
        )
        w.writeheader()
        w.writerows(brands)
    print(f"Brands → {out_brands}  ({len(brands)} entries)")

    # Product words
    words = analyse_words(conn, cat_lookup, args.top)
    out_words = "data/product_words.csv"
    with open(out_words, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["ngram", "canonical", "count", "categories"],
            extrasaction="ignore",
        )
        w.writeheader()
        w.writerows(words)
    print(f"Words  → {out_words}  ({len(words)} entries)")

    # Category anomalies
    anomalies = detect_category_anomalies(conn, cat_lookup, args.anomaly_threshold)
    out_anomalies = "data/category_anomalies.csv"
    with open(out_anomalies, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "brand", "product_id", "product_name",
            "product_category", "dominant_brand_category", "dominant_share_pct",
        ])
        w.writeheader()
        w.writerows(anomalies)
    print(f"Anomalies → {out_anomalies}  ({len(anomalies)} entries)")

    # Quick preview
    print("\n── Top 20 brands ──────────────────────────────────")
    for b in brands[:20]:
        dupes = f"  [{b['variant_count']} variants]" if b["variant_count"] > 1 else ""
        cats = f"  ({b['categories']})" if b["categories"] else ""
        print(f"  {b['total_prices']:5d}  {b['canonical']}{dupes}{cats}")

    print("\n── Top 20 unigrams ────────────────────────────────")
    for w in [x for x in words if x["ngram"] == 1][:20]:
        cats = f"  ({w['categories']})" if w["categories"] else ""
        print(f"  {w['count']:5d}  {w['canonical']}{cats}")

    print("\n── Top 20 bigrams ─────────────────────────────────")
    for w in [x for x in words if x["ngram"] == 2][:20]:
        cats = f"  ({w['categories']})" if w["categories"] else ""
        print(f"  {w['count']:5d}  {w['canonical']}{cats}")

    if anomalies:
        print(f"\n── Category anomalies (threshold={args.anomaly_threshold}) ────")
        for a in anomalies[:30]:
            print(
                f"  {a['brand']:<20s}  {a['product_name'][:40]:<40s}"
                f"  {a['product_category']:<30s}  (dominant: {a['dominant_brand_category']}"
                f"  {a['dominant_share_pct']}%)"
            )
        if len(anomalies) > 30:
            print(f"  ... and {len(anomalies) - 30} more — see {out_anomalies}")

    conn.close()


if __name__ == "__main__":
    main()
