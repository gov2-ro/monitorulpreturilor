"""Split-on-5xx batch fetching in fetch_prices._fetch_batch.

Regression for 2026-10-18: dense anchors × 200-ID batches exceed the API's ~30s
server-side limit and 500 deterministically; retrying the identical request burned
~93s/batch and the day wrote 0 prices.
"""
import re
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fetch_prices  # noqa: E402
from fetch_prices import MIN_SPLIT_BATCH, _fetch_batch  # noqa: E402


def _http_error(code):
    resp = requests.Response()
    resp.status_code = code
    return requests.exceptions.HTTPError(f"{code} Server Error", response=resp)


@pytest.fixture
def fake_api(monkeypatch):
    """fetch_xml fails for batches larger than `limit`; parser echoes the IDs."""
    calls = []
    state = {"limit": 100, "code": 500}

    def fetch_xml(url, retries=3, timeout=30):
        ids = [int(x) for x in re.search(r"csvprodids=([^&]*)", url).group(1).split(",")]
        calls.append((len(ids), retries))
        if len(ids) > state["limit"]:
            raise _http_error(state["code"])
        return ids

    def parse(ids, fetched_at):
        return [{"id": 1}], [{"product_id": p} for p in ids]

    monkeypatch.setattr(fetch_prices, "fetch_xml", fetch_xml)
    monkeypatch.setattr(fetch_prices, "parse_stores_and_prices", parse)
    return calls, state


def test_split_recovers_all_products(fake_api):
    calls, _ = fake_api
    stores, prices = _fetch_batch(44.4, 26.1, 5000, list(range(200)), "t")
    assert sorted(p["product_id"] for p in prices) == list(range(200))
    assert stores == [{"id": 1}]                 # deduped across halves
    assert calls[0] == (200, 1)                  # single attempt before splitting


def test_no_split_below_floor_and_raises(fake_api):
    calls, state = fake_api
    state["limit"] = 0
    with pytest.raises(requests.exceptions.HTTPError):
        _fetch_batch(44.4, 26.1, 5000, list(range(200)), "t")
    assert min(n for n, _ in calls) == MIN_SPLIT_BATCH
    assert (MIN_SPLIT_BATCH, 3) in calls         # floor gets normal retries


def test_4xx_is_not_split(fake_api):
    calls, state = fake_api
    state["code"] = 400
    with pytest.raises(requests.exceptions.HTTPError):
        _fetch_batch(44.4, 26.1, 5000, list(range(200)), "t")
    assert calls == [(200, 1)]
