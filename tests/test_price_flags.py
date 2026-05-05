# tests/test_price_flags.py
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from db import init_db


@pytest.fixture
def db():
    conn = init_db(":memory:")
    conn.execute("INSERT INTO retail_networks VALUES ('N1','PROFI',NULL)")
    conn.execute("INSERT INTO stores (id,name,addr,lat,lon,uat_id,network_id,zipcode) VALUES (1,'Profi Cluj','Str 1',46.7,23.6,1,'N1','400000')")
    conn.execute("INSERT INTO stores (id,name,addr,lat,lon,uat_id,network_id,zipcode) VALUES (2,'Profi Dej','Str 2',47.1,23.9,1,'N1','400100')")
    conn.execute("INSERT INTO stores (id,name,addr,lat,lon,uat_id,network_id,zipcode) VALUES (3,'Profi Turda','Str 3',46.5,23.7,1,'N1','400200')")
    conn.execute("INSERT INTO categories VALUES (10,'Lactate',1,NULL,'api')")
    conn.execute("INSERT INTO products VALUES (1,'Lapte 1L',10)")
    conn.execute("INSERT INTO products VALUES (2,'Unt 200g',10)")
    # Product 1: prices mostly 10 lei, one outlier at 999 lei
    conn.execute("INSERT INTO prices_current (product_id,store_id,price,price_date,promo,brand,unit,retail_categ_id,retail_categ_name,first_seen_at,last_checked_at) VALUES (1,1,10.0,'2026-04-30',NULL,NULL,'L',NULL,NULL,'2026-04-25','2026-04-30')")
    conn.execute("INSERT INTO prices_current (product_id,store_id,price,price_date,promo,brand,unit,retail_categ_id,retail_categ_name,first_seen_at,last_checked_at) VALUES (1,2,10.2,'2026-04-30',NULL,NULL,'L',NULL,NULL,'2026-04-25','2026-04-30')")
    conn.execute("INSERT INTO prices_current (product_id,store_id,price,price_date,promo,brand,unit,retail_categ_id,retail_categ_name,first_seen_at,last_checked_at) VALUES (1,3,999.0,'2026-04-30',NULL,NULL,'L',NULL,NULL,'2026-04-25','2026-04-30')")
    # Product 2: normal prices 5 lei, promo at 0.50 (too deep)
    conn.execute("INSERT INTO prices_current (product_id,store_id,price,price_date,promo,brand,unit,retail_categ_id,retail_categ_name,first_seen_at,last_checked_at) VALUES (2,1,5.0,'2026-04-30',NULL,NULL,'BUC',NULL,NULL,'2026-04-25','2026-04-30')")
    conn.execute("INSERT INTO prices_current (product_id,store_id,price,price_date,promo,brand,unit,retail_categ_id,retail_categ_name,first_seen_at,last_checked_at) VALUES (2,2,5.1,'2026-04-30',NULL,NULL,'BUC',NULL,NULL,'2026-04-25','2026-04-30')")
    conn.execute("INSERT INTO prices_current (product_id,store_id,price,price_date,promo,brand,unit,retail_categ_id,retail_categ_name,first_seen_at,last_checked_at) VALUES (2,3,0.50,'2026-04-30','PROMO',NULL,'BUC',NULL,NULL,'2026-04-25','2026-04-30')")
    # Price history for spike detection: product 1 store 1 jumped 80%
    conn.execute("INSERT INTO prices (product_id,store_id,price,price_date,promo,brand,unit,retail_categ_id,retail_categ_name,fetched_at) VALUES (1,1,10.0,'2026-04-30',NULL,NULL,'L',NULL,NULL,'2026-04-30')")
    conn.execute("INSERT INTO prices (product_id,store_id,price,price_date,promo,brand,unit,retail_categ_id,retail_categ_name,fetched_at) VALUES (1,1,5.5,'2026-04-29',NULL,NULL,'L',NULL,NULL,'2026-04-29')")
    conn.commit()
    return conn


def test_flag_outlier_prices(db):
    from build_price_flags import flag_outlier_prices
    count = flag_outlier_prices(db)
    db.commit()
    assert count >= 1
    row = db.execute(
        "SELECT flag_type, details FROM price_flags WHERE product_id=1 AND store_id=3"
    ).fetchone()
    assert row is not None
    assert row[0] == "outlier_price"
    d = json.loads(row[1])
    assert d["price"] == 999.0
    assert "z_score" in d


def test_flag_outlier_no_false_positive(db):
    from build_price_flags import flag_outlier_prices
    flag_outlier_prices(db)
    db.commit()
    # Product 1, store 1 (price=10.0) should NOT be flagged
    row = db.execute(
        "SELECT * FROM price_flags WHERE product_id=1 AND store_id=1 AND flag_type='outlier_price'"
    ).fetchone()
    assert row is None


def test_flag_price_spikes(db):
    from build_price_flags import flag_price_spikes
    count = flag_price_spikes(db)
    db.commit()
    assert count >= 1  # product 1 store 1: 5.5->10.0 = +81.8%
    row = db.execute(
        "SELECT flag_type, details FROM price_flags WHERE product_id=1 AND store_id=1"
    ).fetchone()
    assert row is not None
    assert row[0] == "price_spike"
    d = json.loads(row[1])
    assert d["pct_change"] > 50


def test_flag_promo_too_deep(db):
    from build_price_flags import flag_promo_too_deep
    count = flag_promo_too_deep(db)
    db.commit()
    assert count >= 1  # product 2 store 3: 0.50 promo vs 5.0 regular
    row = db.execute(
        "SELECT flag_type FROM price_flags WHERE product_id=2 AND store_id=3"
    ).fetchone()
    assert row is not None
    assert row[0] == "promo_too_deep"


def test_flag_idempotent(db):
    from build_price_flags import flag_outlier_prices
    flag_outlier_prices(db)
    db.commit()
    count_before = db.execute("SELECT COUNT(*) FROM price_flags").fetchone()[0]
    flag_outlier_prices(db)
    db.commit()
    count_after = db.execute("SELECT COUNT(*) FROM price_flags").fetchone()[0]
    assert count_before == count_after
