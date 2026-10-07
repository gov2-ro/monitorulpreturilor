"""Per-slice setup cost of fetch_prices.

Regression for 2026-10-07: every cron slice (resumed ones too) ran four full aggregate passes
over prices_current (~17M rows, ~4.5 min each) before fetching anything. Setup + the 1700 s work
window overran the 30-min cron interval, the next tick hit the lock, and the pipeline ran only
~24 slices/day instead of 48.

Maintenance (dead-store pruning, tier refresh) and the stale-first anchor ordering now happen
once per run; resumed slices restore them from the checkpoint.
"""
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fetch_prices as fp  # noqa: E402
from db import init_db  # noqa: E402

# Three stores far apart => three single-store anchors (cluster radius is 5 km).
STORES = [(101, "Bucuresti", 44.43, 26.10), (102, "Cluj", 46.77, 23.59), (103, "Iasi", 47.16, 27.59)]


def test_order_by_saved_applies_saved_rank_and_puts_unknown_first():
    stores = [(1, "a", 0, 0, 0), (2, "b", 0, 0, 0), (3, "c", 0, 0, 0), (4, "d", 0, 0, 0)]
    ordered = fp._order_by_saved(stores, saved_order=[3, 1, 2])
    # 4 is not in the saved order (a store added since run start) -> it has never been
    # fetched, so it goes first, like a never-fetched store in the stale-first order.
    assert [s[0] for s in ordered] == [4, 3, 1, 2]


def test_checkpoint_round_trips_anchor_order(tmp_path):
    path = str(tmp_path / "cp.json")
    fp._save_checkpoint(path, "2026-10-07T00:00:00+00:00", set(), anchor_order=[3, 1, 2])
    assert fp._load_checkpoint(path)["anchor_order"] == [3, 1, 2]


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A temp DB with 3 anchors, and counters around the three whole-table setup passes."""
    db_path = str(tmp_path / "prices.db")
    conn = init_db(db_path)
    for sid, name, lat, lon in STORES:
        conn.execute("INSERT INTO stores (id, name, lat, lon) VALUES (?, ?, ?, ?)",
                     (sid, name, lat, lon))
    conn.execute("INSERT INTO products (id, name) VALUES (1, 'p')")
    conn.commit()
    conn.close()

    calls = {"deactivate": 0, "tiers": 0, "stale_map": 0}

    def counting(key, real):
        def wrapper(*a, **kw):
            calls[key] += 1
            return real(*a, **kw)
        return wrapper

    monkeypatch.setattr(fp, "deactivate_stale_stores", counting("deactivate", fp.deactivate_stale_stores))
    monkeypatch.setattr(fp, "update_store_tiers", counting("tiers", fp.update_store_tiers))
    monkeypatch.setattr(fp, "_load_stale_map", counting("stale_map", fp._load_stale_map))
    old_sigterm = signal.getsignal(signal.SIGTERM)  # main() installs its own handler
    yield type("Env", (), {
        "calls": calls,
        "cp_path": db_path.replace(".db", "_checkpoint.json"),
        # max_runtime≈0 makes the loop hit the time limit at the first anchor: it saves the
        # checkpoint and returns without any API call.
        "run": staticmethod(lambda: fp.main(db_path=db_path, max_runtime=1e-9)),
        "write_cp": staticmethod(lambda **kw: fp._save_checkpoint(
            db_path.replace(".db", "_checkpoint.json"),
            datetime.now(timezone.utc).isoformat(), set(), product_ids=[1],
            weekly_tier_ids=set(), weekly_store_ids=set(),
            full_scan=False, full_scan_week="2000-W01", **kw)),
    })
    signal.signal(signal.SIGTERM, old_sigterm)


def test_new_run_does_maintenance_and_persists_anchor_order(env):
    env.run()
    assert env.calls == {"deactivate": 1, "tiers": 1, "stale_map": 1}
    saved = fp._load_checkpoint(env.cp_path)["anchor_order"]
    assert sorted(saved) == [101, 102, 103]


def test_resumed_slice_skips_maintenance_and_reuses_saved_order(env):
    env.write_cp(anchor_order=[103, 101, 102])  # deliberately not the natural order
    env.run()
    assert env.calls == {"deactivate": 0, "tiers": 0, "stale_map": 0}
    # The saved order must survive the slice, or the next resume would recompute it.
    assert fp._load_checkpoint(env.cp_path)["anchor_order"] == [103, 101, 102]


def test_resumed_legacy_checkpoint_computes_order_once_then_persists_it(env):
    # A checkpoint written before this change has no anchor_order.
    env.write_cp()
    env.run()
    assert env.calls == {"deactivate": 0, "tiers": 0, "stale_map": 1}
    assert sorted(fp._load_checkpoint(env.cp_path)["anchor_order"]) == [101, 102, 103]
