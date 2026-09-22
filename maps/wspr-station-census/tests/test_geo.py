"""Maidenhead decoding and position validity."""

from __future__ import annotations

import pytest

from wspr_station_census import geo


def test_vy0erc_matches_the_database():
    """Decode VY0ERC's locator at Eureka, Ellesmere Island.

    wspr.rx reports max(rx_lat)=80.0625 over the period, and that station is this
    one: exact agreement confirms the decoding.
    """
    pos = geo.decode_grid("ER60tb")
    assert pos is not None
    assert pos.lat == pytest.approx(80.0625)
    assert pos.lon == pytest.approx(-86.375)
    assert pos.precision == 6


def test_dp0gvn_four_character_grid():
    pos = geo.decode_grid("IB59")
    assert pos.lat == pytest.approx(-70.5)
    assert pos.lon == pytest.approx(-9.0)
    assert pos.cell_lat_deg == pytest.approx(1.0)


def test_case_insensitive():
    assert geo.decode_grid("er60TB") == geo.decode_grid("ER60tb")


def test_grid_centres_not_corners():
    """A bare 2-character field must land at the centre of its 20x10 deg cell."""
    pos = geo.decode_grid("AA")
    assert pos.lat == pytest.approx(-85.0)
    assert pos.lon == pytest.approx(-170.0)


def test_eight_character_grid():
    pos = geo.decode_grid("FN20xr55")
    assert pos.precision == 8
    assert pos.cell_lat_deg == pytest.approx(1.0 / 240.0)


@pytest.mark.parametrize("grid", ["", None, "nonsense", "ZZ99", "FN2", "FN20x", "12AB"])
def test_invalid_grids_return_none(grid):
    assert geo.decode_grid(grid) is None
    assert not geo.is_valid_grid(grid)


def test_odd_lengths_rejected_rather_than_guessed():
    assert geo.decode_grid("FN20x") is None


def test_sentinel_and_out_of_range_latlon_rejected():
    """The two pathologies actually present in the source tables."""
    assert not geo.is_valid_latlon(-999, -999)  # wsprdaemon.spots sentinel
    assert not geo.is_valid_latlon(157.3, 10.0)  # impossible latitude, both tables
    assert not geo.is_valid_latlon(166.06, 10.0)
    assert geo.is_valid_latlon(80.0625, -86.375)
    assert geo.is_valid_latlon(-70.5, -9.0)
    assert not geo.is_valid_latlon(None, None)
    assert not geo.is_valid_latlon(float("nan"), 0.0)


def test_hemisphere():
    assert geo.hemisphere(80.0) == "N"
    assert geo.hemisphere(-70.5) == "S"
    assert geo.hemisphere(0.0) == "N"
    assert geo.hemisphere(None) == ""


def test_great_circle_sanity():
    """Eureka to Neumayer is close to a pole-to-pole path."""
    km = geo.great_circle_km(80.0625, -86.375, -70.5, -9.0)
    assert 16000 < km < 17500
    assert geo.great_circle_km(0, 0, 0, 0) == pytest.approx(0.0)


# ------------------------------------------------------- geomagnetic (optional)

pytest_aacgm = pytest.mark.skipif(
    not geo.aacgm_available(), reason="aacgmv2 not installed"
)


@pytest_aacgm
def test_aacgm_separates_the_two_polar_psws_stations():
    """Geographic latitude does not order these two the way magnetic latitude does.

    VY0ERC sits deep in the polar cap; DP0GVN, despite a comparable geographic
    latitude, is equatorward of the typical auroral oval. This is the whole
    reason the census carries a CGM column.
    """
    lats = [80.0625, -70.6458]
    lons = [-86.375, -8.2917]
    vy0erc, dp0gvn = geo.aacgm_latitudes(lats, lons, epoch="2025-12-15")
    assert vy0erc == pytest.approx(86.5, abs=1.0)
    assert dp0gvn == pytest.approx(-60.7, abs=1.0)
    # Geographic |lat| ranks DP0GVN and VY0ERC within 10 deg of each other;
    # magnetic |lat| separates them by ~26 deg.
    assert abs(vy0erc) - abs(dp0gvn) > 20


@pytest_aacgm
def test_aacgm_preserves_input_order_and_marks_bad_rows():
    lats = [80.0625, -999.0, -70.6458, None]
    lons = [-86.375, -999.0, -8.2917, None]
    out = geo.aacgm_latitudes(lats, lons, epoch="2025-12-15")
    assert len(out) == 4
    assert out[1] is None and out[3] is None
    assert out[0] > 0 and out[2] < 0


@pytest_aacgm
def test_aacgm_deduplicates_repeated_positions():
    """Many stations share a grid cell; repeats must give identical answers."""
    out = geo.aacgm_latitudes([80.0625] * 5, [-86.375] * 5, epoch="2025-12-15")
    assert len(set(out)) == 1


def test_aacgm_returns_all_none_without_the_optional_dependency(monkeypatch):
    """The census must still build when aacgmv2 is absent."""
    monkeypatch.setattr(geo, "aacgm_available", lambda: False)
    assert geo.aacgm_available() is False
