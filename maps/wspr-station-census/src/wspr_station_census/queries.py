"""SQL builders for the census, and the table/role descriptions they need.

Design
------
Aggregation happens **server-side**. The study period holds 1.02 billion rows in
``wsprdaemon.spots`` and roughly 2.8 billion in ``wspr.rx``; pulling raw spots is
never the right move when ClickHouse will reduce them for you in a second or two.

Three query families do the work, split by *grain* rather than by output file,
because distinct counts are only meaningful at the grain they are computed at:

``stats`` (grain: station × window)
    The distinct-count-heavy family: distinct 2-minute slots, distinct partner
    callsigns, and — via ``groupUniqArray`` — the exact **set** of active dates,
    bands, grids, software versions and TX powers. Returning day *sets* rather
    than day *counts* costs almost nothing (at most ten dates per row) and makes
    the period-level day count, the activity gap analysis and the band list exact
    rather than approximate.

``detail`` (grain: station × band × grid × [version | power] × month)
    Additive counts only, so it is cheap and can run over a whole month. This is
    what makes the modal grid, the per-band spot counts, the modal TX power and
    the per-receiver noise statistics available.

``population`` (grain: window)
    A single row per window recording the *unfiltered* station and spot counts,
    so the manifest can state honestly how much of the raw population the TX
    pre-gate removed.

Every builder takes an explicit half-open ``[lo, hi)`` window. Unbounded queries
return an nginx ``504``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

#: Defensive filter for the noise columns, from the caveats in
#: ``docs/wsprdaemon_extended_spots_access.md`` §6: it removes the ``0`` and
#: ``-999`` sentinels *and* the grossly miscalibrated sites reporting near
#: -45 dBm/Hz, which are ~85 dB above the population median.
NOISE_OK = "rms_noise BETWEEN -200 AND -50"


@dataclass(frozen=True)
class TableSpec:
    """Description of one source table.

    Parameters
    ----------
    table : str
        Fully qualified table name.
    key : str
        Short identifier used in cache paths and output column suffixes.
    band_convention : {'mhz_int', 'meters'}
        How to decode the ``band`` column; see :mod:`wspr_station_census.bands`.
    has_noise : bool
        True if the table carries ``rms_noise`` / ``c2_noise`` / ``ov_count``.
    has_rx_id : bool
        True if the table distinguishes receiver instances within a site.
    has_version : bool
        True if the ``version`` column is usable. False for
        ``wsprdaemon.spots``, where it is NULL for 95 of 100 receivers.
    sentinel_geometry : bool
        True if ``distance``/``azimuth``/latitude/longitude carry ``-999``
        sentinels that must be excluded from statistics.
    """

    table: str
    key: str
    band_convention: str
    has_noise: bool
    has_rx_id: bool
    has_version: bool
    sentinel_geometry: bool


#: The wsprnet.org clone: broad receiver coverage, integer-MHz band codes, known
#: to silently drop spots.
WSPRNET = TableSpec(
    table="wspr.rx",
    key="wsprnet",
    band_convention="mhz_int",
    has_noise=False,
    has_rx_id=False,
    has_version=True,
    sentinel_geometry=False,
)

#: The WsprDaemon extended-spot table: per-receiver rows with noise, wavelength
#: band codes, loss-resistant but WD-client receivers only.
WSPRDAEMON = TableSpec(
    table="wsprdaemon.spots",
    key="wsprdaemon",
    band_convention="meters",
    has_noise=True,
    has_rx_id=True,
    has_version=False,
    sentinel_geometry=True,
)

TABLES: dict[str, TableSpec] = {spec.key: spec for spec in (WSPRNET, WSPRDAEMON)}


@dataclass(frozen=True)
class Role:
    """Which end of the link a station is being counted as.

    Parameters
    ----------
    name : {'rx', 'tx'}
    call, loc, lat, lon : str
        Column names for this end of the link.
    partner : str
        Callsign column for the *other* end, used for distinct-partner counts.
    """

    name: str
    call: str
    loc: str
    lat: str
    lon: str
    partner: str


RX = Role(name="rx", call="rx_sign", loc="rx_loc", lat="rx_lat", lon="rx_lon", partner="tx_sign")
TX = Role(name="tx", call="tx_sign", loc="tx_loc", lat="tx_lat", lon="tx_lon", partner="rx_sign")

ROLES: dict[str, Role] = {role.name: role for role in (RX, TX)}


def _window(lo: date, hi: date) -> str:
    """Render a half-open time predicate for ``[lo, hi)``."""
    return f"time >= '{lo:%Y-%m-%d} 00:00:00' AND time < '{hi:%Y-%m-%d} 00:00:00'"


def family_name(kind: str, spec: TableSpec, role: Role) -> str:
    """Return the cache-directory name for a query family, e.g. ``stats_rx_wsprnet``."""
    return f"{kind}_{role.name}_{spec.key}"


def stats_sql(
    spec: TableSpec,
    role: Role,
    lo: date,
    hi: date,
    min_spots: int = 1,
    min_partners: int = 1,
) -> str:
    """Build the station × window statistics query.

    Parameters
    ----------
    spec : TableSpec
        Source table.
    role : Role
        Whether to aggregate by receiver or transmitter callsign.
    lo, hi : datetime.date
        Half-open window.
    min_spots, min_partners : int, optional
        Server-side pre-gate applied via ``HAVING``. Used for the transmitter
        role, where the unfiltered population is ~167,000 callsigns per month and
        the great majority are single false decodes. Defaults of 1 keep
        everything, which is what the receiver role wants.

    Returns
    -------
    str
        A complete SQL statement without a ``FORMAT`` clause.

    Notes
    -----
    ``distance`` is ``UInt16`` in ``wspr.rx`` but ``Int32`` in
    ``wsprdaemon.spots``, where sentinel rows carry negative values; distance
    statistics are therefore computed with ``…If(distance >= 0)`` guards in both
    tables so the two are directly comparable.

    Medians use ``quantileTDigest``, which has bounded memory. Exact quantiles
    over a group of 200 million values would be an unkind thing to ask of a
    volunteer-run server; exact sums, counts, minima and maxima are also
    returned, so the mean is exact even though the median is approximate.
    """
    parts = [
        f"{role.call} AS station",
        "count() AS n_spots",
        "uniqExact(time) AS n_slots",
        f"uniqExact({role.partner}) AS n_partners",
        # Day *sets*, not day counts: cheap (<= 10 dates/row) and they make the
        # period-level day count and the activity-gap analysis exact.
        "groupUniqArray(40)(toDate(time)) AS days",
        "groupUniqArray(32)(band) AS bands_raw",
        f"uniqExact({role.loc}) AS n_grids",
        f"groupUniqArray(8)(toString({role.loc})) AS grids",
        "min(time) AS first_spot",
        "max(time) AS last_spot",
        "sum(toInt64(snr)) AS snr_sum",
        "min(snr) AS snr_min",
        "max(snr) AS snr_max",
        "round(quantileTDigest(0.5)(snr), 2) AS snr_p50",
        "countIf(distance >= 0) AS n_dist_ok",
        "sumIf(toInt64(distance), distance >= 0) AS dist_sum",
        "maxIf(distance, distance >= 0) AS dist_max",
        "round(quantileTDigestIf(0.5)(distance, distance >= 0), 1) AS dist_p50",
        "round(quantileTDigestIf(0.9)(distance, distance >= 0), 1) AS dist_p90",
        (
            f"countIf(NOT ({role.lat} BETWEEN -90 AND 90)"
            f" OR NOT ({role.lon} BETWEEN -180 AND 180)) AS n_geo_invalid"
        ),
    ]

    if spec.has_version and role.name == "rx":
        # `version` is the *reporting receiver's* software, so it is only
        # meaningful when aggregating by receiver. Grouping it under a
        # transmitter callsign would describe whoever happened to hear it.
        parts += [
            "uniqExact(version) AS n_versions",
            "groupUniqArray(8)(toString(version)) AS versions",
        ]
    if spec.has_rx_id and role.name == "rx":
        parts += [
            "uniqExact(rx_id) AS n_rx_ids",
            "groupUniqArray(24)(toString(rx_id)) AS rx_ids",
        ]
    if spec.has_noise:
        parts += [
            f"countIf({NOISE_OK}) AS n_noise_ok",
            f"round(avgIf(rms_noise, {NOISE_OK}), 2) AS rms_noise_mean",
            f"round(avgIf(c2_noise, {NOISE_OK}), 2) AS c2_noise_mean",
            (
                f"round(avgIf(abs(c2_noise - rms_noise), {NOISE_OK}"
                " AND c2_noise BETWEEN -200 AND -50), 2) AS noise_disagree_mean"
            ),
            "countIf(ov_count > 0) AS n_ov_nonzero",
        ]
    if role.name == "tx":
        parts += [
            "uniqExact(power) AS n_powers",
            "groupUniqArray(24)(power) AS powers",
        ]

    having = ""
    if min_spots > 1 or min_partners > 1:
        having = f"\nHAVING n_spots >= {min_spots} AND n_partners >= {min_partners}"

    select = ",\n       ".join(parts)
    return (
        f"SELECT {select}\n"
        f"FROM {spec.table}\n"
        f"WHERE {_window(lo, hi)}\n"
        f"GROUP BY station{having}"
    )


def detail_sql(
    spec: TableSpec,
    role: Role,
    lo: date,
    hi: date,
    min_spots: int = 1,
) -> str:
    """Build the station × band × grid × extras × window detail query.

    Only additive aggregates appear here, which keeps the query cheap enough to
    run over a whole month and lets every derived quantity be summed client-side
    without worrying about double counting.

    The "extras" in the grain are chosen per table and role:

    * ``wspr.rx`` receivers add ``version`` — the software/hardware fingerprint.
    * ``wspr.rx`` transmitters add ``power`` — both a real property and a tell
      for telemetry callsigns, which cycle the power field to encode data.
    * ``wsprdaemon.spots`` receivers add ``rx_id`` — the receiver instance, which
      is the grain at which the noise columns are meaningful.

    Parameters
    ----------
    spec : TableSpec
        Source table.
    role : Role
        Receiver or transmitter aggregation.
    lo, hi : datetime.date
        Half-open window.
    min_spots : int, optional
        ``HAVING`` floor on the per-group spot count, to keep the transmitter
        result set manageable.

    Returns
    -------
    str
        A complete SQL statement without a ``FORMAT`` clause.
    """
    keys = [
        f"{role.call} AS station",
        "band AS band_raw",
        f"toString({role.loc}) AS grid",
    ]
    if spec.has_version and role.name == "rx":
        keys.append("toString(version) AS version")
    if spec.has_rx_id and role.name == "rx":
        keys.append("toString(rx_id) AS rx_id")
    if role.name == "tx":
        keys.append("power")

    measures = [
        "count() AS n_spots",
        "min(time) AS first_spot",
        "max(time) AS last_spot",
    ]
    if spec.has_noise:
        measures += [
            f"countIf({NOISE_OK}) AS n_noise_ok",
            f"round(avgIf(rms_noise, {NOISE_OK}), 2) AS rms_noise_mean",
            f"round(avgIf(c2_noise, {NOISE_OK}), 2) AS c2_noise_mean",
            "countIf(ov_count > 0) AS n_ov_nonzero",
        ]

    group_by = ", ".join(
        key.split(" AS ")[-1] if " AS " in key else key for key in keys
    )
    having = f"\nHAVING n_spots >= {min_spots}" if min_spots > 1 else ""
    select = ",\n       ".join(keys + measures)
    return (
        f"SELECT {select}\n"
        f"FROM {spec.table}\n"
        f"WHERE {_window(lo, hi)}\n"
        f"GROUP BY {group_by}{having}"
    )


def membership_sql(
    spec: TableSpec,
    role: Role,
    lo: date,
    hi: date,
    min_spots: int = 2,
) -> str:
    """Build a minimal per-station spot count, used only as a presence flag.

    The transmitter side of ``wsprdaemon.spots`` is queried this way rather than
    with the full :func:`stats_sql`, because all that is needed from it is
    whether a given transmitter was heard by a WsprDaemon site at all — the
    detailed transmitter characterisation comes from ``wspr.rx``, which has
    ~20× the receiver coverage.

    Parameters
    ----------
    spec : TableSpec
        Source table.
    role : Role
        Receiver or transmitter aggregation.
    lo, hi : datetime.date
        Half-open window.
    min_spots : int, optional
        ``HAVING`` floor. The default of 2 drops one-off decodes, which cannot
        pass any downstream gate anyway, and roughly halves the result size.

    Returns
    -------
    str
    """
    having = f"\nHAVING n_spots >= {min_spots}" if min_spots > 1 else ""
    return (
        f"SELECT {role.call} AS station,\n"
        "       count() AS n_spots,\n"
        "       min(time) AS first_spot,\n"
        "       max(time) AS last_spot\n"
        f"FROM {spec.table}\n"
        f"WHERE {_window(lo, hi)}\n"
        f"GROUP BY station{having}"
    )


def population_sql(spec: TableSpec, role: Role, lo: date, hi: date) -> str:
    """Build a one-row census of the *unfiltered* population in a window.

    This exists so the run manifest can report what the transmitter pre-gate
    discarded. Without it, a summary of 2,562 transmitters gives no hint that
    166,977 callsigns were present in the raw month, and a reader has no way to
    judge the filtering.

    Parameters
    ----------
    spec : TableSpec
        Source table.
    role : Role
        Which callsign column to count distinct values of.
    lo, hi : datetime.date
        Half-open window.

    Returns
    -------
    str
    """
    return (
        f"SELECT uniqExact({role.call}) AS n_stations_all,\n"
        "       count() AS n_spots_all,\n"
        f"       uniqExact({role.loc}) AS n_grids_all\n"
        f"FROM {spec.table}\n"
        f"WHERE {_window(lo, hi)}"
    )
