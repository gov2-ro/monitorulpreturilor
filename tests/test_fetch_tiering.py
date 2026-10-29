"""Weekly full-scan scheduling and weekly-tier store rotation in fetch_prices.

Regression for 2026-09-28: every day's first slice was a "full scan" (the daily checkpoint
reset dropped the ISO week), every later slice wasn't, and the full scan never got past
~3 anchors — so ~2,900 weekly-tier stores were never fetched while looking polled.
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fetch_prices import (  # noqa: E402
    WEEKLY_ROTATION_DAYS,
    _decide_full_scan,
    _finish_checkpoint,
    _iso_week_key,
    _load_checkpoint,
    _save_checkpoint,
    _weekly_rotation_skip,
)

WEEK = "2026-W40"


def test_iso_week_key_is_year_qualified():
    assert _iso_week_key(date(2026, 9, 28)) == WEEK
    # ISO week 1 of 2027 starts 2027-01-04; 2027-01-01 is still 2026-W53.
    assert _iso_week_key(date(2027, 1, 1)) == "2026-W53"


def test_first_session_of_week_is_full_scan():
    assert _decide_full_scan(None, "2026-W39", WEEK) is True
    assert _decide_full_scan(None, None, WEEK) is True


def test_later_new_session_same_week_is_not_full_scan():
    # The daily reset drops cp, but the carried full_scan_week must suppress a repeat.
    assert _decide_full_scan(None, WEEK, WEEK) is False


def test_resumed_session_keeps_its_mode():
    # A full scan resumed later the same week must stay a full scan (the old bug flipped it).
    assert _decide_full_scan({"full_scan": True}, WEEK, WEEK) is True
    assert _decide_full_scan({"full_scan": False}, "2026-W39", WEEK) is False


def test_rotation_covers_every_weekly_store_exactly_once_per_period():
    stores = set(range(1000, 1500))
    due_counts = {s: 0 for s in stores}
    for day in range(700000, 700000 + WEEKLY_ROTATION_DAYS):
        skip = _weekly_rotation_skip(stores, day)
        for s in stores - skip:
            due_counts[s] += 1
    assert set(due_counts.values()) == {1}


def test_rotation_due_share_is_about_one_seventh():
    stores = set(range(7000))
    skip = _weekly_rotation_skip(stores, 739000)
    assert len(stores) - len(skip) == 1000


def test_checkpoint_persists_full_scan_across_save_and_finish(tmp_path):
    p = str(tmp_path / "cp.json")
    _save_checkpoint(p, "2026-09-28T00:05:00+00:00", {"1:0"}, full_scan=True,
                     full_scan_week=WEEK, weekly_store_ids=set())
    cp = _load_checkpoint(p)
    assert cp["full_scan"] is True and cp["full_scan_week"] == WEEK
    assert cp["weekly_store_ids"] == []   # empty skip set must survive, not read as "absent"

    _finish_checkpoint(p, "2026-09-28T00:05:00+00:00", {"1:0"}, full_scan=False,
                       full_scan_week=WEEK)
    cp = _load_checkpoint(p)
    assert cp["status"] == "completed" and cp["full_scan_week"] == WEEK
