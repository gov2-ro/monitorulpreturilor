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
    CREATE TABLE IF NOT EXISTS prices_current (
        product_id        INTEGER NOT NULL,
        store_id          INTEGER NOT NULL,
        price             REAL NOT NULL,
        price_date        TEXT,
        promo             TEXT,
        brand             TEXT,
        unit              TEXT,
        retail_categ_id   TEXT,
        retail_categ_name TEXT,
        first_seen_at     TEXT,
        last_checked_at   TEXT,
        PRIMARY KEY (product_id, store_id)
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
    CREATE TABLE IF NOT EXISTS runs (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        script          TEXT,
        started_at      TEXT,
        finished_at     TEXT,
        status          TEXT,
        uats_processed  INTEGER,
        records_written INTEGER,
        notes           TEXT
    );
    """)
    conn.commit()
    # Migrate existing DBs that predate last_checked_at columns
    for ddl in [
        "ALTER TABLE prices ADD COLUMN last_checked_at TEXT",
        "ALTER TABLE gas_prices ADD COLUMN last_checked_at TEXT",
        "ALTER TABLE stores ADD COLUMN surrounding_population REAL",
    ]:
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()
    # Create analytical views (idempotent)
    conn.executescript("""
    CREATE VIEW IF NOT EXISTS v_price_variability AS
    WITH floor AS (
      SELECT pr.product_id, s.network_id, MIN(pr.price) AS min_p
      FROM prices pr JOIN stores s ON pr.store_id = s.id
      WHERE pr.price_date = (SELECT MAX(price_date) FROM prices) AND pr.price > 0
      GROUP BY pr.product_id, s.network_id
    ),
    clean AS (
      SELECT pr.product_id, pr.store_id, pr.price, s.network_id
      FROM prices pr JOIN stores s ON pr.store_id = s.id
      JOIN floor f ON pr.product_id = f.product_id AND s.network_id = f.network_id
      WHERE pr.price_date = (SELECT MAX(price_date) FROM prices)
        AND pr.price > 0 AND pr.price <= f.min_p * 10
    )
    SELECT n.name AS network, c.product_id, p.name AS product,
           COUNT(DISTINCT c.store_id) AS stores,
           ROUND(MIN(c.price), 2) AS min_price,
           ROUND(AVG(c.price), 2) AS avg_price,
           ROUND(MAX(c.price), 2) AS max_price,
           ROUND((MAX(c.price) - MIN(c.price)) / MIN(c.price) * 100, 1) AS spread_pct
    FROM clean c
    JOIN retail_networks n ON c.network_id = n.id
    JOIN products p ON c.product_id = p.id
    GROUP BY n.name, c.product_id
    HAVING stores >= 3 AND spread_pct > 5
    ORDER BY spread_pct DESC;

    CREATE VIEW IF NOT EXISTS v_cross_network_spread AS
    WITH per_store AS (
      SELECT p.product_id, pr.name AS product, n.name AS network, p.price
      FROM prices p
      JOIN stores s ON p.store_id = s.id
      JOIN retail_networks n ON s.network_id = n.id
      JOIN products pr ON p.product_id = pr.id
      WHERE n.name != 'SELGROS'
    ),
    net_avg AS (
      SELECT product_id, product, network,
             AVG(price) AS avg_price, COUNT(*) AS stores
      FROM per_store GROUP BY product_id, network HAVING stores >= 2
    ),
    product_range AS (
      SELECT product_id, product,
             MIN(avg_price) AS min_net_price, MAX(avg_price) AS max_net_price,
             COUNT(DISTINCT network) AS networks,
             ROUND(MAX(avg_price) - MIN(avg_price), 2) AS spread,
             ROUND(MAX(avg_price) / MIN(avg_price), 3) AS ratio
      FROM net_avg GROUP BY product_id HAVING networks >= 2
    )
    SELECT * FROM product_range ORDER BY ratio DESC;

    CREATE VIEW IF NOT EXISTS v_product_popularity AS
    WITH coverage AS (
      SELECT product_id,
             COUNT(DISTINCT store_id) AS store_count,
             COUNT(*) AS record_count
      FROM prices GROUP BY product_id
    ),
    ranked AS (
      SELECT product_id, store_count, record_count,
             RANK() OVER (ORDER BY store_count  DESC) AS cov_rank,
             RANK() OVER (ORDER BY record_count DESC) AS rec_rank
      FROM coverage
    )
    SELECT r.product_id, pr.name, c.name AS category,
           r.store_count, r.record_count,
           ROUND((r.cov_rank + r.rec_rank) / 2.0, 1) AS blended_rank
    FROM ranked r
    JOIN products pr ON r.product_id = pr.id
    JOIN categories c ON pr.categ_id = c.id
    ORDER BY blended_rank;

    CREATE VIEW IF NOT EXISTS v_private_label_candidates AS
    SELECT pr.name AS product, n.name AS network, COUNT(DISTINCT s.id) AS stores
    FROM prices p
    JOIN stores s ON p.store_id = s.id
    JOIN retail_networks n ON s.network_id = n.id
    JOIN products pr ON p.product_id = pr.id
    GROUP BY p.product_id
    HAVING COUNT(DISTINCT s.network_id) = 1
    ORDER BY stores DESC;

    CREATE VIEW IF NOT EXISTS v_stores_per_network AS
    SELECT COALESCE(n.name, 'Unknown') AS network, COUNT(*) AS stores
    FROM stores s
    LEFT JOIN retail_networks n ON s.network_id = n.id
    GROUP BY network ORDER BY stores DESC;

    CREATE VIEW IF NOT EXISTS v_price_freshness AS
    SELECT price_date,
           COUNT(*) AS records,
           COUNT(DISTINCT store_id) AS stores,
           COUNT(DISTINCT product_id) AS products
    FROM prices GROUP BY price_date ORDER BY price_date DESC;

    CREATE VIEW IF NOT EXISTS v_products_no_prices AS
    SELECT pr.id, pr.name AS product, c.name AS category
    FROM products pr
    JOIN categories c ON pr.categ_id = c.id
    WHERE pr.id NOT IN (SELECT DISTINCT product_id FROM prices)
    ORDER BY c.name, pr.name;
    """)
    conn.commit()
    return conn


def upsert_network(conn, id, name, logo_url):
    conn.execute(
        "INSERT OR REPLACE INTO retail_networks VALUES (?,?,?)",
        (id, name, logo_url),
    )


def ensure_uat(conn, id):
    """Insert UAT id if not already present (preserves existing name/coords)."""
    conn.execute(
        "INSERT OR IGNORE INTO uats (id) VALUES (?)", (id,)
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
        """INSERT INTO stores (id, name, addr, lat, lon, uat_id, network_id, zipcode)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
             name=excluded.name, addr=excluded.addr, lat=excluded.lat,
             lon=excluded.lon, uat_id=excluded.uat_id,
             network_id=excluded.network_id, zipcode=excluded.zipcode""",
        (id, name, addr, lat, lon, uat_id, network_id, zipcode),
    )


def insert_price(conn, product_id, store_id, price, price_date, promo,
                 brand, unit, retail_categ_id, retail_categ_name, fetched_at,
                 last_checked_at=None):
    """
    Insert or update a price. Uses change-based deduplication:
    - If price+promo unchanged: only update last_checked_at in prices_current
    - If changed or new: insert to prices (changelog) + upsert prices_current
    """
    if last_checked_at is None:
        last_checked_at = fetched_at

    # Check current price for this (product_id, store_id)
    cur = conn.execute(
        "SELECT price, promo FROM prices_current WHERE product_id=? AND store_id=?",
        (product_id, store_id)
    )
    existing = cur.fetchone()

    if existing and existing[0] == price and existing[1] == promo:
        # Price unchanged — only update last_checked_at
        conn.execute(
            "UPDATE prices_current SET last_checked_at=? WHERE product_id=? AND store_id=?",
            (last_checked_at, product_id, store_id)
        )
    else:
        # New or changed price — write to history and update snapshot
        conn.execute(
            """INSERT INTO prices
               (product_id, store_id, price, price_date, promo, brand, unit,
                retail_categ_id, retail_categ_name, fetched_at, last_checked_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(product_id, store_id, price_date)
               DO UPDATE SET last_checked_at = excluded.last_checked_at""",
            (product_id, store_id, price, price_date, promo, brand, unit,
             retail_categ_id, retail_categ_name, fetched_at, last_checked_at),
        )
        # Upsert current snapshot
        conn.execute(
            """INSERT INTO prices_current
               (product_id, store_id, price, price_date, promo, brand, unit,
                retail_categ_id, retail_categ_name, first_seen_at, last_checked_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(product_id, store_id) DO UPDATE SET
                 price=excluded.price, price_date=excluded.price_date,
                 promo=excluded.promo, brand=excluded.brand, unit=excluded.unit,
                 retail_categ_id=excluded.retail_categ_id,
                 retail_categ_name=excluded.retail_categ_name,
                 last_checked_at=excluded.last_checked_at""",
            (product_id, store_id, price, price_date, promo, brand, unit,
             retail_categ_id, retail_categ_name, fetched_at, last_checked_at),
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


# ---------------------------------------------------------------------------
# Run log helpers
# ---------------------------------------------------------------------------

def start_run(conn, script, started_at):
    """Insert a 'running' row and return its id."""
    cur = conn.execute(
        "INSERT INTO runs (script, started_at, status) VALUES (?,?,'running')",
        (script, started_at),
    )
    conn.commit()
    return cur.lastrowid


def finish_run(conn, run_id, status, uats_processed, records_written, notes=None):
    """Update the run row with final status and counts."""
    from datetime import datetime, timezone
    conn.execute(
        """UPDATE runs
           SET finished_at=?, status=?, uats_processed=?, records_written=?, notes=?
           WHERE id=?""",
        (datetime.now(timezone.utc).isoformat(),
         status, uats_processed, records_written, notes, run_id),
    )
    conn.commit()


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
