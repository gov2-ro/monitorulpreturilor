"""
Product stats — most common products by store coverage.

Outputs:
  data/product_stats.csv   — top products with store coverage, network count, price stats

Usage:
  python product_stats.py
  python product_stats.py --top 100
  python product_stats.py --by-category 10
  python product_stats.py --category CAFEA
  python product_stats.py --db data/prices.db
"""
import argparse
import csv
import textwrap

from db import init_db

COL_WIDTH = 40


def query_top_products(conn, top: int, category: str | None = None) -> list[dict]:
    cat_filter = "AND c.name = :cat" if category else ""
    rows = conn.execute(
        f"""
        SELECT
            p.id,
            p.name,
            COALESCE(c.name, '—')                              AS category,
            COUNT(DISTINCT pc.store_id)                        AS store_count,
            COUNT(DISTINCT n.id)                               AS network_count,
            ROUND(AVG(pc.price), 2)                            AS avg_price,
            MIN(pc.price)                                      AS min_price,
            MAX(pc.price)                                      AS max_price,
            ROUND(100.0 * COUNT(DISTINCT pc.store_id)
                  / (SELECT COUNT(*) FROM stores), 1)          AS coverage_pct
        FROM prices_current pc
        JOIN products      p  ON pc.product_id  = p.id
        JOIN stores        s  ON pc.store_id    = s.id
        JOIN retail_networks n ON s.network_id  = n.id
        LEFT JOIN categories c ON p.categ_id   = c.id
        WHERE 1=1 {cat_filter}
        GROUP BY pc.product_id
        ORDER BY store_count DESC
        LIMIT :top
        """,
        {"cat": category, "top": top},
    ).fetchall()
    cols = [
        "id", "name", "category", "store_count", "network_count",
        "avg_price", "min_price", "max_price", "coverage_pct",
    ]
    return [dict(zip(cols, r)) for r in rows]


def query_top_per_category(conn, per_cat: int) -> list[dict]:
    rows = conn.execute(
        """
        WITH ranked AS (
            SELECT
                p.id,
                p.name,
                COALESCE(c.name, '—')                          AS category,
                COUNT(DISTINCT pc.store_id)                    AS store_count,
                COUNT(DISTINCT n.id)                           AS network_count,
                ROUND(AVG(pc.price), 2)                        AS avg_price,
                MIN(pc.price)                                  AS min_price,
                MAX(pc.price)                                  AS max_price,
                ROUND(100.0 * COUNT(DISTINCT pc.store_id)
                      / (SELECT COUNT(*) FROM stores), 1)      AS coverage_pct,
                RANK() OVER (
                    PARTITION BY COALESCE(c.name, '—')
                    ORDER BY COUNT(DISTINCT pc.store_id) DESC
                ) AS cat_rank
            FROM prices_current pc
            JOIN products      p  ON pc.product_id  = p.id
            JOIN stores        s  ON pc.store_id    = s.id
            JOIN retail_networks n ON s.network_id  = n.id
            LEFT JOIN categories c ON p.categ_id   = c.id
            GROUP BY pc.product_id
        )
        SELECT id, name, category, store_count, network_count,
               avg_price, min_price, max_price, coverage_pct
        FROM ranked
        WHERE cat_rank <= :per_cat
        ORDER BY category, store_count DESC
        """,
        {"per_cat": per_cat},
    ).fetchall()
    cols = [
        "id", "name", "category", "store_count", "network_count",
        "avg_price", "min_price", "max_price", "coverage_pct",
    ]
    return [dict(zip(cols, r)) for r in rows]


def print_table(rows: list[dict], title: str) -> None:
    if not rows:
        print(f"  (no results)")
        return
    print(f"\n{title}")
    print("─" * 110)
    hdr = f"{'#':>4}  {'Product':<{COL_WIDTH}}  {'Category':<22}  {'Stores':>6}  {'Nets':>4}  {'Avg RON':>8}  {'Min':>7}  {'Max':>7}  {'Cover':>6}"
    print(hdr)
    print("─" * 110)
    for i, r in enumerate(rows, 1):
        name = r["name"][:COL_WIDTH]
        cat = r["category"][:22]
        print(
            f"{i:>4}  {name:<{COL_WIDTH}}  {cat:<22}  "
            f"{r['store_count']:>6,}  {r['network_count']:>4}  "
            f"{r['avg_price']:>8.2f}  {r['min_price']:>7.2f}  {r['max_price']:>7.2f}  "
            f"{r['coverage_pct']:>5.1f}%"
        )
    print("─" * 110)


def print_by_category(rows: list[dict], per_cat: int) -> None:
    current_cat = None
    rank = 0
    print(f"\nTop {per_cat} per category")
    for r in rows:
        if r["category"] != current_cat:
            current_cat = r["category"]
            rank = 0
            print(f"\n  ── {current_cat} ──")
            print(f"  {'#':>3}  {'Product':<{COL_WIDTH}}  {'Stores':>6}  {'Nets':>4}  {'Avg RON':>8}  {'Cover':>6}")
        rank += 1
        name = r["name"][:COL_WIDTH]
        print(
            f"  {rank:>3}  {name:<{COL_WIDTH}}  "
            f"{r['store_count']:>6,}  {r['network_count']:>4}  "
            f"{r['avg_price']:>8.2f}  {r['coverage_pct']:>5.1f}%"
        )


def save_csv(rows: list[dict], path: str) -> None:
    if not rows:
        return
    fieldnames = ["name", "category", "store_count", "network_count",
                  "avg_price", "min_price", "max_price", "coverage_pct"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"\nSaved → {path}  ({len(rows)} rows)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--db", default="data/prices.db")
    parser.add_argument("--top", type=int, default=50,
                        help="top N products globally (default: 50)")
    parser.add_argument("--by-category", type=int, metavar="N", dest="by_category",
                        help="show top N per category instead of global list")
    parser.add_argument("--category", metavar="NAME",
                        help="filter to a single category (e.g. CAFEA)")
    parser.add_argument("--csv", default="data/product_stats.csv", metavar="PATH",
                        help="output CSV path (default: data/product_stats.csv)")
    args = parser.parse_args()

    conn = init_db(args.db)

    total_stores = conn.execute("SELECT COUNT(*) FROM stores").fetchone()[0]
    total_products = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    total_covered = conn.execute(
        "SELECT COUNT(DISTINCT product_id) FROM prices_current"
    ).fetchone()[0]

    print(f"\nDatabase: {args.db}")
    print(f"  {total_stores:,} stores  |  {total_products:,} products  |  {total_covered:,} products with current prices")

    if args.by_category:
        rows = query_top_per_category(conn, args.by_category)
        print_by_category(rows, args.by_category)
        save_csv(rows, args.csv)
    else:
        label = f"Top {args.top} products by store coverage"
        if args.category:
            label += f" — category: {args.category}"
        rows = query_top_products(conn, args.top, args.category)
        print_table(rows, label)

        if not args.category:
            # Also show a brief by-category summary (top 3 per cat)
            cat_rows = query_top_per_category(conn, 3)
            print_by_category(cat_rows, 3)

        save_csv(rows, args.csv)

    conn.close()


if __name__ == "__main__":
    main()
