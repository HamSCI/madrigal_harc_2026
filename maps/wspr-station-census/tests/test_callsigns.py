"""Callsign normalisation, site grouping and shape classification.

The synthetic-callsign cases are real values sampled from ``wsprdaemon.spots``
for June 2026; the amateur cases include the two polar PSWS stations and a
selection of the receivers that actually appear in the data.
"""

from __future__ import annotations

import pytest

from wspr_station_census import callsigns


@pytest.mark.parametrize(
    "call",
    ["W2NAF", "DP0GVN", "VY0ERC", "EA8BFK", "OE9GHV", "WA2TP", "KD2OM", "G4ZFQ", "KA9Q"],
)
def test_real_amateur_calls(call):
    assert callsigns.classify(call) == "amateur"


@pytest.mark.parametrize("call", ["9A1AA", "4X4ABC", "2E0ABC", "3D2AG", "7X2ABC"])
def test_digit_prefix_amateur_calls(call):
    """Prefixes starting with digits 2-9 are legitimate ITU allocations."""
    assert callsigns.classify(call) == "amateur"


@pytest.mark.parametrize(
    "call",
    [
        "Q51MXZ",
        "QI1HAV",
        "Q44NNZ",
        "QC2RMW",
        "0I5WQZ",
        "0K5SBL",
        "1P9LXC",
        "1A4WVS",
        "128UAB",
        "065MIT",
        "177JWD",
        "158ADU",
    ],
)
def test_telemetry_callsigns_from_the_database(call):
    """Sampled from wsprdaemon.spots; all carry fabricated grids and powers.

    The ITU allocates no prefix beginning 0 or 1, and reserves the Q block for
    Q-codes, so none of these can be a real station.
    """
    assert callsigns.classify(call) == "synthetic_telemetry"


def test_hashed_calls_take_precedence():
    assert callsigns.classify("<VK6TQ>") == "hashed"
    assert callsigns.classify("<EC3ABA>") == "hashed"
    assert callsigns.is_hashed("<VK6TQ>")
    assert not callsigns.is_hashed("VK6TQ")


def test_special_calls_without_a_call_area_digit_are_nonstandard():
    """KFS is a real receiving site; 'nonstandard' is not a synonym for 'bad'."""
    assert callsigns.classify("KFS") == "nonstandard"


def test_normalise():
    assert callsigns.normalise(" <vk6tq> ") == "VK6TQ"
    assert callsigns.normalise(None) == ""
    assert callsigns.normalise("KD7EFG-1") == "KD7EFG-1"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HB9VQQ/RS", "HB9VQQ"),  # receiver label suffix
        ("HB9VQQ/RL", "HB9VQQ"),
        ("OE3GBB/Q", "OE3GBB"),
        ("KX4AZ/T", "KX4AZ"),
        ("KD7EFG-1", "KD7EFG"),  # SSID-style receiver index
        ("W7WKR-K1", "W7WKR"),  # non-numeric receiver label
        ("w7wkr-2", "W7WKR"),  # wsprdaemon.spots stores this one lower case
        ("KFS/OMNI", "KFS"),  # neither segment is a standard call -> first wins
        ("KH6/W2NAF", "W2NAF"),  # leading DXCC prefix -> the real call wins
        ("W2NAF", "W2NAF"),
        ("", ""),
    ],
)
def test_base_call(raw, expected):
    assert callsigns.base_call(raw) == expected


def test_receiver_variants_collapse_to_one_site():
    """The three HB9VQQ receivers are one physical site and must map to one dot."""
    variants = ["HB9VQQ/RS", "HB9VQQ/RL", "HB9VQQ/KE"]
    assert len({callsigns.base_call(call) for call in variants}) == 1


def test_hyphenated_receiver_labels_collapse_to_one_site():
    """W7WKR runs four receivers under three label styles and two cases."""
    variants = ["W7WKR", "W7WKR-1", "w7wkr-2", "W7WKR-K1"]
    assert {callsigns.base_call(call) for call in variants} == {"W7WKR"}


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("WD_3.3.2-2", "wsprdaemon"),
        ("1.4A Kiwi", "kiwisdr"),
        ("web-888", "web888"),
        ("2.7.0", "wsjtx"),
        ("3.0.1", "wsjtx"),
        ("2.7.0-rc3", "wsjtx"),
        ("2.2.159", "wsjtx"),
        ("v1.2.117", "other"),
        (None, "unknown"),
        ("", "unknown"),
        ("\\N", "unknown"),
    ],
)
def test_software_classification(version, expected):
    assert callsigns.classify_software(version) == expected
