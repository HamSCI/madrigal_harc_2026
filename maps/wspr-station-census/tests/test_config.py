"""Window generation. The month-alignment property is load-bearing."""

from __future__ import annotations

from datetime import date

from wspr_station_census.config import CensusConfig


def _config(**kwargs) -> CensusConfig:
    return CensusConfig(data_dir="/tmp/wspr-census-test", **kwargs)


def test_months_cover_the_period_exactly():
    config = _config(start="2025-06-01", end="2026-07-01")
    months = config.months()
    assert len(months) == 13
    assert months[0] == (date(2025, 6, 1), date(2025, 7, 1))
    assert months[-1] == (date(2026, 6, 1), date(2026, 7, 1))
    for (_, hi), (lo, _) in zip(months, months[1:], strict=False):
        assert hi == lo  # contiguous, no gaps or overlaps


def test_stats_windows_never_straddle_a_month():
    """Station-month rollups are only exact because of this property."""
    config = _config(start="2025-06-01", end="2026-07-01", stats_chunk_days=10)
    for lo, hi in config.stats_windows():
        assert (lo.year, lo.month) == ((hi - date.resolution).year, (hi - date.resolution).month)


def test_stats_windows_respect_the_cap_and_are_contiguous():
    config = _config(start="2025-06-01", end="2026-07-01", stats_chunk_days=10)
    windows = config.stats_windows()
    assert all((hi - lo).days <= 10 for lo, hi in windows)
    assert windows[0][0] == date(2025, 6, 1)
    assert windows[-1][1] == date(2026, 7, 1)
    for (_, hi), (lo, _) in zip(windows, windows[1:], strict=False):
        assert hi == lo


def test_windows_are_split_evenly_not_with_a_ragged_tail():
    """A 31-day month becomes 8+8+8+7, not 10+10+10+1: no wasted round trips."""
    config = _config(start="2025-07-01", end="2025-08-01", stats_chunk_days=10)
    widths = [(hi - lo).days for lo, hi in config.stats_windows()]
    assert widths == [8, 8, 8, 7]
    assert sum(widths) == 31


def test_detail_windows_are_coarser_than_stats_windows():
    config = _config(start="2025-06-01", end="2026-07-01")
    assert len(config.detail_windows()) < len(config.stats_windows())


def test_partial_first_month():
    config = _config(start="2025-06-15", end="2025-08-01")
    months = config.months()
    assert months[0] == (date(2025, 6, 15), date(2025, 7, 1))
    assert sum((hi - lo).days for lo, hi in config.stats_windows()) == 47


def test_paths_are_period_scoped():
    config = _config(start="2025-06-01", end="2026-07-01")
    assert config.period_slug == "2025-06-01_2026-07-01"
    assert config.cache_dir.name == "cache"
    assert config.output_dir.name == "summary"
    assert config.period_slug in str(config.cache_dir)
