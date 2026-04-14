"""
Fetch slow-changing gas reference data: networks and fuel product types.
Run once (or weekly). Writes to data/prices.db.
UATs are shared with the retail pipeline — run fetch_reference.py to populate them.
"""
import argparse

from tqdm import tqdm

from api import GAS_BASE, fetch_xml, parse_gas_networks, parse_gas_products
from db import init_db, upsert_gas_network, upsert_gas_product


def main(db_path="data/prices.db"):
    conn = init_db(db_path)

    tqdm.write("Fetching gas networks...")
    root = fetch_xml(f"{GAS_BASE}/GetGasNetworks")
    networks = parse_gas_networks(root)
    for n in networks:
        upsert_gas_network(conn, n["id"], n["name"], n["logo_url"])
    conn.commit()
    tqdm.write(f"  {len(networks)} gas networks saved.")
    for n in networks:
        tqdm.write(f"    {n['id']:20s}  {n['name']}")

    tqdm.write("Fetching fuel product types...")
    root = fetch_xml(f"{GAS_BASE}/GetGasProductsFromCatalog")
    products = parse_gas_products(root)
    for p in products:
        upsert_gas_product(conn, p["id"], p["name"], p["logo_url"])
    conn.commit()
    tqdm.write(f"  {len(products)} fuel product types saved.")
    for p in products:
        tqdm.write(f"    {p['id']}  {p['name']}")

    tqdm.write("\nDone.")
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("db", nargs="?", default="data/prices.db",
                        help="path to SQLite DB (default: data/prices.db)")
    args = parser.parse_args()
    main(args.db)
