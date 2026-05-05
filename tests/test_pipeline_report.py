# tests/test_pipeline_report.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from db import init_db


@pytest.fixture
def db():
    conn = init_db(":memory:")
    conn.execute("INSERT INTO retail_networks VALUES ('N1','PROFI',NULL)")
    conn.execute("INSERT INTO uats VALUES (1,'Cluj',NULL,NULL,46.7,23.6)")
    conn.execute("INSERT INTO stores (id,name,addr,lat,lon,uat_id,network_id,zipcode) VALUES (1,'Profi Cluj','Str 1',46.7,23.6,1,'N1','400000')")
    conn.execute("INSERT INTO stores (id,name,addr,lat,lon,uat_id,network_id,zipcode) VALUES (2,'Profi Dej','Str 2',47.1,23.9,1,'N1','405300')")
    conn.execute("INSERT INTO categories VALUES (10,'Lactate',1,NULL,'api')")
    conn.execute("INSERT INTO products VALUES (1,'Lapte 1L',10)")
    conn.execute("INSERT INTO products VALUES (2,'Unt 200g',10)")
    # Store 1: fresh (last_checked_at = today)
    conn.execute("INSERT INTO prices_current VALUES (1,1,10.0,'2026-04-30',NULL,NULL,'L',NULL,NULL,'2026-04-25','2026-04-30')")
    # Store 2: stale (last_checked_at = 4 days ago)
    conn.execute("INSERT INTO prices_current VALUES (1,2,10.5,'2026-04-26',NULL,NULL,'L',NULL,NULL,'2026-04-20','2026-04-26')")
    # Price history for velocity: product 1, store 1 changed on 2026-04-30
    conn.execute("INSERT INTO prices (product_id,store_id,price,price_date,promo,brand,unit,retail_categ_id,retail_categ_name,fetched_at) VALUES (1,1,10.0,'2026-04-30',NULL,NULL,'L',NULL,NULL,'2026-04-30T04:00:00')")
    conn.execute("INSERT INTO prices (product_id,store_id,price,price_date,promo,brand,unit,retail_categ_id,retail_categ_name,fetched_at) VALUES (1,1,9.5,'2026-04-29',NULL,NULL,'L',NULL,NULL,'2026-04-29T04:00:00')")
    # Outlier: product 2, store 1 has abnormally high price
    conn.execute("INSERT INTO prices_current VALUES (2,1,999.0,'2026-04-30',NULL,NULL,'BUC',NULL,NULL,'2026-04-25','2026-04-30')")
    conn.execute("INSERT INTO prices_current VALUES (2,2,5.0,'2026-04-30',NULL,NULL,'BUC',NULL,NULL,'2026-04-25','2026-04-30')")
    # Promo sanity: product 1 store 2 has suspiciously deep promo (0.10 lei for 10 lei product)
    conn.execute("INSERT INTO prices_current VALUES (1,2,0.10,'2026-04-30','PROMO',NULL,'L',NULL,NULL,'2026-04-25','2026-04-30') ON CONFLICT(product_id,store_id) DO UPDATE SET price=0.10, promo='PROMO'")
    # Runs
    conn.execute("INSERT INTO runs VALUES (1,'fetch_prices.py','2026-04-30T04:00:00','2026-04-30T05:10:00','completed',300,50000,NULL)")
    conn.execute("INSERT INTO runs VALUES (2,'fetch_prices.py','2026-04-29T04:00:00',NULL,'interrupted',100,15000,NULL)")
    conn.commit()
    return conn


def test_store_freshness_detects_stale(db):
    from generate_pipeline_report import load_store_freshness
    rows = load_store_freshness(db, as_of_date="2026-05-05")
    stale = [r for r in rows if r["stale"]]
    assert len(stale) >= 1
    assert any(r["store_id"] == 2 for r in stale)


def test_store_freshness_fresh_store_not_stale(db):
    from generate_pipeline_report import load_store_freshness
    rows = load_store_freshness(db, as_of_date="2026-04-30")
    stale = [r for r in rows if r["stale"] and r["store_id"] == 1]
    assert len(stale) == 0


def test_run_stats_counts_completion(db):
    from generate_pipeline_report import load_run_stats
    stats = load_run_stats(db)
    assert len(stats) == 2
    completed = [r for r in stats if r["status"] == "completed"]
    assert len(completed) == 1


def test_outlier_summary_detects_outlier(db):
    from generate_pipeline_report import compute_outlier_summary
    summary = compute_outlier_summary(db)
    # product 2: price 999 vs avg ~502 — the 999 should be flagged
    assert summary["flagged_count"] >= 1
    assert summary["flagged_pct"] > 0


def test_price_velocity_computes_change(db):
    from generate_pipeline_report import compute_price_velocity
    vel = compute_price_velocity(db)
    assert vel["curr_date"] == "2026-04-30"
    assert vel["prev_date"] == "2026-04-29"
    assert vel["changed_count"] >= 1


def test_promo_sanity_catches_deep_promo(db):
    from generate_pipeline_report import load_promo_sanity_issues
    issues = load_promo_sanity_issues(db)
    assert len(issues) >= 1
    pids = [r["product_id"] for r in issues]
    assert 1 in pids  # product 1 has 0.10 promo vs 10.0 median


def test_render_html_returns_string(db):
    from generate_pipeline_report import build_report_data, render_html
    data = build_report_data(db, as_of_date="2026-05-05")
    html = render_html(data)
    assert "<html" in html
    assert "pipeline" in html.lower()
