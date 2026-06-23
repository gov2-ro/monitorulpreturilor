import sqlite3


def normalize_unit(unit):
    """
    Normalize unit field to canonical form. Handles inconsistent API responses:
    - 'Kg', 'kg', 'K', 'KILOGRAM' → 'kg'
    - 'BUC', 'BUCATA', 'BUCATI', 'BU', 'Buc', 'Buc.', 'PC', 'PIECE', 'PCS' → 'pcs'
    - 'L', 'Litru', 'Liter' → 'L'
    - 'ml', 'ML' → 'ml'
    - 'g', 'G' → 'g'
    - NULL/empty → None
    """
    if not unit:
        return None

    u = unit.strip().upper()

    # Weight (kilograms)
    if u in ('KG', 'K', 'KILOGRAM'):
        return 'kg'

    # Count (pieces)
    if u in ('BUC', 'BUCATA', 'BUCATI', 'BU', 'PC', 'PIECE', 'PCS', 'PIECES'):
        return 'pcs'

    # Volume (liters)
    if u in ('L', 'LITRU', 'LITER'):
        return 'L'

    # Volume (milliliters)
    if u in ('ML',):
        return 'ml'

    # Weight (grams)
    if u in ('G',):
        return 'g'

    # Unknown: return uppercase for consistency
    return u


