"""Rollup arithmetic and the quality gates.

These tests use synthetic station-window frames rather than the network, and
concentrate on the properties that are easy to get silently wrong: day-set
unions, gap computation, weighted quantile means, and the transmitter gate.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from wspr_station_census import summarize as S


def _window(station, days, n_spots, **extra):
    """Build a one-row stats-window frame."""
    row = {
        "station": station,
        "n_spots": n_spots,
        "n_slots": n_spots,
        "n_partners": extra.pop("n_partners", 5),
        "days": days,
        "bands_raw": extra.pop("bands_raw", [14]),
        "n_grids": 1,
        "grids": extra.pop("grids", ["FN20"]),
        "first_spot": f"{days[0]} 00:00:00",
        "last_spot": f"{days[-1]} 23:58:00",
        "snr_sum": extra.pop("snr_sum", -10 * n_spots),
        "snr_min": -30,
        "snr_max": 0,
        "snr_p50": extra.pop("snr_p50", -10.0),
        "n_dist_ok": n_spots,
        "dist_sum": 1000 * n_spots,
        "dist_max": 5000,
        "dist_p50": 900.0,
        "dist_p90": 3000.0,
        "n_geo_invalid": 0,
        "window_start": days[0],
    }
    row.update(extra)
    return pd.DataFrame([row])


def test_day_sets_union_exactly_across_windows():
    acc = S._StatsAccumulator()
    acc.ingest(_window("K1ABC", ["2025-06-01", "2025-06-02"], 10))
    acc.ingest(_window("K1ABC", ["2025-06-02", "2025-06-03"], 10))  # 06-02 repeats
    out = acc.station_frame(period_days=395).set_index("station")
    assert out.loc["K1ABC", "n_days_active"] == 3
    assert out.loc["K1ABC", "n_spots"] == 20


def test_max_gap_days_counts_silent_days():
    acc = S._StatsAccumulator()
    acc.ingest(_window("K1ABC", ["2025-06-01"], 5))
    acc.ingest(_window("K1ABC", ["2025-06-05"], 5))  # 3 silent days: 02, 03, 04
    out = acc.station_frame(period_days=395).set_index("station")
    assert out.loc["K1ABC", "max_gap_days"] == 3


def test_no_gap_when_every_day_is_active():
    acc = S._StatsAccumulator()
    acc.ingest(_window("K1ABC", ["2025-06-01", "2025-06-02", "2025-06-03"], 9))
    out = acc.station_frame(period_days=395).set_index("station")
    assert out.loc["K1ABC", "max_gap_days"] == 0


def test_means_are_exact_and_medians_are_spot_weighted():
    acc = S._StatsAccumulator()
    acc.ingest(_window("K1ABC", ["2025-06-01"], 100, snr_sum=-1000, snr_p50=-10.0))
    acc.ingest(_window("K1ABC", ["2025-06-02"], 300, snr_sum=-9000, snr_p50=-30.0))
    out = acc.station_frame(period_days=395).set_index("station")
    # Exact: total SNR / total spots = -10000 / 400
    assert out.loc["K1ABC", "snr_mean"] == pytest.approx(-25.0)
    # Weighted mean of medians: (100*-10 + 300*-30) / 400
    assert out.loc["K1ABC", "snr_p50_approx"] == pytest.approx(-25.0)


def test_partner_counts_are_reported_as_bounds_not_a_period_value():
    acc = S._StatsAccumulator()
    acc.ingest(_window("K1ABC", ["2025-06-01"], 10, n_partners=40))
    acc.ingest(_window("K1ABC", ["2025-06-02"], 10, n_partners=70))
    out = acc.station_frame(period_days=395).set_index("station")
    assert out.loc["K1ABC", "n_partners_max_window"] == 70
    assert out.loc["K1ABC", "n_partners_sum_windows"] == 110  # upper bound only


def test_duty_fraction_uses_the_period_length():
    acc = S._StatsAccumulator()
    acc.ingest(_window("K1ABC", ["2025-06-01", "2025-06-02"], 10))
    out = acc.station_frame(period_days=100).set_index("station")
    assert out.loc["K1ABC", "duty_frac"] == pytest.approx(0.02)


def test_compaction_does_not_change_the_answer():
    """Compaction runs every 8 windows; results must be identical either way."""
    days = [[f"2025-06-{day:02d}"] for day in range(1, 21)]
    acc = S._StatsAccumulator()
    for day in days:
        acc.ingest(_window("K1ABC", day, 10))
    out = acc.station_frame(period_days=395).set_index("station")
    assert out.loc["K1ABC", "n_days_active"] == 20
    assert out.loc["K1ABC", "n_spots"] == 200
    assert out.loc["K1ABC", "max_gap_days"] == 0


def test_compaction_across_many_months_does_not_inflate_totals():
    """Regression: compaction must reduce to (station, month), not (station).

    Reducing to (station) and re-attaching the month column fans each station out
    to one row per month, and the next compaction round sums those duplicated
    totals -- inflating counts by orders of magnitude. The bug is invisible over a
    single-month period, so this test spans thirteen months and forces several
    compaction rounds (which trigger every 8 ingests).
    """
    acc = S._StatsAccumulator()
    expected_spots = 0
    for month in range(1, 14):
        year, mon = (2025, month) if month <= 12 else (2026, month - 12)
        for window in range(4):  # 52 windows -> 6 compaction rounds
            day = 1 + window * 7
            acc.ingest(_window("K1ABC", [f"{year}-{mon:02d}-{day:02d}"], 10))
            expected_spots += 10
    out = acc.station_frame(period_days=395).set_index("station")
    assert out.loc["K1ABC", "n_spots"] == expected_spots == 520
    assert out.loc["K1ABC", "n_days_active"] == 52
    assert out.loc["K1ABC", "n_months_active"] == 13
    assert out.loc["K1ABC", "snr_mean"] == pytest.approx(-10.0)


def test_month_table_survives_compaction():
    """Per-month totals must stay per-month after several compaction rounds."""
    acc = S._StatsAccumulator()
    for month in range(1, 14):
        year, mon = (2025, month) if month <= 12 else (2026, month - 12)
        for window in range(4):
            day = 1 + window * 7
            acc.ingest(_window("K1ABC", [f"{year}-{mon:02d}-{day:02d}"], 10))
    months = acc.month_table()
    assert len(months) == 13
    assert (months.n_spots == 40).all()          # 4 windows x 10 spots per month
    assert months.n_spots.sum() == 520


def test_month_table_is_month_aligned():
    acc = S._StatsAccumulator()
    acc.ingest(_window("K1ABC", ["2025-06-25"], 10))
    acc.ingest(_window("K1ABC", ["2025-07-01"], 20))
    months = acc.month_table().set_index("month")
    assert months.loc["2025-06", "n_spots"] == 10
    assert months.loc["2025-07", "n_spots"] == 20


# ---------------------------------------------------------------- modal values


def test_modal_grid_is_weighted_by_spots_not_by_row_count():
    detail = pd.DataFrame(
        {
            "station": ["K1ABC"] * 3,
            "grid": ["FN20", "FN20", "EM73"],
            "n_spots": [10, 10, 5000],
        }
    )
    out = S._modal_by_spots(detail, "grid").set_index("station")
    assert out.loc["K1ABC", "modal_grid"] == "EM73"
    assert out.loc["K1ABC", "n_distinct_grid"] == 2
    assert out.loc["K1ABC", "modal_grid_frac"] == pytest.approx(5000 / 5020, abs=1e-4)


# -------------------------------------------------------------- quality gates


def _tx_row(**kwargs):
    row = {
        "tx_sign": "K1ABC",
        "callsign_class": "amateur",
        "n_spots": 500,
        "n_partners_max_window": 20,
        "n_days_active": 200,
        "grid_valid": True,
        "n_distinct_grid": 1,
        "modal_grid_frac": 1.0,
        "n_distinct_power": 1,
    }
    row.update(kwargs)
    return row


def test_tier_a_for_a_well_behaved_transmitter():
    out = S.assign_tx_tier(pd.DataFrame([_tx_row()]))
    assert out.loc[0, "quality_tier"] == "A"
    assert out.loc[0, "reject_reason"] == ""


def test_telemetry_callsign_is_rejected_even_when_persistent():
    """Reject telemetry callsigns: the filter that keeps phantoms off the maps."""
    out = S.assign_tx_tier(
        pd.DataFrame([_tx_row(tx_sign="Q51MXZ", callsign_class="synthetic_telemetry")])
    )
    assert out.loc[0, "quality_tier"] == "rejected"
    assert out.loc[0, "reject_reason"] == "synthetic_telemetry_callsign"


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"n_spots": 3}, "too_few_spots"),
        ({"n_partners_max_window": 1}, "too_few_receivers"),
        ({"n_days_active": 1}, "too_few_days"),
        ({"grid_valid": False}, "undecodable_grid"),
    ],
)
def test_minimum_bar_rejections(override, reason):
    out = S.assign_tx_tier(pd.DataFrame([_tx_row(**override)]))
    assert out.loc[0, "quality_tier"] == "rejected"
    assert out.loc[0, "reject_reason"] == reason


def test_wandering_grid_demotes_to_tier_c():
    out = S.assign_tx_tier(pd.DataFrame([_tx_row(n_distinct_grid=12)]))
    assert out.loc[0, "quality_tier"] == "C"


def test_tier_b_when_persistent_but_not_pristine():
    out = S.assign_tx_tier(pd.DataFrame([_tx_row(n_days_active=4, modal_grid_frac=0.6)]))
    assert out.loc[0, "quality_tier"] == "B"


def test_nothing_is_dropped_by_the_gate():
    frame = pd.DataFrame(
        [_tx_row(), _tx_row(tx_sign="Q1AAA", callsign_class="synthetic_telemetry")]
    )
    out = S.assign_tx_tier(frame)
    assert len(out) == len(frame)


def test_rx_tiers_track_coverage_and_never_reject():
    frame = pd.DataFrame(
        {
            "rx_sign": ["A", "B", "C", "D"],
            "n_days_active": [300, 40, 10, 2],
            "grid_valid": [True, True, True, True],
            "n_distinct_grid": [1, 1, 1, 1],
        }
    )
    out = S.assign_rx_tier(frame, period_days=395)
    assert list(out["quality_tier"]) == ["A", "B", "C", "D"]
    assert not out["geo_suspect"].any()


def test_rx_geo_suspect_flag_is_independent_of_tier():
    frame = pd.DataFrame(
        {
            "rx_sign": ["A", "B"],
            "n_days_active": [300, 300],
            "grid_valid": [False, True],
            "n_distinct_grid": [1, 9],
        }
    )
    out = S.assign_rx_tier(frame, period_days=395)
    assert list(out["quality_tier"]) == ["A", "A"]
    assert list(out["geo_suspect"]) == [True, True]


def test_safe_div_handles_zero_and_missing():
    result = S._safe_div(pd.Series([1.0, 1.0, np.nan]), pd.Series([2.0, 0.0, 5.0]))
    assert result.iloc[0] == pytest.approx(0.5)
    assert pd.isna(result.iloc[1])
    assert pd.isna(result.iloc[2])


# -------------------------------------------------- callsign-case normalisation


def test_station_keys_are_upper_cased():
    """wsprdaemon.spots stores w7wkr-2 lower case; wspr.rx stores W7WKR-2."""
    series = pd.Series(["w7wkr-2", "W7WKR-2", " vy0erc "])
    assert list(S.normalise_station(series)) == ["W7WKR-2", "W7WKR-2", "VY0ERC"]


def test_case_variants_merge_into_one_station():
    acc = S._StatsAccumulator()
    acc.ingest(_window("w7wkr-2", ["2025-06-01"], 100))
    acc.ingest(_window("W7WKR-2", ["2025-06-02"], 200))
    out = acc.station_frame(period_days=395)
    assert len(out) == 1
    assert out.loc[0, "station"] == "W7WKR-2"
    assert out.loc[0, "n_spots"] == 300
    assert out.loc[0, "n_days_active"] == 2


def test_combined_activity_covers_a_station_missing_from_one_table():
    """A receiver present only in wsprdaemon.spots must still get a day count."""
    net = S._StatsAccumulator()
    net.ingest(_window("K1ABC", ["2025-06-01", "2025-06-02"], 10))
    wd = S._StatsAccumulator()
    wd.ingest(_window("WDONLY", ["2025-06-01", "2025-06-05"], 10))

    frame = pd.DataFrame({"rx_sign": ["K1ABC", "WDONLY"]})
    out = S._apply_combined_activity(frame, "rx_sign", [net, wd], period_days=100)
    out = out.set_index("rx_sign")
    assert out.loc["K1ABC", "n_days_active"] == 2
    assert out.loc["WDONLY", "n_days_active"] == 2  # not NaN
    assert out.loc["WDONLY", "max_gap_days"] == 3
    assert out.loc["WDONLY", "duty_frac"] == pytest.approx(0.02)


def test_combined_activity_unions_days_across_tables():
    net = S._StatsAccumulator()
    net.ingest(_window("K1ABC", ["2025-06-01"], 10))
    wd = S._StatsAccumulator()
    wd.ingest(_window("K1ABC", ["2025-06-02", "2025-06-03"], 10))
    out = S._apply_combined_activity(
        pd.DataFrame({"rx_sign": ["K1ABC"]}), "rx_sign", [net, wd], period_days=100
    )
    assert out.loc[0, "n_days_active"] == 3
