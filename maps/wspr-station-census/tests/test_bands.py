"""Band decoding, including the collisions between the two conventions."""

from __future__ import annotations

import pytest

from wspr_station_census import bands


@pytest.mark.parametrize(
    ("raw", "convention", "expected"),
    [
        (14, "mhz_int", "20m"),
        (7, "mhz_int", "40m"),
        (-1, "mhz_int", "2200m"),
        (0, "mhz_int", "630m"),
        (20, "meters", "20m"),
        (40, "meters", "40m"),
        (2200, "meters", "2200m"),
    ],
)
def test_known_codes(raw, convention, expected):
    assert bands.decode_band(raw, convention) == expected


def test_the_collision_that_matters():
    """band=40 is 8 m in wspr.rx (40 MHz) but 40 m in wsprdaemon.spots.

    Getting this backwards returns rows silently attributed to the wrong band,
    which is why decoding is always table-specific.
    """
    assert bands.decode_band(40, "mhz_int") == "8m"
    assert bands.decode_band(40, "meters") == "40m"
    assert bands.decode_band(6, "mhz_int") == bands.OTHER  # out-of-band junk
    assert bands.decode_band(6, "meters") == "6m"  # the real 6 m band


def test_spurious_wsprnet_codes_are_other():
    """wspr.rx carries out-of-band decodes at codes with no band allocation."""
    for raw in (2, 9, 15, 19, 27, 30, 46, 107):
        assert bands.decode_band(raw, "mhz_int") == bands.OTHER


def test_unparseable_and_null():
    assert bands.decode_band(None, "meters") == bands.OTHER
    assert bands.decode_band("not-a-number", "meters") == bands.OTHER


def test_unknown_convention_raises():
    with pytest.raises(KeyError):
        bands.decode_band(20, "furlongs")


def test_band_list_is_sorted_longest_wavelength_first():
    labels = ["20m", "2200m", "40m", "other", "80m", "20m"]
    assert bands.format_band_list(labels) == "2200m|80m|40m|20m|other"


def test_band_list_edge_cases():
    assert bands.format_band_list([]) == ""
    assert bands.format_band_list(None) == ""