def init_db(path="data/prices.db"):
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")
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
        zipcode    TEXT,
        logo_url   TEXT,
        type_id    INTEGER,
        type_name  TEXT
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
    CREATE TABLE IF NOT EXISTS price_flags (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id  INTEGER NOT NULL,
        store_id    INTEGER NOT NULL,
        price_date  TEXT NOT NULL,
        flag_type   TEXT NOT NULL,
        details     TEXT,
        created_at  TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(product_id, store_id, price_date, flag_type)
    );
    CREATE INDEX IF NOT EXISTS idx_price_flags_lookup
        ON price_flags(product_id, store_id, price_date);
    """)
    conn.commit()
    # Migrate existing DBs that predate columns / indexes added after initial schema
    for ddl in [
        "ALTER TABLE prices ADD COLUMN last_checked_at TEXT",
        "ALTER TABLE gas_prices ADD COLUMN last_checked_at TEXT",
        "ALTER TABLE stores ADD COLUMN surrounding_population REAL",
        "ALTER TABLE prices_current ADD COLUMN last_changed_at TEXT",
        "ALTER TABLE stores ADD COLUMN is_active INTEGER DEFAULT 1",
        "ALTER TABLE stores ADD COLUMN fetch_tier TEXT DEFAULT 'daily'",
        "ALTER TABLE stores ADD COLUMN logo_url TEXT",
        "ALTER TABLE stores ADD COLUMN type_id INTEGER",
        "ALTER TABLE stores ADD COLUMN type_name TEXT",
        "CREATE INDEX IF NOT EXISTS idx_prices_current_store ON prices_current(store_id)",
        "ALTER TABLE runs ADD COLUMN acknowledged_at TEXT",
    ]:
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass  # column / index already exists
    # Backfill: last_changed_at = last_checked_at for rows predating this column
    conn.execute(
        "UPDATE prices_current SET last_changed_at = last_checked_at WHERE last_changed_at IS NULL"
    )
    conn.execute("UPDATE stores SET is_active = 1 WHERE is_active IS NULL")
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


def upsert_store(conn, id, name, addr, lat, lon, uat_id, network_id, zipcode,
                 logo_url=None, type_id=None, type_name=None):
    conn.execute(
        """INSERT INTO stores
               (id, name, addr, lat, lon, uat_id, network_id, zipcode,
                logo_url, type_id, type_name, is_active)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,1)
           ON CONFLICT(id) DO UPDATE SET
             name=excluded.name, addr=excluded.addr, lat=excluded.lat,
             lon=excluded.lon, uat_id=excluded.uat_id,
             network_id=COALESCE(excluded.network_id, stores.network_id),
             zipcode=excluded.zipcode,
             logo_url=COALESCE(excluded.logo_url, stores.logo_url),
             type_id=COALESCE(excluded.type_id, stores.type_id),
             type_name=COALESCE(excluded.type_name, stores.type_name),
             is_active=1""",
        (id, name, addr, lat, lon, uat_id, network_id, zipcode,
         logo_url, type_id, type_name),
    )


def insert_price(conn, product_id, store_id, price, price_date, promo,
                 brand, unit, retail_categ_id, retail_categ_name, fetched_at,
                 last_checked_at=None):
    """Insert or update a price. Uses change-based deduplication.

    Returns True if the price/promo changed (or is new), False if unchanged.
    Sets last_changed_at only on actual change; last_checked_at always updated.
    Normalizes unit field to canonical form (kg, pcs, L, ml, g).
    """
    if last_checked_at is None:
        last_checked_at = fetched_at

    unit = normalize_unit(unit)

    cur = conn.execute(
        "SELECT price, promo FROM prices_current WHERE product_id=? AND store_id=?",
        (product_id, store_id)
    )
    existing = cur.fetchone()

    if existing and existing[0] == price and existing[1] == promo:
        # Price unchanged — only bump last_checked_at; last_changed_at stays
        conn.execute(
            "UPDATE prices_current SET last_checked_at=? WHERE product_id=? AND store_id=?",
            (last_checked_at, product_id, store_id)
        )
        return False
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
        # Upsert current snapshot; last_changed_at = now (price actually changed)
        conn.execute(
            """INSERT INTO prices_current
               (product_id, store_id, price, price_date, promo, brand, unit,
                retail_categ_id, retail_categ_name, first_seen_at,
                last_checked_at, last_changed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(product_id, store_id) DO UPDATE SET
                 price=excluded.price, price_date=excluded.price_date,
                 promo=excluded.promo, brand=excluded.brand, unit=excluded.unit,
                 retail_categ_id=excluded.retail_categ_id,
                 retail_categ_name=excluded.retail_categ_name,
                 last_checked_at=excluded.last_checked_at,
                 last_changed_at=excluded.last_changed_at""",
            (product_id, store_id, price, price_date, promo, brand, unit,
             retail_categ_id, retail_categ_name, fetched_at, last_checked_at,
             last_checked_at),
        )
        return True


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


def abandon_stale_runs(conn, script):
    """Mark any 'running' rows for this script as 'abandoned' (left by a prior crash/kill)."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "UPDATE runs SET finished_at=?, status='abandoned' WHERE script=? AND status='running'",
        (now, script),
    )
    conn.commit()
    return cur.rowcount


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


def update_store_tiers(conn, days=7):
    """Sync stores.fetch_tier from prices_current.last_changed_at.

    Promotes stores with no price change in the last `days` days to 'weekly';
    re-demotes stores with a recent change back to 'daily'.
    Called at fetch_prices startup so the tier set is fresh each run.

    Returns (weekly_count, total_active).
    Cold-start: returns (0, N) until last_changed_at data is at least `days` old.
    """
    conn.execute(f"""
        UPDATE stores SET fetch_tier = 'weekly'
        WHERE (is_active IS NULL OR is_active = 1)
          AND id IN (
              SELECT store_id FROM prices_current
              GROUP BY store_id
              HAVING MAX(last_changed_at) IS NOT NULL
                 AND MAX(last_changed_at) < date('now', '-{days} days')
          )
    """)
    conn.execute(f"""
        UPDATE stores SET fetch_tier = 'daily'
        WHERE (is_active IS NULL OR is_active = 1)
          AND id IN (
              SELECT store_id FROM prices_current
              GROUP BY store_id
              HAVING MAX(last_changed_at) >= date('now', '-{days} days')
          )
    """)
    conn.commit()
    weekly = conn.execute(
        "SELECT COUNT(*) FROM stores WHERE fetch_tier = 'weekly' "
        "AND (is_active IS NULL OR is_active = 1)"
    ).fetchone()[0]
    total = conn.execute(
        "SELECT COUNT(*) FROM stores WHERE (is_active IS NULL OR is_active = 1)"
    ).fetchone()[0]
    return weekly, total


