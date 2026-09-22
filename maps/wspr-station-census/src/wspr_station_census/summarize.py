"""Roll cached query results up into the station-level summary tables.

Additivity is the whole problem
-------------------------------
Extraction returns one row per station per *window*. Turning that into one row
per station per *period* is only trivial for additive quantities. This module is
organised around which is which:

**Exactly additive across windows** — spot counts, distinct 2-minute slot counts
(a slot belongs to exactly one window), sums of SNR and distance, invalid-geometry
counts, overload counts.

**Exact via set union** — active days, bands, grid squares, software versions,
receiver instances, TX powers. The ``stats`` queries return these as
``groupUniqArray`` *sets* rather than counts precisely so that the period-level
answer is exact rather than an upper bound. Day sets additionally give the exact
longest activity gap.

**Not recoverable from windowed results** — distinct *partner* counts. The number
of transmitters a receiver heard over thirteen months is not the sum, nor the
maximum, of the monthly figures. Computing it exactly would need one unbounded
``uniqExact`` over 2.8 billion rows, which the proxy will not allow. Two honest
bounds are reported instead: ``n_partners_max_window`` (exact for its window) and
``n_partners_sum_windows`` (an upper bound on the period figure). Neither is
labelled as the period value.

**Approximate by construction** — medians. ``quantileTDigest`` is used
server-side to keep memory bounded on a volunteer-run host, and the period figure
is a spot-count-weighted mean of the per-window medians. Means, minima and maxima
are exact, so use those when precision matters; the ``_p50``/``_p90`` columns are
for ranking and eyeballing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import bands as band_mod
from . import callsigns, geo
from .config import CensusConfig
from .extract import load_family

LOGGER = logging.getLogger(__name__)

#: Columns summed across windows.
_SUM_COLS = (
    "n_spots",
    "n_slots",
    "n_partners_sum_windows",
    "snr_sum",
    "n_dist_ok",
    "dist_sum",
    "n_geo_invalid",
    "n_noise_ok",
    "n_ov_nonzero",
    "snr_p50_w",
    "dist_p50_w",
    "dist_p90_w",
    "rms_noise_w",
    "c2_noise_w",
    "noise_disagree_w",
)

#: Columns reduced with ``max`` across windows.
_MAX_COLS = ("n_partners_max_window", "snr_max", "dist_max", "n_grids_window")

#: Columns reduced with ``min`` across windows.
_MIN_COLS = ("snr_min",)

#: Set-valued columns, unioned across windows. Maps the source array column to
#: the name of the long (station, value) table built from it.
_SET_COLS = {
    "days": "day",
    "bands_raw": "band_raw",
    "grids": "grid",
    "versions": "version",
    "rx_ids": "rx_id",
    "powers": "power",
}


def normalise_station(series: pd.Series) -> pd.Series:
    """Upper-case a station-key column.

    Callsigns are case-insensitive, but the two tables do not agree on case:
    ``wsprdaemon.spots`` stores ``w7wkr-2`` and ``w7wkr-k1`` in lower case
    (865,034 rows in June 2026) while ``wspr.rx`` stores the same receivers as
    ``W7WKR-2`` and ``W7WKR-K1``, and has no lower-case callsigns at all.
    Without normalisation those receivers are reported as present in only one
    table — which is exactly the signal the ``source_class`` column is supposed
    to carry, so the bug would masquerade as a finding.
    """
    return series.astype("string").str.strip().str.upper()


class _StatsAccumulator:
    """Incrementally reduce ``stats`` windows to one row per station.

    Windows are ingested one file at a time and compacted as they go, so peak
    memory stays close to the size of the *result* rather than the size of the
    whole family. That matters for the transmitter family, which is roughly 1.4
    million station-window rows over a thirteen-month period.
    """

    def __init__(self) -> None:
        self._numeric: list[pd.DataFrame] = []
        self._sets: dict[str, list[pd.DataFrame]] = {name: [] for name in _SET_COLS}
        self._n_windows = 0

    def ingest(self, frame: pd.DataFrame) -> None:
        """Add one window's results."""
        if frame.empty:
            return
        self._n_windows += 1
        work = frame.copy()
        work["station"] = normalise_station(work["station"])

        # Weighted numerators for the median-of-medians, so the final division is
        # a single vectorised step.
        work["snr_p50_w"] = _num(work.get("snr_p50")) * _num(work["n_spots"])
        work["dist_p50_w"] = _num(work.get("dist_p50")) * _num(work.get("n_dist_ok"))
        work["dist_p90_w"] = _num(work.get("dist_p90")) * _num(work.get("n_dist_ok"))
        work["rms_noise_w"] = _num(work.get("rms_noise_mean")) * _num(work.get("n_noise_ok"))
        work["c2_noise_w"] = _num(work.get("c2_noise_mean")) * _num(work.get("n_noise_ok"))
        work["noise_disagree_w"] = _num(work.get("noise_disagree_mean")) * _num(
            work.get("n_noise_ok")
        )
        work["n_partners_max_window"] = _num(work.get("n_partners"))
        work["n_partners_sum_windows"] = _num(work.get("n_partners"))
        work["n_grids_window"] = _num(work.get("n_grids"))

        for column, value_name in _SET_COLS.items():
            if column not in work.columns:
                continue
            long = (
                work[["station", column]]
                .explode(column)
                .rename(columns={column: value_name})
                .dropna()
                .drop_duplicates()
            )
            if not long.empty:
                self._sets[column].append(long)

        numeric = pd.DataFrame({"station": work["station"]})
        for column in _SUM_COLS + _MAX_COLS + _MIN_COLS:
            if column in work.columns:
                numeric[column] = _num(work[column])
        numeric["first_spot"] = pd.to_datetime(work["first_spot"], errors="coerce")
        numeric["last_spot"] = pd.to_datetime(work["last_spot"], errors="coerce")
        numeric["month"] = pd.to_datetime(work["window_start"]).dt.to_period("M")
        self._numeric.append(numeric)
        self._compact()

    def _compact(self, force: bool = False) -> None:
        """Fold accumulated pieces down, bounding memory growth.

        Compaction reduces to **(station, month)**, not to (station). Two windows
        of the same month collapse into one row, so the operation is idempotent
        and can run any number of times without changing the totals — while the
        month granularity needed by :meth:`month_table` survives.

        Reducing to (station) here instead would be a correctness bug, not just a
        loss of detail: the month column would have to be re-attached, fanning
        each station out to one row per month, and the next compaction round
        would then sum those duplicated totals. The inflation compounds with
        every round and is invisible over a single-month period.
        """
        if not force and len(self._numeric) < 8:
            return
        self._numeric = [
            _reduce_numeric(pd.concat(self._numeric, ignore_index=True), ["station", "month"])
        ]
        for column, pieces in self._sets.items():
            if len(pieces) > 1:
                self._sets[column] = [
                    pd.concat(pieces, ignore_index=True).drop_duplicates()
                ]

    # ------------------------------------------------------------------ output

    def station_frame(self, period_days: int) -> pd.DataFrame:
        """Return one row per station with period-level metrics."""
        if not self._numeric:
            return pd.DataFrame()
        self._compact(force=True)
        out = _reduce_numeric(self._numeric[0], ["station"]).set_index("station")

        day_stats = _day_statistics(self._sets["days"])
        out = out.join(day_stats, how="left")
        out["duty_frac"] = (out["n_days_active"] / period_days).round(4)

        out["n_geo_invalid_frac"] = _safe_div(out["n_geo_invalid"], out["n_spots"]).round(5)
        out["snr_mean"] = _safe_div(out["snr_sum"], out["n_spots"]).round(2)
        out["snr_p50_approx"] = _safe_div(out["snr_p50_w"], out["n_spots"]).round(2)
        out["dist_mean_km"] = _safe_div(out["dist_sum"], out["n_dist_ok"]).round(1)
        out["dist_p50_approx_km"] = _safe_div(out["dist_p50_w"], out["n_dist_ok"]).round(1)
        out["dist_p90_approx_km"] = _safe_div(out["dist_p90_w"], out["n_dist_ok"]).round(1)

        if "n_noise_ok" in out.columns:
            out["noise_valid_frac"] = _safe_div(out["n_noise_ok"], out["n_spots"]).round(4)
            out["rms_noise_mean"] = _safe_div(out["rms_noise_w"], out["n_noise_ok"]).round(2)
            out["c2_noise_mean"] = _safe_div(out["c2_noise_w"], out["n_noise_ok"]).round(2)
            out["noise_disagree_mean"] = _safe_div(
                out["noise_disagree_w"], out["n_noise_ok"]
            ).round(2)
            out["ov_frac"] = _safe_div(out["n_ov_nonzero"], out["n_spots"]).round(4)

        out = out.drop(columns=[col for col in out.columns if col.endswith("_w")])
        return out.reset_index()

    def set_table(self, column: str) -> pd.DataFrame:
        """Return the deduplicated long (station, value) table for one set column."""
        pieces = self._sets.get(column) or []
        if not pieces:
            return pd.DataFrame(columns=["station", _SET_COLS[column]])
        self._compact(force=True)
        return self._sets[column][0]

    def month_table(self) -> pd.DataFrame:
        """Return additive per-station-per-month metrics.

        Exact because query windows never straddle a month boundary, so every
        window contributes to exactly one month.
        """
        if not self._numeric:
            return pd.DataFrame()
        self._compact(force=True)
        frame = self._numeric[0]
        if "month" not in frame.columns:
            return pd.DataFrame()
        keep = ["station", "month", "n_spots", "n_slots", "n_partners_max_window"]
        out = frame[[col for col in keep if col in frame.columns]].copy()
        out["month"] = out["month"].astype(str)
        return out


