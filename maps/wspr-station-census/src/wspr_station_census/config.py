"""Endpoint, study period, chunking policy and on-disk cache layout.

Chunking policy
---------------
The WsprDaemon servers sit behind an nginx reverse proxy whose read timeout was
measured at approximately **10 seconds** (2026-07-28). A single-month
``GROUP BY rx_sign`` over ``wspr.rx`` took 8.5 s — inside the wall, but with no
margin — while the same query over a 10-day window took 1.4 s. Every query is
therefore issued over a bounded window of at most :attr:`CensusConfig.stats_chunk_days`
days, and windows are additionally **clipped to month boundaries** so that
station-month rollups can be computed exactly from the cached chunks.

If a window still times out, :func:`wspr_station_census.client.fetch_window`
bisects it and retries.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

#: ClickHouse HTTP endpoint. Plain HTTP on port 80, GET only; ``https://`` and
#: port 8123 do not answer. ``wd10`` is the mirror the WsprDaemon team asks
#: users to prefer so that they can manage load.
WD_ENDPOINT = "http://wd10.wsprdaemon.org/"

#: Study period for the polar-PSWS transmitter/receiver census: 2025-06-01
#: through 2026-06-30 inclusive. ``END`` is *exclusive*, matching the SQL
#: half-open ``time >= START AND time < END`` convention used throughout.
DEFAULT_START = "2025-06-01"
DEFAULT_END = "2026-07-01"

#: Environment variable naming the local data cache. The parent repository
#: deliberately does not track bulk data, so nothing written by this package
#: should land inside the repository.
ENV_DATA_DIR = "POLAR_PSWS_DATA_DIR"

#: Fallback cache root when :data:`ENV_DATA_DIR` is unset.
DEFAULT_DATA_DIR = Path.home() / "polar_psws_data"


def _parse_day(value: str | date | datetime) -> date:
    """Coerce a date-like value to a :class:`datetime.date`.

    Parameters
    ----------
    value : str or datetime.date or datetime.datetime
        A ``YYYY-MM-DD`` string or date-like object.

    Returns
    -------
    datetime.date
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(value, "%Y-%m-%d").date()


def _next_month(day: date) -> date:
    """Return the first day of the month following ``day``."""
    return date(day.year + (day.month == 12), (day.month % 12) + 1, 1)


@dataclass(frozen=True)
class CensusConfig:
    """Everything needed to reproduce a census run.

    Parameters
    ----------
    start, end : str
        Half-open study period as ``YYYY-MM-DD``; ``end`` is exclusive.
    data_dir : pathlib.Path
        Cache and output root. Defaults to ``$POLAR_PSWS_DATA_DIR`` or
        ``~/polar_psws_data``.
    endpoint : str
        ClickHouse HTTP endpoint.
    stats_chunk_days : int
        Maximum window length, in days, for the distinct-count-heavy "stats"
        queries. Windows are also clipped to month boundaries.
    detail_chunk_days : int
        Maximum window length for the additive-only "detail" queries. These are
        cheaper per day, but the transmitter variant produces ~160,000 rows per
        month and measured 10.3 s over a full month — right on the proxy wall —
        so the default splits each month in two.
    timeout_s : float
        Per-request client timeout. The server-side nginx wall (~10 s) bites
        first; a larger client timeout only buys a clearer error message.
    pace_s : float
        Minimum interval between requests. These are volunteer-run servers.
    max_retries : int
        Retries per request for transient failures.
    """

    start: str = DEFAULT_START
    end: str = DEFAULT_END
    data_dir: Path = None  # type: ignore[assignment]  # resolved in __post_init__
    endpoint: str = WD_ENDPOINT
    stats_chunk_days: int = 10
    detail_chunk_days: int = 15
    timeout_s: float = 60.0
    pace_s: float = 0.5
    max_retries: int = 3

    def __post_init__(self) -> None:
        """Resolve the data directory from the environment when not supplied."""
        if self.data_dir is None:
            env = os.environ.get(ENV_DATA_DIR)
            resolved = Path(env).expanduser() if env else DEFAULT_DATA_DIR
            object.__setattr__(self, "data_dir", resolved)
        else:
            object.__setattr__(self, "data_dir", Path(self.data_dir).expanduser())

    # ------------------------------------------------------------------ paths

    @property
    def period_slug(self) -> str:
        """Filesystem-safe label for the study period, e.g. ``2025-06-01_2026-07-01``."""
        return f"{self.start}_{self.end}"

    @property
    def root(self) -> Path:
        """Root directory for this census run."""
        return self.data_dir / "wspr_station_census" / self.period_slug

    @property
    def cache_dir(self) -> Path:
        """Directory holding the raw, gzipped server responses."""
        return self.root / "cache"

    @property
    def output_dir(self) -> Path:
        """Directory holding the summary CSVs and the manifest."""
        return self.root / "summary"

    # ----------------------------------------------------------------- windows

    @property
    def start_date(self) -> date:
        """Inclusive first day of the study period."""
        return _parse_day(self.start)

    @property
    def end_date(self) -> date:
        """Exclusive last day of the study period."""
        return _parse_day(self.end)

    def months(self) -> list[tuple[date, date]]:
        """Return the study period as half-open, month-aligned windows.

        Returns
        -------
        list of (datetime.date, datetime.date)
            ``(lo, hi)`` pairs with ``lo`` on the first of a month, except
            possibly the first window if ``start`` is mid-month.
        """
        out: list[tuple[date, date]] = []
        lo = self.start_date
        while lo < self.end_date:
            hi = min(_next_month(lo), self.end_date)
            out.append((lo, hi))
            lo = hi
        return out

    def _split_months(self, max_days: int) -> list[tuple[date, date]]:
        """Split each month into equal-ish windows of at most ``max_days`` days.

        Clipping to month boundaries is what makes station-month rollups exact:
        no cached window ever straddles two months, so per-month sums of additive
        metrics and per-month unions of day sets are both well defined.
        """
        out: list[tuple[date, date]] = []
        for m_lo, m_hi in self.months():
            span = (m_hi - m_lo).days
            # Fewest equal-ish pieces respecting the cap, so a 31-day month
            # becomes 8+8+8+7 rather than 10+10+10+1.
            n_pieces = max(1, -(-span // max_days))
            base, extra = divmod(span, n_pieces)
            lo = m_lo
            for piece in range(n_pieces):
                width = base + (1 if piece < extra else 0)
                hi = lo + timedelta(days=width)
                out.append((lo, hi))
                lo = hi
        return out

    def stats_windows(self) -> list[tuple[date, date]]:
        """Return month-clipped windows for the distinct-count-heavy queries."""
        return self._split_months(self.stats_chunk_days)

    def detail_windows(self) -> list[tuple[date, date]]:
        """Return month-clipped windows for the additive-only detail queries."""
        return self._split_months(self.detail_chunk_days)