def backfill_store_network_ids(conn):
    """Infer network_id for stores where it is NULL but the name is recognisable.

    Applies case-insensitive name-prefix / substring rules against the known
    retail_networks table.  Only fills rows where network_id IS NULL; idempotent.

    Returns dict {network_id: rows_updated}.
    """
    rules = [
        ("UPPER(name) LIKE '%KAUFLAND%'",   "KAUFLAND"),
        ("UPPER(name) LIKE '%PROFI%'",       "PROFI"),
        ("UPPER(name) LIKE '%SELGROS%'",     "SELGROS"),
        ("UPPER(name) LIKE '%SEGLROS%'",     "SELGROS"),   # typo variant
        ("UPPER(name) LIKE '%CORA%'",        "5948914999995"),
        ("UPPER(name) LIKE '%AUCHAN%'",      "AUCHAN"),
        ("UPPER(name) LIKE '%SUPECO%'",      "SUPECO"),
        ("UPPER(name) LIKE '%CARREFOUR%'",   "5940475006709"),
        ("UPPER(name) LIKE '%MEGA IMAGE%'",  "5940475870003"),
        # "MI " and "SG " prefixes are Mega Image store abbreviations
        ("UPPER(name) LIKE 'MI %'",          "5940475870003"),
        ("UPPER(name) LIKE 'SG %'",          "5940475870003"),
        # "Express " prefix is Carrefour Express format
        ("UPPER(name) LIKE 'EXPRESS %'",     "5940475006709"),
    ]
    results = {}
    for condition, network_id in rules:
        cur = conn.execute(
            f"UPDATE stores SET network_id = ? WHERE network_id IS NULL AND {condition}",
            (network_id,),
        )
        if cur.rowcount:
            results[network_id] = results.get(network_id, 0) + cur.rowcount
    conn.commit()
    return results


def backfill_store_network_from_logo(conn):
    """Infer network_id for stores where it is NULL but logo_url matches retail_networks.logo_url.

    More reliable than name matching — the logo URL is a direct API-provided signal.
    Only fills rows where network_id IS NULL; idempotent.
    Returns number of rows updated.
    """
    cur = conn.execute("""
        UPDATE stores
        SET network_id = (
            SELECT id FROM retail_networks
            WHERE retail_networks.logo_url = stores.logo_url
            LIMIT 1
        )
        WHERE network_id IS NULL
          AND logo_url IS NOT NULL
          AND EXISTS (
              SELECT 1 FROM retail_networks
              WHERE retail_networks.logo_url = stores.logo_url
          )
    """)
    conn.commit()
    return cur.rowcount


def check_store_network_conflicts(conn):
    """Report stores where logo_url implies a different network than network_id.

    Returns list of dicts with store id, name, stored network_id, and logo-implied network_id.
    Useful for auditing data quality after bulk fetches.
    """
    rows = conn.execute("""
        SELECT s.id, s.name, s.network_id, rn.id AS logo_network_id, rn.name AS logo_network_name
        FROM stores s
        JOIN retail_networks rn ON rn.logo_url = s.logo_url
        WHERE s.network_id IS NOT NULL
          AND s.network_id != rn.id
    """).fetchall()
    return [
        {"store_id": r[0], "store_name": r[1], "db_network_id": r[2],
         "logo_network_id": r[3], "logo_network_name": r[4]}
        for r in rows
    ]


