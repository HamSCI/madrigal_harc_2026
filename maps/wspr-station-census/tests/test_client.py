"""Response parsing, cache round-tripping, and timeout bisection.

No network access: the bisection test uses a stub client that fails on wide
windows, which is exactly the behaviour the real nginx front end exhibits.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pandas as pd
import pytest

from wspr_station_census import client as C


def test_parse_compact_each_row_with_names():
    body = "\n".join(
        [
            json.dumps(["station", "n_spots", "days"]),
            json.dumps(["K1ABC", 10, ["2025-06-01", "2025-06-02"]]),
            json.dumps(["W2NAF", 20, ["2025-06-01"]]),
        ]
    )
    frame = C.parse_response(body)
    assert list(frame.columns) == ["station", "n_spots", "days"]
    assert len(frame) == 2
    # Arrays survive as real Python lists, which is why this format is used
    # instead of TSV.
    assert frame.loc[0, "days"] == ["2025-06-01", "2025-06-02"]


def test_parse_header_only_and_empty():
    assert C.parse_response(json.dumps(["a", "b"])).empty
    assert C.parse_response("").empty


def test_cache_round_trip(tmp_path):
    body = "\n".join(
        [json.dumps(["station", "n_spots"]), json.dumps(["K1ABC", 10])]
    )
    path = C.cache_path(tmp_path, "stats_rx_wsprnet", date(2025, 6, 1), date(2025, 6, 11))
    C.write_cache(path, body)
    assert path.exists()
    assert path.name == "20250601_20250611.jsonl.gz"
    frame = C.read_cache(path)
    assert frame.loc[0, "station"] == "K1ABC"


def test_cache_write_is_atomic(tmp_path):
    """No .tmp file is left behind, so an interrupted run leaves no partial cache."""
    path = C.cache_path(tmp_path, "fam", date(2025, 6, 1), date(2025, 6, 2))
    C.write_cache(path, json.dumps(["a"]))
    assert list(path.parent.glob("*.tmp")) == []


class _StubClient:
    """Times out for windows wider than ``limit`` days, like the real proxy."""

    def __init__(self, limit_days: int) -> None:
        self.limit = timedelta(days=limit_days)
        self.calls: list[tuple[date, date]] = []

    def query_frame(self, sql: str) -> pd.DataFrame:
        lo, hi = (date.fromisoformat(part) for part in sql.split())
        self.calls.append((lo, hi))
        if hi - lo > self.limit:
            raise C.GatewayTimeout("too wide")
        return pd.DataFrame({"lo": [lo.isoformat()], "hi": [hi.isoformat()]})


def _build(lo: date, hi: date) -> str:
    return f"{lo.isoformat()} {hi.isoformat()}"


def test_bisection_narrows_until_the_query_answers():
    stub = _StubClient(limit_days=8)
    frames = C.fetch_window(stub, _build, date(2025, 6, 1), date(2025, 7, 1))
    # 30 days -> 15 -> 7/8, so every returned piece is within the limit.
    assert len(frames) == 4
    widths = [
        date.fromisoformat(frame.loc[0, "hi"]) - date.fromisoformat(frame.loc[0, "lo"])
        for frame in frames
    ]
    assert all(width <= timedelta(days=8) for width in widths)
    # And the pieces tile the original window exactly.
    assert sum(widths, timedelta()) == timedelta(days=30)


def test_bisection_preserves_chronological_order():
    stub = _StubClient(limit_days=8)
    frames = C.fetch_window(stub, _build, date(2025, 6, 1), date(2025, 7, 1))
    los = [frame.loc[0, "lo"] for frame in frames]
    assert los == sorted(los)


def test_no_bisection_when_the_window_already_works():
    stub = _StubClient(limit_days=30)
    frames = C.fetch_window(stub, _build, date(2025, 6, 1), date(2025, 6, 11))
    assert len(frames) == 1
    assert len(stub.calls) == 1


def test_a_timeout_at_the_minimum_window_is_a_real_error():
    stub = _StubClient(limit_days=0)
    with pytest.raises(C.GatewayTimeout):
        C.fetch_window(stub, _build, date(2025, 6, 1), date(2025, 6, 3))
