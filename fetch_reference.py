"""
Fetch slow-changing reference data: networks, UATs, categories, products.
Run once (or weekly). Writes to prices.db.
"""
import sys

from api import BASE, fetch_xml, parse_categories, parse_networks, parse_products, parse_uats
from db import (
    init_db,
    upsert_category,
    upsert_network,
    upsert_product,
    upsert_uat,
)


def main(db_path="prices.db"):
    conn = init_db(db_path)

    # Networks
    print("Fetching retail networks...")
    root = fetch_xml(f"{BASE}/GetRetailNetworks")
    networks = parse_networks(root)
    for n in networks:
        upsert_network(conn, n["id"], n["name"], n["logo_url"])
    conn.commit()
    print(f"  {len(networks)} networks saved.")

    # UATs
    print("Fetching UATs...")
    root = fetch_xml(f"{BASE}/GetUATByName")
    uats = parse_uats(root)
    for u in uats:
        upsert_uat(conn, u["id"], u["name"], u["route_id"],
                   u["wkt"], u["center_lat"], u["center_lon"])
    conn.commit()
    print(f"  {len(uats)} UATs saved.")

    # Categories – two sources
    for endpoint, source in [
        ("GetProductCategoriesNetwork", "network"),
        ("GetProductCategoriesNetworkOUG", "oug"),
    ]:
        print(f"Fetching categories ({source})...")
        root = fetch_xml(f"{BASE}/{endpoint}")
        cats = parse_categories(root, source)
        for c in cats:
            upsert_category(conn, c["id"], c["name"], c["parent_id"],
                            c["logo_url"], c["source"])
        conn.commit()
        print(f"  {len(cats)} categories saved.")

    # Products – one request per category
    categ_ids = [row[0] for row in conn.execute("SELECT id FROM categories")]
    print(f"Fetching products for {len(categ_ids)} categories...")
    total = 0
    for i, cid in enumerate(categ_ids, 1):
        print(f"  [{i}/{len(categ_ids)}] categ {cid}...", end=" ", flush=True)
        root = fetch_xml(f"{BASE}/GetCatalogProductsByNameNetwork?CSVcategids={cid}")
        prods = parse_products(root)
        for p in prods:
            # API returns empty Prodcateg/Id; fall back to the queried category
            upsert_product(conn, p["id"], p["name"], p["categ_id"] if p["categ_id"] is not None else cid)
        conn.commit()
        total += len(prods)
        print(len(prods))

    print(f"\nDone. {total} products saved.")
    conn.close()


if __name__ == "__main__":
    db_path = sys.argv[1] if len(sys.argv) > 1 else "prices.db"
    main(db_path)
