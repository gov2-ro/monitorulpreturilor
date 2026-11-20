import gzip
import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import retention
from db import init_db

PID = retention.basket_product_ids()[0]


def _d(days_ago):
    return (date.today() - timedelta(days=days_ago)).isoformat() + " 04:00"


def _setup(tmp_path):
    db = tmp_path / "p.db"
    conn = init_db(str(db))
    conn.execute("INSERT INTO stores (id, name, network_id) VALUES (1,'a','N1'), (2,'b','N1')")
    rows = [(PID, 1, 5.0, _d(100)), (PID, 2, 4.0, _d(100)), (PID, 1, 6.0, _d(60)),
            (999, 1, 1.0, _d(60)), (PID, 1, 7.0, _d(10)), (999, 2, 2.0, _d(1))]
    conn.executemany("INSERT INTO prices (product_id, store_id, price, price_date, promo) "
                     "VALUES (?,?,?,?,'it''s')", rows)
    # rollup already holds an older, lower minimum for the 100-days-ago date
    conn.execute("INSERT INTO price_daily_min VALUES (?,?,?,?)", (_d(100), PID, "N1", 3.0))
    conn.commit()
    conn.close()
    return db


def _run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["retention.py", *argv])
    return retention.main()


def test_dry_run_changes_nothing(tmp_path, monkeypatch):
    db = _setup(tmp_path)
    assert _run(monkeypatch, "--db", str(db), "--archive-dir", str(tmp_path / "a")) == 0
    assert not (tmp_path / "a").exists()
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 6


def test_apply_archives_rolls_up_and_deletes(tmp_path, monkeypatch):
    db, arch = _setup(tmp_path), tmp_path / "a"
    assert _run(monkeypatch, "--db", str(db), "--archive-dir", str(arch), "--apply",
                "--min-free-gb", "0") == 0
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 2
    mins = dict(conn.execute("SELECT price_date, min_price FROM price_daily_min"))
    assert mins[_d(100)] == 3.0 and mins[_d(60)] == 6.0       # MIN-merge keeps older value
    files = sorted(arch.glob("prices-*.sql.gz"))
    assert len(files) == 2 and len((arch / "MANIFEST.sha256").read_text().splitlines()) == 2
    restored = sqlite3.connect(":memory:")
    for f in files:
        restored.executescript(gzip.open(f, "rt").read())
    assert restored.execute("SELECT COUNT(*), MIN(promo) FROM prices").fetchone() == (4, "it's")
    assert json.loads((arch / "retention_state.json").read_text())["cutoff"] == _d(45)[:10]

    # second run: nothing new to export, nothing left to delete
    assert _run(monkeypatch, "--db", str(db), "--archive-dir", str(arch), "--apply",
                "--min-free-gb", "0") == 0
    assert len(list(arch.glob("prices-*.sql.gz"))) == 2


def test_export_only_then_apply_does_not_reexport(tmp_path, monkeypatch):
    db, arch = _setup(tmp_path), tmp_path / "a"
    _run(monkeypatch, "--db", str(db), "--archive-dir", str(arch), "--export-only", "--min-free-gb", "0")
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 6
    _run(monkeypatch, "--db", str(db), "--archive-dir", str(arch), "--apply", "--min-free-gb", "0")
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 2
    assert len(list(arch.glob("prices-*.sql.gz"))) == 2
