"""Extraction: issue the census queries and cache the raw server responses.

Extraction is separated from summarisation on purpose. The queries are the only
part that touches a volunteer-run server, they take on the order of twenty
minutes for a thirteen-month period, and the summarisation logic is the part
most likely to need revision. Caching the raw responses means every later change
to the rollup rules, the quality gates or the output columns costs zero queries.

The cache is keyed by ``(query family, window)`` and stores the literal gzipped
response body, so a run is resumable after an interruption and the record of
what the database actually returned is auditable.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from . import queries as q
from .client import (
    ClickHouseHTTP,
    cache_path,
    fetch_window,
    read_cache,
    write_cache,
)
from .config import CensusConfig

LOGGER = logging.getLogger(__name__)

#: Transmitter pre-gate applied *server-side* per stats window.
#:
#: Motivation, measured on ``wspr.rx`` for June 2026: the unfiltered month holds
#: 166,977 distinct ``tx_sign`` values, of which 88,367 have at least two spots
#: heard by at least two receivers. The discarded remainder is single-decode
#: noise that cannot pass any downstream persistence gate, so dropping it
#: server-side halves the transferred volume while changing no result. The
#: :func:`~wspr_station_census.queries.population_sql` family records what was
#: dropped, so the manifest can report it.
TX_PREGATE_MIN_SPOTS = 2
TX_PREGATE_MIN_PARTNERS = 2

#: ``HAVING`` floor for the transmitter detail family, same reasoning.
TX_DETAIL_MIN_SPOTS = 2

#: ``HAVING`` floor for the WsprDaemon transmitter presence family.
WD_MEMBERSHIP_MIN_SPOTS = 2


@dataclass(frozen=True)
class QueryFamily:
    """One cacheable group of queries: a name, a window list, and a SQL builder.

    Parameters
    ----------
    name : str
        Cache subdirectory, e.g. ``stats_tx_wsprnet``.
    build : callable
        ``build(lo, hi) -> str``.
    windows : list of (datetime.date, datetime.date)
        Windows to cover.
    description : str
        One line for logs and the manifest.
    """

    name: str
    build: Callable[[date, date], str]
    windows: list[tuple[date, date]] = field(repr=False)
    description: str = ""


def build_families(config: CensusConfig) -> list[QueryFamily]:
    """Assemble the full query plan for a census run.

    The plan is deliberately asymmetric between the two tables and the two roles,
    because their information content is asymmetric:

    * **Receivers** are characterised from *both* tables. ``wspr.rx`` gives the
      full ~5,000-receiver population plus the ``version`` string; the WsprDaemon
      table adds the receiver-instance inventory and the noise columns, and
      membership in it is itself the answer to "does this site report extended
      spots?".
    * **Transmitters** are characterised from ``wspr.rx`` only, which has ~20×
      the receiver coverage and therefore hears far more transmitters. The
      WsprDaemon table contributes only a presence flag.

    Parameters
    ----------
    config : CensusConfig

    Returns
    -------
    list of QueryFamily
    """
    stats_w = config.stats_windows()
    detail_w = config.detail_windows()
    months = config.months()

    def stats(spec: q.TableSpec, role: q.Role, **kwargs: int) -> QueryFamily:
        return QueryFamily(
            name=q.family_name("stats", spec, role),
            build=lambda lo, hi: q.stats_sql(spec, role, lo, hi, **kwargs),
            windows=stats_w,
            description=f"per-{role.name} statistics from {spec.table}",
        )

    def detail(spec: q.TableSpec, role: q.Role, **kwargs: int) -> QueryFamily:
        return QueryFamily(
            name=q.family_name("detail", spec, role),
            build=lambda lo, hi: q.detail_sql(spec, role, lo, hi, **kwargs),
            windows=detail_w,
            description=f"per-{role.name} band/grid detail from {spec.table}",
        )

    families = [
        stats(q.WSPRNET, q.RX),
        stats(
            q.WSPRNET,
            q.TX,
            min_spots=TX_PREGATE_MIN_SPOTS,
            min_partners=TX_PREGATE_MIN_PARTNERS,
        ),
        stats(q.WSPRDAEMON, q.RX),
        detail(q.WSPRNET, q.RX),
        detail(q.WSPRNET, q.TX, min_spots=TX_DETAIL_MIN_SPOTS),
        detail(q.WSPRDAEMON, q.RX),
        QueryFamily(
            name=q.family_name("membership", q.WSPRDAEMON, q.TX),
            build=lambda lo, hi: q.membership_sql(
                q.WSPRDAEMON, q.TX, lo, hi, min_spots=WD_MEMBERSHIP_MIN_SPOTS
            ),
            windows=detail_w,
            description="transmitter presence in wsprdaemon.spots",
        ),
    ]
    for spec in (q.WSPRNET, q.WSPRDAEMON):
        for role in (q.RX, q.TX):
            families.append(
                QueryFamily(
                    name=q.family_name("population", spec, role),
                    build=lambda lo, hi, s=spec, r=role: q.population_sql(s, r, lo, hi),
                    windows=months,
                    description=f"unfiltered {role.name} population in {spec.table}",
                )
            )
    return families


def extract(
    config: CensusConfig,
    client: ClickHouseHTTP | None = None,
    only: list[str] | None = None,
    refresh: bool = False,
) -> dict[str, int]:
    """Run the query plan, writing raw responses into the cache.

    Parameters
    ----------
    config : CensusConfig
        Period, endpoint and chunking policy.
    client : ClickHouseHTTP, optional
        Pre-built client. One is constructed from ``config`` if omitted.
    only : list of str, optional
        Restrict to these family names (exact match). Useful when a single
        family needs re-extraction.
    refresh : bool, optional
        Re-query windows that are already cached. Off by default, which is what
        makes an interrupted run resumable.

    Returns
    -------
    dict
        ``{'queried': int, 'cached': int, 'requests': int, 'seconds': float}``
        summarising the run.
    """
    client = client or ClickHouseHTTP(
        endpoint=config.endpoint,
        timeout_s=config.timeout_s,
        pace_s=config.pace_s,
        max_retries=config.max_retries,
    )
    families = build_families(config)
    if only:
        wanted = set(only)
        families = [fam for fam in families if fam.name in wanted]
        missing = wanted - {fam.name for fam in families}
        if missing:
            raise ValueError(f"unknown query families: {sorted(missing)}")

    total_windows = sum(len(fam.windows) for fam in families)
    LOGGER.info(
        "census extraction: %d families, %d windows, %s .. %s",
        len(families),
        total_windows,
        config.start,
        config.end,
    )

    n_queried = 0
    n_cached = 0
    done = 0
    for fam in families:
        for lo, hi in fam.windows:
            done += 1
            path = cache_path(config.cache_dir, fam.name, lo, hi)
            if path.exists() and not refresh:
                n_cached += 1
                continue
            # fetch_window bisects on a 504, so a window that is too wide for
            # the proxy still completes -- as several narrower responses.
            frames = fetch_window(client, fam.build, lo, hi)
            body = _reserialise(frames)
            write_cache(path, body)
            n_queried += 1
            LOGGER.info(
                "[%d/%d] %-28s %s..%s  %6d rows",
                done,
                total_windows,
                fam.name,
                lo,
                hi,
                sum(len(frame) for frame in frames),
            )

    LOGGER.info(
        "extraction done: %d queried, %d already cached, %d requests, %.0f s server time",
        n_queried,
        n_cached,
        client.n_requests,
        client.seconds_spent,
    )
    return {
        "queried": n_queried,
        "cached": n_cached,
        "requests": client.n_requests,
        "seconds": round(client.seconds_spent, 1),
    }


def _reserialise(frames: list[pd.DataFrame]) -> str:
    """Render one or more result frames back into the cache's on-disk format.

    A window that had to be bisected comes back as several frames; concatenating
    them here keeps one cache file per requested window, so the cache layout
    stays a clean function of the query plan regardless of how the server
    behaved on the day.
    """
    import json

    combined = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
    lines = [json.dumps(list(combined.columns))]
    lines += [json.dumps(row, default=str) for row in combined.to_dict(orient="split")["data"]]
    return "\n".join(lines) + "\n"


def load_family(config: CensusConfig, family: str) -> pd.DataFrame:
    """Load every cached window of one family into a single frame.

    A ``window_start`` column is added, carrying the first day of the window the
    rows came from. Because windows never straddle a month, ``window_start``
    determines the month unambiguously, which is what the station-month rollups
    rely on.

    Parameters
    ----------
    config : CensusConfig
    family : str
        Family name, e.g. ``stats_rx_wsprnet``.

    Returns
    -------
    pandas.DataFrame
        Empty if nothing is cached for that family.
    """
    frames = []
    for path in sorted(_family_files(config, family)):
        frame = read_cache(path)
        if frame.empty:
            continue
        if "station" in frame.columns:
            # Callsigns are case-insensitive but the two tables disagree on case;
            # see wspr_station_census.summarize.normalise_station.
            frame["station"] = frame["station"].astype("string").str.strip().str.upper()
        window_start = path.name.split("_")[0]
        frame["window_start"] = pd.to_datetime(window_start, format="%Y%m%d")
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _family_files(config: CensusConfig, family: str) -> Iterator:
    """Yield cached response paths for one family."""
    directory = config.cache_dir / family
    if not directory.is_dir():
        return iter(())
    return directory.glob("*.jsonl.gz")


def cache_report(config: CensusConfig) -> pd.DataFrame:
    """Summarise cache completeness per family, for ``wspr-census status``.

    Returns
    -------
    pandas.DataFrame
        Columns ``family``, ``expected``, ``cached``, ``bytes``, ``complete``.
    """
    rows = []
    for fam in build_families(config):
        paths = [
            cache_path(config.cache_dir, fam.name, lo, hi) for lo, hi in fam.windows
        ]
        present = [path for path in paths if path.exists()]
        rows.append(
            {
                "family": fam.name,
                "expected": len(paths),
                "cached": len(present),
                "bytes": sum(path.stat().st_size for path in present),
                "complete": len(present) == len(paths),
            }
        )
    return pd.DataFrame(rows)