def _num(series: pd.Series | None) -> pd.Series | float:
    """Coerce to numeric, mapping nulls to NaN. Returns NaN for a missing column."""
    if series is None:
        return np.nan
    return pd.to_numeric(series, errors="coerce")


def _pick(frame: pd.DataFrame, *names: str, default: float = np.nan) -> pd.Series:
    """Return the first present column among ``names``, coerced to numeric.

    The station tables carry table-suffixed metric names (``n_spots_wsprnet``)
    while the gate functions are also used directly on unsuffixed frames in
    tests and in exploratory work. Accepting both keeps one implementation.
    """
    for name in names:
        if name in frame.columns:
            return pd.to_numeric(frame[name], errors="coerce")
    return pd.Series(default, index=frame.index, dtype="float64")


def _safe_div(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """Element-wise division with zero and NaN denominators yielding NaN."""
    denom = pd.to_numeric(denominator, errors="coerce").replace(0, np.nan)
    return pd.to_numeric(numerator, errors="coerce") / denom


def _reduce_numeric(frame: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Group a numeric frame down to one row per ``keys`` combination.

    Parameters
    ----------
    frame : pandas.DataFrame
        Station-window (or station-month) numeric metrics.
    keys : list of str
        ``["station", "month"]`` during compaction, which is idempotent, or
        ``["station"]`` for the final period-level reduction.

    Returns
    -------
    pandas.DataFrame
        Sums for additive columns, extrema for the rest. When reducing to
        ``["station"]``, ``n_months_active`` is added from the month column.
    """
    agg: dict[str, tuple[str, str]] = {}
    for column in _SUM_COLS:
        if column in frame.columns:
            agg[column] = (column, "sum")
    for column in _MAX_COLS:
        if column in frame.columns:
            agg[column] = (column, "max")
    for column in _MIN_COLS:
        if column in frame.columns:
            agg[column] = (column, "min")
    agg["first_spot"] = ("first_spot", "min")
    agg["last_spot"] = ("last_spot", "max")
    if "month" not in keys and "month" in frame.columns:
        agg["n_months_active"] = ("month", "nunique")
    return frame.groupby(keys, as_index=False).agg(**agg)


def _day_statistics(pieces: list[pd.DataFrame]) -> pd.DataFrame:
    """Compute exact active-day counts and the longest activity gap.

    Parameters
    ----------
    pieces : list of pandas.DataFrame
        Long ``(station, day)`` tables from :class:`_StatsAccumulator`.

    Returns
    -------
    pandas.DataFrame
        Indexed by ``station``, with ``n_days_active``, ``max_gap_days`` and
        ``first_day`` / ``last_day``. ``max_gap_days`` is the number of
        *consecutive silent days* in the largest interior gap; 0 means the
        station was heard on every day between its first and last appearance.
    """
    if not pieces:
        return pd.DataFrame(
            columns=["n_days_active", "max_gap_days", "first_day", "last_day"]
        )
    long = pd.concat(pieces, ignore_index=True).drop_duplicates()
    long["day"] = pd.to_datetime(long["day"], errors="coerce")
    long = long.dropna(subset=["day"]).sort_values(["station", "day"])
    long["gap"] = long.groupby("station")["day"].diff().dt.days - 1.0
    stats = long.groupby("station").agg(
        n_days_active=("day", "nunique"),
        max_gap_days=("gap", "max"),
        first_day=("day", "min"),
        last_day=("day", "max"),
    )
    stats["max_gap_days"] = stats["max_gap_days"].fillna(0).astype("int64")
    return stats


# --------------------------------------------------------------------- helpers


def _join_set(table: pd.DataFrame, value: str, name: str, sort_bands: bool = False) -> pd.DataFrame:
    """Collapse a long (station, value) table to pipe-delimited strings and counts."""
    if table.empty:
        return pd.DataFrame(columns=["station", name, f"n_{name}"])
    grouped = table.groupby("station")[value].agg(list)
    if sort_bands:
        text = grouped.map(band_mod.format_band_list)
    else:
        text = grouped.map(lambda values: "|".join(sorted({str(v) for v in values})))
    out = pd.DataFrame(
        {name: text, f"n_{name}": grouped.map(lambda values: len({str(v) for v in values}))}
    )
    return out.reset_index()


def _decode_bands(table: pd.DataFrame, convention: str) -> pd.DataFrame:
    """Map a long (station, band_raw) table to canonical band labels."""
    if table.empty:
        return pd.DataFrame(columns=["station", "band"])
    out = table.copy()
    lookup = {
        raw: band_mod.decode_band(raw, convention) for raw in out["band_raw"].unique()
    }
    out["band"] = out["band_raw"].map(lookup)
    return out[["station", "band"]].drop_duplicates()


def _modal_by_spots(detail: pd.DataFrame, key: str) -> pd.DataFrame:
    """Return the spot-count-weighted modal value of ``key`` per station.

    Parameters
    ----------
    detail : pandas.DataFrame
        Must contain ``station``, ``key`` and ``n_spots``.
    key : str
        Column to take the mode of, e.g. ``grid``, ``version``, ``power``.

    Returns
    -------
    pandas.DataFrame
        Columns ``station``, ``modal_<key>``, ``modal_<key>_frac``,
        ``n_distinct_<key>``.
    """
    if detail.empty or key not in detail.columns:
        return pd.DataFrame(
            columns=["station", f"modal_{key}", f"modal_{key}_frac", f"n_distinct_{key}"]
        )
    counts = detail.groupby(["station", key], as_index=False)["n_spots"].sum()
    totals = counts.groupby("station", as_index=False)["n_spots"].sum().rename(
        columns={"n_spots": "total"}
    )
    counts = counts.sort_values(["station", "n_spots"], ascending=[True, False])
    best = counts.drop_duplicates("station")
    n_distinct = counts.groupby("station", as_index=False)[key].nunique().rename(
        columns={key: f"n_distinct_{key}"}
    )
    out = best.merge(totals, on="station").merge(n_distinct, on="station")
    out[f"modal_{key}_frac"] = _safe_div(out["n_spots"], out["total"]).round(4)
    out = out.rename(columns={key: f"modal_{key}"})
    return out[["station", f"modal_{key}", f"modal_{key}_frac", f"n_distinct_{key}"]]


def _add_position(frame: pd.DataFrame, grid_column: str) -> pd.DataFrame:
    """Add grid-derived position columns to a station frame."""
    out = frame.copy()
    unique_grids = out[grid_column].dropna().unique()
    decoded = {grid: geo.decode_grid(grid) for grid in unique_grids}
    out["lat"] = out[grid_column].map(
        lambda g: None if decoded.get(g) is None else round(decoded[g].lat, 4)
    )
    out["lon"] = out[grid_column].map(
        lambda g: None if decoded.get(g) is None else round(decoded[g].lon, 4)
    )
    out["grid_precision"] = out[grid_column].map(
        lambda g: None if decoded.get(g) is None else decoded[g].precision
    )
    out["grid_cell_lat_deg"] = out[grid_column].map(
        lambda g: None if decoded.get(g) is None else round(decoded[g].cell_lat_deg, 5)
    )
    out["grid_valid"] = out[grid_column].map(lambda g: geo.is_valid_grid(g))
    out["hemisphere"] = out["lat"].map(geo.hemisphere)
    out["abs_lat"] = pd.to_numeric(out["lat"], errors="coerce").abs().round(4)
    return out


def _add_geomagnetic(frame: pd.DataFrame, epoch: str) -> pd.DataFrame:
    """Add an AACGM-v2 geomagnetic latitude column if :mod:`aacgmv2` is installed."""
    out = frame.copy()
    if not geo.aacgm_available():
        # Emit the columns regardless, so downstream notebooks can rely on the
        # schema and simply find them empty.
        out["cgm_lat"] = np.nan
        out["abs_cgm_lat"] = np.nan
        return out
    lats = out["lat"].tolist()
    lons = out["lon"].tolist()
    out["cgm_lat"] = [
        None if value is None else round(value, 3)
        for value in geo.aacgm_latitudes(lats, lons, epoch=epoch)
    ]
    out["abs_cgm_lat"] = pd.to_numeric(out["cgm_lat"], errors="coerce").abs().round(3)
    return out


# ----------------------------------------------------------------- quality gates


@dataclass(frozen=True)
class TxGate:
    """Thresholds for the transmitter quality tiers.

    Defaults come from the population study in ``docs/COLUMNS.md``. The decisive
    threshold is ``tier_b_min_days``: requiring three distinct active days cut the
    June 2026 ``wspr.rx`` transmitter population from 53,708 to 4,629, and the
    resulting high-latitude counts from implausible (32,757 above 70° absolute
    latitude) to plausible (12).
    """

    min_spots: int = 10
    min_partners: int = 2
    min_days: int = 2
    tier_b_min_days: int = 3
    tier_b_max_grids: int = 3
    tier_a_min_days: int = 10
    tier_a_min_grid_frac: float = 0.9
    tier_a_max_powers: int = 3


def assign_tx_tier(frame: pd.DataFrame, gate: TxGate | None = None) -> pd.DataFrame:
    """Assign a quality tier and a rejection reason to each transmitter.

    Nothing is dropped. The tier is a *column*, so the notebook's filter is
    visible in the notebook and can be revised without re-running any query —
    which matters because these thresholds are judgement calls, not physics.

    Tiers
    -----
    ``A``
        Persistent, single-location, consistent power. Safe to map unconditionally.
    ``B``
        Persistent and plausibly located: a recognisable amateur or hashed
        callsign, active on at least three days, at most three grid squares.
    ``C``
        Passes the minimum bar (spots, partners, days, decodable grid) but fails
        at least one plausibility test — e.g. many grid squares, or a callsign
        shape the ITU does not allocate.
    ``rejected``
        Fails the minimum bar, or has a synthetic-telemetry callsign shape, or has
        no decodable grid. **Excluding these is strongly recommended for any
        geographic plot**; they are the population that puts thousands of phantom
        transmitters at the poles.

    Parameters
    ----------
    frame : pandas.DataFrame
        Transmitter summary with ``n_spots``, ``n_partners_max_window``,
        ``n_days_active``, ``grid_valid``, ``callsign_class``,
        ``n_distinct_grid``, ``modal_grid_frac``, ``n_distinct_power``.
    gate : TxGate, optional

    Returns
    -------
    pandas.DataFrame
        ``frame`` with ``quality_tier`` and ``reject_reason`` added.
    """
    gate = gate or TxGate()
    out = frame.copy()

    n_spots = _pick(out, "n_spots_wsprnet", "n_spots").fillna(0)
    n_partners = _pick(
        out, "n_partners_max_window_wsprnet", "n_partners_max_window"
    ).fillna(0)
    n_days = _pick(out, "n_days_active").fillna(0)
    n_grids = _pick(out, "n_distinct_grid").fillna(99)
    grid_frac = _pick(out, "modal_grid_frac").fillna(0)
    n_powers = _pick(out, "n_distinct_power").fillna(99)
    synthetic = out["callsign_class"].eq("synthetic_telemetry")
    plausible_call = out["callsign_class"].isin(["amateur", "hashed"])
    grid_ok = out["grid_valid"].fillna(False).astype(bool)

    reasons = pd.Series("", index=out.index, dtype="object")
    reasons = reasons.mask(n_spots < gate.min_spots, "too_few_spots")
    reasons = reasons.mask(n_partners < gate.min_partners, "too_few_receivers")
    reasons = reasons.mask(n_days < gate.min_days, "too_few_days")
    reasons = reasons.mask(~grid_ok, "undecodable_grid")
    reasons = reasons.mask(synthetic, "synthetic_telemetry_callsign")

    rejected = reasons.ne("")
    tier_b = (
        ~rejected
        & (n_days >= gate.tier_b_min_days)
        & (n_grids <= gate.tier_b_max_grids)
        & plausible_call
    )
    tier_a = (
        tier_b
        & (n_days >= gate.tier_a_min_days)
        & (grid_frac >= gate.tier_a_min_grid_frac)
        & (n_powers <= gate.tier_a_max_powers)
    )

    tier = pd.Series("C", index=out.index, dtype="object")
    tier = tier.mask(tier_b, "B")
    tier = tier.mask(tier_a, "A")
    tier = tier.mask(rejected, "rejected")

    out["quality_tier"] = tier
    out["reject_reason"] = reasons
    return out


def assign_rx_tier(frame: pd.DataFrame, period_days: int) -> pd.DataFrame:
    """Assign a persistence tier and a geometry-suspect flag to each receiver.

    Receivers are a far cleaner population than transmitters — they are
    registered, persistent installations — so the tiers here describe *coverage*
    rather than plausibility, and no receiver is ever rejected.

    Tiers
    -----
    ``A`` active on at least half the days in the period; ``B`` at least 30 days;
    ``C`` at least 7 days; ``D`` fewer than 7. ``geo_suspect`` is set
    independently when the grid is undecodable or the station reported more than
    three distinct grid squares.

    Parameters
    ----------
    frame : pandas.DataFrame
    period_days : int
        Length of the study period in days, for the tier-A threshold.

    Returns
    -------
    pandas.DataFrame
        ``frame`` with ``quality_tier`` and ``geo_suspect`` added.
    """
    out = frame.copy()
    n_days = _pick(out, "n_days_active").fillna(0)
    tier = pd.Series("D", index=out.index, dtype="object")
    tier = tier.mask(n_days >= 7, "C")
    tier = tier.mask(n_days >= 30, "B")
    tier = tier.mask(n_days >= 0.5 * period_days, "A")
    out["quality_tier"] = tier
    n_grids = _pick(out, "n_distinct_grid").fillna(99)
    out["geo_suspect"] = (~out["grid_valid"].fillna(False).astype(bool)) | (n_grids > 3)
    return out


# ------------------------------------------------------------------- the tables


@dataclass
class CensusTables:
    """The complete set of summary tables produced by :func:`summarize`."""

    rx_stations: pd.DataFrame
    tx_stations: pd.DataFrame
    rx_station_band: pd.DataFrame
    tx_station_band: pd.DataFrame
    rx_station_month: pd.DataFrame
    tx_station_month: pd.DataFrame
    wd_receivers: pd.DataFrame
    population: pd.DataFrame

    def as_dict(self) -> dict[str, pd.DataFrame]:
        """Return ``{output_name: frame}`` for writing."""
        return {
            "rx_stations": self.rx_stations,
            "tx_stations": self.tx_stations,
            "rx_station_band": self.rx_station_band,
            "tx_station_band": self.tx_station_band,
            "rx_station_month": self.rx_station_month,
            "tx_station_month": self.tx_station_month,
            "wd_receivers": self.wd_receivers,
            "population": self.population,
        }


def _accumulate(config: CensusConfig, family: str) -> _StatsAccumulator:
    """Load one stats family from cache into an accumulator, window by window."""
    from .client import read_cache
    from .extract import _family_files

    acc = _StatsAccumulator()
    for path in sorted(_family_files(config, family)):
        frame = read_cache(path)
        if frame.empty:
            continue
        frame["window_start"] = pd.to_datetime(path.name.split("_")[0], format="%Y%m%d")
        acc.ingest(frame)
    return acc


def summarize(config: CensusConfig, geomag_epoch: str | None = None) -> CensusTables:
    """Build every summary table from the cached query results.

    Parameters
    ----------
    config : CensusConfig
        Must point at a cache populated by :func:`~wspr_station_census.extract.extract`.
    geomag_epoch : str, optional
        ``YYYY-MM-DD`` epoch for the AACGM-v2 conversion. Defaults to the middle
        of the study period. Ignored if :mod:`aacgmv2` is not installed.

    Returns
    -------
    CensusTables
    """
    period_days = (config.end_date - config.start_date).days
    epoch = geomag_epoch or mid_period_epoch(config)

    LOGGER.info("summarising receivers")
    rx = _summarize_receivers(config, period_days, epoch)
    LOGGER.info("summarising transmitters")
    tx = _summarize_transmitters(config, period_days, epoch)
    LOGGER.info("summarising per-receiver (WsprDaemon rx_id) inventory")
    wd_receivers = _summarize_wd_receivers(config)
    population = _summarize_population(config)

    return CensusTables(
        rx_stations=rx["stations"],
        tx_stations=tx["stations"],
        rx_station_band=rx["bands"],
        tx_station_band=tx["bands"],
        rx_station_month=rx["months"],
        tx_station_month=tx["months"],
        wd_receivers=wd_receivers,
        population=population,
    )


def mid_period_epoch(config: CensusConfig) -> str:
    """Return the midpoint of the study period as ``YYYY-MM-DD``.

    Used as the default AACGM-v2 epoch. Exposed publicly so the CLI can resolve
    it before summarising and record the value actually used in the manifest.
    """
    start, end = config.start_date, config.end_date
    return (start + (end - start) / 2).strftime("%Y-%m-%d")


def _summarize_receivers(
    config: CensusConfig, period_days: int, epoch: str
) -> dict[str, pd.DataFrame]:
    """Build the receiver station, band and month tables."""
    net = _accumulate(config, "stats_rx_wsprnet")
    wd = _accumulate(config, "stats_rx_wsprdaemon")

    net_stations = net.station_frame(period_days)
    wd_stations = wd.station_frame(period_days)

    detail_net = load_family(config, "detail_rx_wsprnet")
    detail_wd = load_family(config, "detail_rx_wsprdaemon")

    # --- identity and provenance -------------------------------------------
    stations = pd.DataFrame(
        {"rx_sign": sorted(set(net_stations["station"]) | set(wd_stations["station"]))}
    )
    stations["site_call"] = stations["rx_sign"].map(callsigns.base_call)
    stations["callsign_class"] = stations["rx_sign"].map(callsigns.classify)
    stations["in_wsprnet"] = stations["rx_sign"].isin(set(net_stations["station"]))
    stations["in_wsprdaemon"] = stations["rx_sign"].isin(set(wd_stations["station"]))
    stations["source_class"] = np.select(
        [
            stations["in_wsprnet"] & stations["in_wsprdaemon"],
            stations["in_wsprnet"],
        ],
        ["both", "wsprnet_only"],
        default="wsprdaemon_only",
    )
    site_counts = (
        stations.groupby("site_call")["rx_sign"].nunique().rename("n_sign_variants_at_site")
    )
    stations = stations.merge(site_counts, left_on="site_call", right_index=True, how="left")

    # --- metrics from wspr.rx (the broad-coverage table) --------------------
    stations = stations.merge(
        _rename_metrics(net_stations, "wsprnet"), left_on="rx_sign", right_on="station",
        how="left",
    ).drop(columns=["station"], errors="ignore")

    # --- metrics from wsprdaemon.spots (extended, per-receiver rows) --------
    stations = stations.merge(
        _rename_metrics(wd_stations, "wd", keep_noise=True),
        left_on="rx_sign",
        right_on="station",
        how="left",
    ).drop(columns=["station"], errors="ignore")

    # --- sets: bands, grids, versions, receiver instances -------------------
    bands_net = _decode_bands(net.set_table("bands_raw"), "mhz_int")
    bands_wd = _decode_bands(wd.set_table("bands_raw"), "meters")
    all_bands = pd.concat([bands_net, bands_wd], ignore_index=True).drop_duplicates()
    stations = _merge_set(stations, "rx_sign", _join_set(all_bands, "band", "bands", True))

    # --- activity: union the day sets across BOTH tables --------------------
    # A receiver present only in wsprdaemon.spots (a wsprnet drop, or a site
    # whose uploads never reached wsprnet) would otherwise have no day count at
    # all and be tiered as if it had barely operated.
    stations = _apply_combined_activity(stations, "rx_sign", [net, wd], period_days)

    grids = pd.concat(
        [net.set_table("grids"), wd.set_table("grids")], ignore_index=True
    ).drop_duplicates()
    stations = _merge_set(stations, "rx_sign", _join_set(grids, "grid", "grids"))
    stations = _merge_set(
        stations, "rx_sign", _join_set(net.set_table("versions"), "version", "versions")
    )
    stations = _merge_set(
        stations, "rx_sign", _join_set(wd.set_table("rx_ids"), "rx_id", "rx_ids")
    )

    # --- location: modal grid across both tables ---------------------------
    detail_all = pd.concat(
        [
            detail_net[["station", "grid", "n_spots"]] if not detail_net.empty else pd.DataFrame(),
            detail_wd[["station", "grid", "n_spots"]] if not detail_wd.empty else pd.DataFrame(),
        ],
        ignore_index=True,
    )
    stations = stations.merge(
        _modal_by_spots(detail_all, "grid"), left_on="rx_sign", right_on="station", how="left"
    ).drop(columns=["station"], errors="ignore")
    stations = _add_position(stations, "modal_grid")
    stations = _add_geomagnetic(stations, epoch)

    # --- software class from wspr.rx versions -------------------------------
    modal_version = (
        _modal_by_spots(detail_net, "version") if not detail_net.empty else pd.DataFrame()
    )
    stations = stations.merge(
        modal_version, left_on="rx_sign", right_on="station", how="left"
    ).drop(columns=["station"], errors="ignore")
    stations["sw_class"] = stations.get(
        "modal_version", pd.Series(index=stations.index, dtype="object")
    ).map(callsigns.classify_software)

    stations = assign_rx_tier(stations, period_days)

    # --- per-band and per-month tables -------------------------------------
    band_table = _band_table(detail_net, detail_wd, "rx_sign")
    month_table = _month_table(
        net.month_table(), wd.month_table(), detail_net, detail_wd, "rx_sign"
    )

    return {
        "stations": _order_columns(stations, _RX_COLUMN_ORDER),
        "bands": band_table,
        "months": month_table,
    }


def _summarize_transmitters(
    config: CensusConfig, period_days: int, epoch: str
) -> dict[str, pd.DataFrame]:
    """Build the transmitter station, band and month tables."""
    net = _accumulate(config, "stats_tx_wsprnet")
    net_stations = net.station_frame(period_days)
    detail_net = load_family(config, "detail_tx_wsprnet")
    membership = load_family(config, "membership_tx_wsprdaemon")

    stations = pd.DataFrame({"tx_sign": net_stations["station"]})
    stations["base_call"] = stations["tx_sign"].map(callsigns.base_call)
    stations["callsign_class"] = stations["tx_sign"].map(callsigns.classify)
    stations["in_wsprnet"] = True
    wd_calls = set(membership["station"]) if not membership.empty else set()
    stations["in_wsprdaemon"] = stations["tx_sign"].isin(wd_calls)
    stations["source_class"] = np.where(stations["in_wsprdaemon"], "both", "wsprnet_only")

    stations = stations.merge(
        _rename_metrics(net_stations, "wsprnet"), left_on="tx_sign", right_on="station",
        how="left",
    ).drop(columns=["station"], errors="ignore")

    if not membership.empty:
        wd_spots = membership.groupby("station", as_index=False)["n_spots"].sum().rename(
            columns={"n_spots": "n_spots_wd_raw"}
        )
        stations = stations.merge(
            wd_spots, left_on="tx_sign", right_on="station", how="left"
        ).drop(columns=["station"], errors="ignore")
    else:
        stations["n_spots_wd_raw"] = np.nan

    stations = _merge_set(
        stations,
        "tx_sign",
        _join_set(_decode_bands(net.set_table("bands_raw"), "mhz_int"), "band", "bands", True),
    )
    stations = _merge_set(
        stations, "tx_sign", _join_set(net.set_table("grids"), "grid", "grids")
    )
    stations = _merge_set(
        stations, "tx_sign", _join_set(net.set_table("powers"), "power", "powers")
    )

    stations = stations.merge(
        _modal_by_spots(detail_net, "grid"), left_on="tx_sign", right_on="station", how="left"
    ).drop(columns=["station"], errors="ignore")
    stations = stations.merge(
        _modal_by_spots(detail_net, "power"), left_on="tx_sign", right_on="station", how="left"
    ).drop(columns=["station"], errors="ignore")
    stations = _add_position(stations, "modal_grid")
    stations = _add_geomagnetic(stations, epoch)
    stations = assign_tx_tier(stations)

    band_table = _band_table(detail_net, pd.DataFrame(), "tx_sign")
    month_table = _month_table(
        net.month_table(), pd.DataFrame(), detail_net, pd.DataFrame(), "tx_sign"
    )
    return {
        "stations": _order_columns(stations, _TX_COLUMN_ORDER),
        "bands": band_table,
        "months": month_table,
    }


def _summarize_wd_receivers(config: CensusConfig) -> pd.DataFrame:
    """Build the per-receiver-instance table from the WsprDaemon detail family.

    ``rx_id`` is only unique *within* a site, so the key is ``(rx_sign, rx_id)``.
    This table is where the noise-capability assessment lives: a site can have one
    well-calibrated receiver and one reporting nonsense, and the site-level
    average would hide that.
    """
    detail = load_family(config, "detail_rx_wsprdaemon")
    if detail.empty:
        return pd.DataFrame()
    detail = detail.copy()
    detail["band"] = detail["band_raw"].map(
        {raw: band_mod.decode_band(raw, "meters") for raw in detail["band_raw"].unique()}
    )
    detail["rms_noise_w"] = pd.to_numeric(detail["rms_noise_mean"], errors="coerce") * detail[
        "n_noise_ok"
    ]
    detail["c2_noise_w"] = pd.to_numeric(detail["c2_noise_mean"], errors="coerce") * detail[
        "n_noise_ok"
    ]
    grouped = detail.groupby(["station", "rx_id"], as_index=False).agg(
        n_spots=("n_spots", "sum"),
        n_noise_ok=("n_noise_ok", "sum"),
        n_ov_nonzero=("n_ov_nonzero", "sum"),
        rms_noise_w=("rms_noise_w", "sum"),
        c2_noise_w=("c2_noise_w", "sum"),
        first_spot=("first_spot", "min"),
        last_spot=("last_spot", "max"),
        n_bands=("band", "nunique"),
    )
    band_lists = (
        detail.groupby(["station", "rx_id"])["band"]
        .agg(list)
        .map(band_mod.format_band_list)
        .rename("bands")
        .reset_index()
    )
    grouped = grouped.merge(band_lists, on=["station", "rx_id"], how="left")
    grouped["noise_valid_frac"] = _safe_div(grouped["n_noise_ok"], grouped["n_spots"]).round(4)
    grouped["rms_noise_mean"] = _safe_div(grouped["rms_noise_w"], grouped["n_noise_ok"]).round(2)
    grouped["c2_noise_mean"] = _safe_div(grouped["c2_noise_w"], grouped["n_noise_ok"]).round(2)
    grouped["ov_frac"] = _safe_div(grouped["n_ov_nonzero"], grouped["n_spots"]).round(4)
    # A receiver reporting no usable noise at all cannot support noise analysis,
    # regardless of how many spots it contributes.
    grouped["noise_usable"] = grouped["noise_valid_frac"].fillna(0) >= 0.5
    grouped = grouped.drop(columns=["rms_noise_w", "c2_noise_w"])
    return grouped.rename(columns={"station": "rx_sign"}).sort_values(
        ["rx_sign", "rx_id"], ignore_index=True
    )


def _summarize_population(config: CensusConfig) -> pd.DataFrame:
    """Assemble the unfiltered population census, per table, role and month."""
    rows = []
    for spec_key in ("wsprnet", "wsprdaemon"):
        for role in ("rx", "tx"):
            frame = load_family(config, f"population_{role}_{spec_key}")
            if frame.empty:
                continue
            frame = frame.copy()
            frame["table"] = spec_key
            frame["role"] = role
            frame["month"] = pd.to_datetime(frame["window_start"]).dt.to_period("M").astype(str)
            rows.append(
                frame[
                    ["table", "role", "month", "n_stations_all", "n_spots_all", "n_grids_all"]
                ]
            )
    if not rows:
        return pd.DataFrame()
    return pd.concat(rows, ignore_index=True).sort_values(
        ["table", "role", "month"], ignore_index=True
    )


def _rename_metrics(
    frame: pd.DataFrame, suffix: str, keep_noise: bool = False
) -> pd.DataFrame:
    """Suffix table-specific metric columns so both sources can sit side by side."""
    if frame.empty:
        return pd.DataFrame(columns=["station"])
    shared = {
        "n_spots": f"n_spots_{suffix}",
        "n_slots": f"n_slots_{suffix}",
        "n_partners_max_window": f"n_partners_max_window_{suffix}",
        "n_partners_sum_windows": f"n_partners_sum_windows_{suffix}",
        "snr_mean": f"snr_mean_{suffix}",
        "snr_min": f"snr_min_{suffix}",
        "snr_max": f"snr_max_{suffix}",
        "snr_p50_approx": f"snr_p50_approx_{suffix}",
        "dist_mean_km": f"dist_mean_km_{suffix}",
        "dist_p50_approx_km": f"dist_p50_approx_km_{suffix}",
        "dist_p90_approx_km": f"dist_p90_approx_km_{suffix}",
        "dist_max": f"dist_max_km_{suffix}",
        "n_geo_invalid_frac": f"n_geo_invalid_frac_{suffix}",
    }
    # These describe the station itself, not the table, so the wsprnet copy wins
    # and the WsprDaemon copy is suffixed for comparison.
    unsuffixed = [
        "n_days_active",
        "max_gap_days",
        "n_months_active",
        "duty_frac",
        "first_spot",
        "last_spot",
    ]
    noise = [
        "noise_valid_frac",
        "rms_noise_mean",
        "c2_noise_mean",
        "noise_disagree_mean",
        "ov_frac",
        "n_noise_ok",
    ]

    keep = ["station"] + [col for col in shared if col in frame.columns]
    out = frame[keep].rename(columns=shared)
    for column in unsuffixed:
        if column in frame.columns:
            name = column if suffix == "wsprnet" else f"{column}_{suffix}"
            out[name] = frame[column].values
    if keep_noise:
        for column in noise:
            if column in frame.columns:
                out[column] = frame[column].values
    return out


def _apply_combined_activity(
    frame: pd.DataFrame,
    key: str,
    accumulators: list[_StatsAccumulator],
    period_days: int,
) -> pd.DataFrame:
    """Recompute activity metrics from the union of several tables' day sets.

    The per-table ``station_frame`` results each describe activity *as seen by
    that table*. The station-level answer to "when was this station operating?"
    is the union, because a spot in either table is evidence of operation.
    ``n_days_active``, ``max_gap_days``, ``duty_frac``, ``n_months_active``,
    ``first_spot`` and ``last_spot`` are overwritten with the combined values;
    the per-table copies remain available under their suffixed names.

    Parameters
    ----------
    frame : pandas.DataFrame
        Station frame, keyed by ``key``.
    key : str
        Station key column, e.g. ``rx_sign``.
    accumulators : list of _StatsAccumulator
        Accumulators whose day and month sets should be unioned.
    period_days : int

    Returns
    -------
    pandas.DataFrame
    """
    out = frame.copy()
    day_pieces = [acc.set_table("days") for acc in accumulators]
    day_pieces = [piece for piece in day_pieces if not piece.empty]
    if day_pieces:
        combined = _day_statistics(day_pieces)
        out = out.drop(
            columns=[
                col
                for col in ("n_days_active", "max_gap_days", "first_day", "last_day")
                if col in out.columns
            ]
        )
        out = out.merge(combined, left_on=key, right_index=True, how="left")
        out["duty_frac"] = (out["n_days_active"] / period_days).round(4)

    month_pieces = [acc.month_table() for acc in accumulators]
    month_pieces = [piece for piece in month_pieces if not piece.empty]
    if month_pieces:
        months = (
            pd.concat([piece[["station", "month"]] for piece in month_pieces], ignore_index=True)
            .drop_duplicates()
            .groupby("station")["month"]
            .nunique()
            .rename("n_months_active_combined")
        )
        out = out.merge(months, left_on=key, right_index=True, how="left")
        out["n_months_active"] = out["n_months_active_combined"]
        out = out.drop(columns=["n_months_active_combined"])

    # Coalesce the per-table first/last timestamps into overall bounds.
    first_cols = [col for col in out.columns if col.startswith("first_spot")]
    last_cols = [col for col in out.columns if col.startswith("last_spot")]
    if first_cols:
        out["first_spot"] = out[first_cols].apply(pd.to_datetime, errors="coerce").min(axis=1)
    if last_cols:
        out["last_spot"] = out[last_cols].apply(pd.to_datetime, errors="coerce").max(axis=1)
    return out


def _merge_set(frame: pd.DataFrame, key: str, set_frame: pd.DataFrame) -> pd.DataFrame:
    """Left-merge a collapsed set table onto a station frame."""
    if set_frame.empty:
        return frame
    return frame.merge(set_frame, left_on=key, right_on="station", how="left").drop(
        columns=["station"], errors="ignore"
    )


def _band_table(
    detail_net: pd.DataFrame, detail_wd: pd.DataFrame, key: str
) -> pd.DataFrame:
    """Build the long station × band table from the detail families.

    Both spot counts are reported side by side. They are *not* comparable
    quantities: ``wspr.rx`` keeps one row per ``(time, tx, rx, band)`` — the
    strongest decode at a site — while ``wsprdaemon.spots`` keeps every receiver
    instance's decode separately. A site with nine receivers shows roughly nine
    times the raw count. That ratio is itself informative, so it is exposed as
    ``wd_inflation`` rather than hidden by normalising.
    """
    pieces = []
    for detail, convention, column in (
        (detail_net, "mhz_int", "n_spots_wsprnet"),
        (detail_wd, "meters", "n_spots_wd_raw"),
    ):
        if detail.empty:
            continue
        work = detail.copy()
        work["band"] = work["band_raw"].map(
            {raw: band_mod.decode_band(raw, convention) for raw in work["band_raw"].unique()}
        )
        work["month"] = pd.to_datetime(work["window_start"]).dt.to_period("M")
        grouped = work.groupby(["station", "band"], as_index=False).agg(
            **{
                column: ("n_spots", "sum"),
                f"n_months_{column.split('_')[-1]}": ("month", "nunique"),
                f"first_spot_{column.split('_')[-1]}": ("first_spot", "min"),
                f"last_spot_{column.split('_')[-1]}": ("last_spot", "max"),
            }
        )
        pieces.append(grouped)
    if not pieces:
        return pd.DataFrame()
    out = pieces[0]
    for piece in pieces[1:]:
        out = out.merge(piece, on=["station", "band"], how="outer")
    if "n_spots_wsprnet" in out.columns and "n_spots_wd_raw" in out.columns:
        out["wd_inflation"] = _safe_div(out["n_spots_wd_raw"], out["n_spots_wsprnet"]).round(2)
    out = out.rename(columns={"station": key})
    out["band_sort"] = out["band"].map(lambda label: band_mod.sort_key(label)[1])
    return out.sort_values([key, "band_sort"], ignore_index=True).drop(columns=["band_sort"])


def _month_table(
    month_net: pd.DataFrame,
    month_wd: pd.DataFrame,
    detail_net: pd.DataFrame,
    detail_wd: pd.DataFrame,
    key: str,
) -> pd.DataFrame:
    """Build the long station × month table.

    Active-day counts per month come from the ``stats`` day sets rather than from
    a separate query: because windows never straddle a month, the union of a
    month's day sets is exactly that month's active days.
    """
    pieces = []
    if not month_net.empty:
        pieces.append(
            month_net.rename(
                columns={
                    "n_spots": "n_spots_wsprnet",
                    "n_slots": "n_slots_wsprnet",
                    "n_partners_max_window": "n_partners_max_window_wsprnet",
                }
            )
        )
    if not month_wd.empty:
        pieces.append(
            month_wd.rename(
                columns={
                    "n_spots": "n_spots_wd_raw",
                    "n_slots": "n_slots_wd_raw",
                    "n_partners_max_window": "n_partners_max_window_wd",
                }
            )
        )
    if not pieces:
        return pd.DataFrame()
    out = pieces[0]
    for piece in pieces[1:]:
        out = out.merge(piece, on=["station", "month"], how="outer")

    for detail, suffix in ((detail_net, "wsprnet"), (detail_wd, "wd")):
        if detail.empty:
            continue
        work = detail.copy()
        work["month"] = pd.to_datetime(work["window_start"]).dt.to_period("M").astype(str)
        bands = (
            work.groupby(["station", "month"], as_index=False)["band_raw"]
            .nunique()
            .rename(columns={"band_raw": f"n_bands_{suffix}"})
        )
        out = out.merge(bands, on=["station", "month"], how="left")

    return out.rename(columns={"station": key}).sort_values([key, "month"], ignore_index=True)


#: Preferred column order for the receiver summary. Any column not listed is
#: appended, so adding a metric never breaks the writer.
_RX_COLUMN_ORDER = [
    "rx_sign",
    "site_call",
    "n_sign_variants_at_site",
    "callsign_class",
    "source_class",
    "in_wsprnet",
    "in_wsprdaemon",
    "sw_class",
    "modal_version",
    "versions",
    "n_versions",
    "n_rx_ids",
    "rx_ids",
    "modal_grid",
    "modal_grid_frac",
    "n_distinct_grid",
    "grids",
    "grid_valid",
    "grid_precision",
    "grid_cell_lat_deg",
    "lat",
    "lon",
    "hemisphere",
    "abs_lat",
    "cgm_lat",
    "abs_cgm_lat",
    "quality_tier",
    "geo_suspect",
    "n_days_active",
    "duty_frac",
    "n_months_active",
    "max_gap_days",
    "first_spot",
    "last_spot",
    "n_bands",
    "bands",
    "n_spots_wsprnet",
    "n_slots_wsprnet",
    "n_spots_wd",
    "n_slots_wd",
    "n_partners_max_window_wsprnet",
    "n_partners_sum_windows_wsprnet",
    "snr_mean_wsprnet",
    "snr_p50_approx_wsprnet",
    "dist_mean_km_wsprnet",
    "dist_p50_approx_km_wsprnet",
    "dist_p90_approx_km_wsprnet",
    "dist_max_km_wsprnet",
    "noise_valid_frac",
    "rms_noise_mean",
    "c2_noise_mean",
    "noise_disagree_mean",
    "ov_frac",
    "n_geo_invalid_frac_wsprnet",
]

#: Preferred column order for the transmitter summary.
_TX_COLUMN_ORDER = [
    "tx_sign",
    "base_call",
    "callsign_class",
    "source_class",
    "in_wsprnet",
    "in_wsprdaemon",
    "quality_tier",
    "reject_reason",
    "modal_grid",
    "modal_grid_frac",
    "n_distinct_grid",
    "grids",
    "grid_valid",
    "grid_precision",
    "grid_cell_lat_deg",
    "lat",
    "lon",
    "hemisphere",
    "abs_lat",
    "cgm_lat",
    "abs_cgm_lat",
    "n_days_active",
    "duty_frac",
    "n_months_active",
    "max_gap_days",
    "first_spot",
    "last_spot",
    "n_bands",
    "bands",
    "modal_power",
    "n_distinct_power",
    "powers",
    "n_spots_wsprnet",
    "n_slots_wsprnet",
    "n_spots_wd_raw",
    "n_partners_max_window_wsprnet",
    "n_partners_sum_windows_wsprnet",
    "snr_mean_wsprnet",
    "snr_p50_approx_wsprnet",
    "dist_mean_km_wsprnet",
    "dist_p90_approx_km_wsprnet",
    "dist_max_km_wsprnet",
    "n_geo_invalid_frac_wsprnet",
]


def _order_columns(frame: pd.DataFrame, preferred: list[str]) -> pd.DataFrame:
    """Reorder columns and tidy integer types.

    Left joins turn count columns into floats as soon as any station is missing
    from one source, which writes ``1.0`` where ``1`` is meant. Nullable
    ``Int64`` keeps them integral while still allowing a genuine missing value.
    """
    head = [column for column in preferred if column in frame.columns]
    tail = [column for column in frame.columns if column not in head]
    out = frame[head + tail].copy()
    for column in out.columns:
        looks_like_count = column.startswith("n_") or column in {
            "grid_precision",
            "modal_power",
            "max_gap_days",
        }
        if looks_like_count and not column.endswith("_frac"):
            numeric = pd.to_numeric(out[column], errors="coerce")
            if numeric.notna().any() and (numeric.dropna() % 1 == 0).all():
                out[column] = numeric.astype("Int64")
    return out


def write_tables(tables: CensusTables, config: CensusConfig) -> dict[str, str]:
    """Write every summary table to ``config.output_dir`` as CSV.

    Returns
    -------
    dict
        ``{name: path}`` for the manifest.
    """
    config.output_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    for name, frame in tables.as_dict().items():
        if frame is None or frame.empty:
            LOGGER.warning("table %s is empty; not written", name)
            continue
        path = config.output_dir / f"{name}.csv"
        frame.to_csv(path, index=False)
        written[name] = str(path)
        LOGGER.info("wrote %-20s %8d rows  %s", name, len(frame), path.name)
    return written