def deactivate_stale_stores(conn, days=21):
    """Mark stores with no prices_current activity in the last N days as is_active=0.

    Returns the count of newly deactivated stores.
    Called at fetch_prices startup so dead stores are excluded from clustering.
    """
    cur = conn.execute(
        f"""UPDATE stores SET is_active = 0
            WHERE (is_active IS NULL OR is_active = 1)
              AND id IN (
                  SELECT store_id FROM prices_current
                  GROUP BY store_id
                  HAVING MAX(last_checked_at) < datetime('now', '-{days} days')
              )"""
    )
    conn.commit()
    return cur.rowcount


def propagate_last_checked(conn, store_ids, fetched_at):
    """Bulk-update last_checked_at for all prices_current rows of the given stores.

    Used by the canary logic to mark pure-uniform-network anchors as current
    without making an API call — keeps the freshness audit score accurate.
    """
    if not store_ids:
        return
    ph = ",".join("?" * len(store_ids))
    conn.execute(
        f"UPDATE prices_current SET last_checked_at=? WHERE store_id IN ({ph})",
        [fetched_at] + list(store_ids),
    )
    conn.commit()


def propagate_network_prices(conn, source_store_id, target_store_ids, fetched_at):
    """Copy prices_current from a sentinel store to all non-sentinel stores in the same network.

    Used by sentinel mode: after a Tier-A sentinel's prices are fetched, propagate
    them to the non-sentinel stores so they stay current without individual API calls.
    Accuracy caveat: ~10-15% of products in Tier-A networks vary regionally; those
    products will get the sentinel's price until the weekly full-scan corrects them.

    Writes to both prices (history, INSERT OR IGNORE) and prices_current (upsert).
    Uses SQL-level INSERT … SELECT per target store for efficiency.
    Returns number of target stores updated.
    """
    if not target_store_ids:
        return 0

    updated = 0
    for target_id in target_store_ids:
        conn.execute(
            """INSERT OR IGNORE INTO prices
               (product_id, store_id, price, price_date, promo, brand, unit,
                retail_categ_id, retail_categ_name, fetched_at, last_checked_at)
               SELECT product_id, ?, price, price_date, promo, brand, unit,
                      retail_categ_id, retail_categ_name, ?, ?
               FROM prices_current WHERE store_id = ?""",
            (target_id, fetched_at, fetched_at, source_store_id),
        )
        conn.execute(
            """INSERT INTO prices_current
               (product_id, store_id, price, price_date, promo, brand, unit,
                retail_categ_id, retail_categ_name, first_seen_at,
                last_checked_at, last_changed_at)
               SELECT product_id, ?, price, price_date, promo, brand, unit,
                      retail_categ_id, retail_categ_name, ?, ?, ?
               FROM prices_current WHERE store_id = ?
               ON CONFLICT(product_id, store_id) DO UPDATE SET
                 price=excluded.price, price_date=excluded.price_date,
                 promo=excluded.promo, brand=excluded.brand, unit=excluded.unit,
                 retail_categ_id=excluded.retail_categ_id,
                 retail_categ_name=excluded.retail_categ_name,
                 last_checked_at=excluded.last_checked_at,
                 last_changed_at=CASE
                   WHEN prices_current.price != excluded.price
                     OR prices_current.promo != excluded.promo
                   THEN excluded.last_changed_at
                   ELSE prices_current.last_changed_at
                 END""",
            (target_id, fetched_at, fetched_at, fetched_at, source_store_id),
        )
        updated += 1
    conn.commit()
    return updated


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


def upsert_price_flag(conn, product_id, store_id, price_date, flag_type, details=None):
    """Insert a price flag, ignoring duplicates (same product/store/date/type)."""
    import json as _json
    conn.execute(
        """INSERT OR IGNORE INTO price_flags
           (product_id, store_id, price_date, flag_type, details)
           VALUES (?,?,?,?,?)""",
        (product_id, store_id, price_date, flag_type,
         _json.dumps(details) if details is not None else None),
    )
