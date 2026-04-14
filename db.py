import sqlite3


def init_db(path="data/prices.db"):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS retail_networks (
        id      TEXT PRIMARY KEY,
        name    TEXT,
        logo_url TEXT
    );
    CREATE TABLE IF NOT EXISTS uats (
        id          INTEGER PRIMARY KEY,
        name        TEXT,
        route_id    TEXT,
        wkt         TEXT,
        center_lat  REAL,
        center_lon  REAL
    );
    CREATE TABLE IF NOT EXISTS categories (
        id        INTEGER PRIMARY KEY,
        name      TEXT,
        parent_id INTEGER,
        logo_url  TEXT,
        source    TEXT
    );
    CREATE TABLE IF NOT EXISTS products (
        id       INTEGER PRIMARY KEY,
        name     TEXT,
        categ_id INTEGER
    );
    CREATE TABLE IF NOT EXISTS stores (
        id         INTEGER PRIMARY KEY,
        name       TEXT,
        addr       TEXT,
        lat        REAL,
        lon        REAL,
        uat_id     INTEGER,
        network_id TEXT,
        zipcode    TEXT
    );
    CREATE TABLE IF NOT EXISTS prices (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id       INTEGER,
        store_id         INTEGER,
        price            REAL,
        price_date       TEXT,
        promo            TEXT,
        brand            TEXT,
        unit             TEXT,
        retail_categ_id  TEXT,
        retail_categ_name TEXT,
        fetched_at       TEXT,
        last_checked_at  TEXT,
        UNIQUE(product_id, store_id, price_date)
    );
    CREATE TABLE IF NOT EXISTS gas_networks (
        id       TEXT PRIMARY KEY,
        name     TEXT,
        logo_url TEXT
    );
    CREATE TABLE IF NOT EXISTS gas_products (
        id       INTEGER PRIMARY KEY,
        name     TEXT,
        logo_url TEXT
    );
    CREATE TABLE IF NOT EXISTS gas_stations (
        id          TEXT PRIMARY KEY,
        name        TEXT,
        addr        TEXT,
        lat         REAL,
        lon         REAL,
        uat_id      INTEGER,
        network_id  TEXT,
        zipcode     TEXT,
        update_date TEXT
    );
    CREATE TABLE IF NOT EXISTS gas_prices (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id      INTEGER,
        station_id      TEXT,
        price           REAL,
        price_date      TEXT,
        fetched_at      TEXT,
        last_checked_at TEXT,
        UNIQUE(product_id, station_id, price_date)
    );
    """)
    conn.commit()
    # Migrate existing DBs that predate last_checked_at columns
    for ddl in [
        "ALTER TABLE prices ADD COLUMN last_checked_at TEXT",
        "ALTER TABLE gas_prices ADD COLUMN last_checked_at TEXT",
    ]:
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()
    return conn


def upsert_network(conn, id, name, logo_url):
    conn.execute(
        "INSERT OR REPLACE INTO retail_networks VALUES (?,?,?)",
        (id, name, logo_url),
    )


def upsert_uat(conn, id, name, route_id, wkt, center_lat, center_lon):
    conn.execute(
        "INSERT OR REPLACE INTO uats VALUES (?,?,?,?,?,?)",
        (id, name, route_id, wkt, center_lat, center_lon),
    )


def upsert_category(conn, id, name, parent_id, logo_url, source):
    conn.execute(
        "INSERT OR REPLACE INTO categories VALUES (?,?,?,?,?)",
        (id, name, parent_id, logo_url, source),
    )


def upsert_product(conn, id, name, categ_id):
    conn.execute(
        "INSERT OR REPLACE INTO products VALUES (?,?,?)",
        (id, name, categ_id),
    )


def upsert_store(conn, id, name, addr, lat, lon, uat_id, network_id, zipcode):
    conn.execute(
        "INSERT OR REPLACE INTO stores VALUES (?,?,?,?,?,?,?,?)",
        (id, name, addr, lat, lon, uat_id, network_id, zipcode),
    )


def insert_price(conn, product_id, store_id, price, price_date, promo,
                 brand, unit, retail_categ_id, retail_categ_name, fetched_at,
                 last_checked_at=None):
    conn.execute(
        """INSERT INTO prices
           (product_id, store_id, price, price_date, promo, brand, unit,
            retail_categ_id, retail_categ_name, fetched_at, last_checked_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(product_id, store_id, price_date)
           DO UPDATE SET last_checked_at = excluded.last_checked_at""",
        (product_id, store_id, price, price_date, promo, brand, unit,
         retail_categ_id, retail_categ_name, fetched_at,
         last_checked_at if last_checked_at is not None else fetched_at),
    )


# ---------------------------------------------------------------------------
# Gas helpers
# ---------------------------------------------------------------------------

def upsert_gas_network(conn, id, name, logo_url):
    conn.execute(
        "INSERT OR REPLACE INTO gas_networks VALUES (?,?,?)",
        (id, name, logo_url),
    )


def upsert_gas_product(conn, id, name, logo_url):
    conn.execute(
        "INSERT OR REPLACE INTO gas_products VALUES (?,?,?)",
        (id, name, logo_url),
    )


def upsert_gas_station(conn, id, name, addr, lat, lon, uat_id, network_id,
                       zipcode, update_date):
    conn.execute(
        "INSERT OR REPLACE INTO gas_stations VALUES (?,?,?,?,?,?,?,?,?)",
        (id, name, addr, lat, lon, uat_id, network_id, zipcode, update_date),
    )


def insert_gas_price(conn, product_id, station_id, price, price_date, fetched_at,
                     last_checked_at=None):
    conn.execute(
        """INSERT INTO gas_prices
           (product_id, station_id, price, price_date, fetched_at, last_checked_at)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(product_id, station_id, price_date)
           DO UPDATE SET last_checked_at = excluded.last_checked_at""",
        (product_id, station_id, price, price_date, fetched_at,
         last_checked_at if last_checked_at is not None else fetched_at),
    )
