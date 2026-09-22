"""Canonical band labels, and the table-specific decoding needed to get them.

The two source tables encode ``band`` differently, and using the wrong
convention returns **zero rows with no error**:

============================  =====================  ==========  ==========
Table                         ``band`` means         20 m        40 m
============================  =====================  ==========  ==========
``wspr.rx``                   integer MHz            ``14``      ``7``
``wsprdaemon.spots``          wavelength in meters   ``20``      ``40``
``wsprdaemon.noise``          meters, as a *String*  ``'20'``    ``'40'``
============================  =====================  ==========  ==========

Worse, the codes *collide*: ``band = 40`` means the 40 m band (7 MHz) in
``wsprdaemon.spots`` but the 8 m band (40.68 MHz) in ``wspr.rx``, and
``band = 6`` means the 6 m band (50 MHz) in ``wsprdaemon.spots`` but assorted
out-of-band junk near 6–7 MHz in ``wspr.rx``. Decoding is therefore always
table-specific, and this module is the only place either mapping lives.

Both mappings below were built from the band/frequency cross-tabulations
measured on 2026-07-28 for June 2026 (see ``docs/COLUMNS.md``), not from
recollection.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Label used for any band code not in the table-specific maps below. In
#: ``wspr.rx`` these are almost entirely spurious decodes at out-of-band
#: frequencies (``band`` values 2, 9, 15, 19, 27, 30, 46, 107 …, a few dozen
#: spots each in a month of 218 million).
OTHER = "other"


@dataclass(frozen=True)
class Band:
    """A canonical amateur band.

    Parameters
    ----------
    label : str
        Human-readable label used in every output CSV, e.g. ``"20m"``.
    nominal_mhz : float
        Nominal WSPR dial frequency in MHz, for reference and for ordering.
    wavelength_m : float
        Nominal wavelength in meters. Used only for sorting.
    hf : bool
        True for 1.8–30 MHz. The polar-PSWS study is HF-centric; the 8 m, 6 m
        and higher bands are retained but flagged as non-HF.
    """

    label: str
    nominal_mhz: float
    wavelength_m: float
    hf: bool


#: Canonical bands, ordered by frequency. Frequencies are the dial frequencies
#: measured in ``wsprdaemon.spots`` for June 2026.
BANDS: tuple[Band, ...] = (
    Band("2200m", 0.1375, 2200.0, False),
    Band("630m", 0.4757, 630.0, False),
    Band("160m", 1.8380, 160.0, True),
    Band("80m", 3.5700, 80.0, True),
    Band("60m", 5.2886, 60.0, True),
    Band("40m", 7.0401, 40.0, True),
    Band("30m", 10.1402, 30.0, True),
    Band("22m", 13.5554, 22.0, True),
    Band("20m", 14.0971, 20.0, True),
    Band("17m", 18.1061, 17.0, True),
    Band("15m", 21.0961, 15.0, True),
    Band("12m", 24.9261, 12.0, True),
    Band("10m", 28.1261, 10.0, True),
    Band("8m", 40.6815, 8.0, False),
    Band("6m", 50.2945, 6.0, False),
    Band("4m", 70.0924, 4.0, False),
    Band("2m", 144.4890, 2.0, False),
    Band("70cm", 432.3014, 0.70, False),
    Band("23cm", 1296.5005, 0.23, False),
    Band("13cm", 2301.0664, 0.13, False),
)

BY_LABEL: dict[str, Band] = {band.label: band for band in BANDS}

#: ``wspr.rx``: integer-MHz code -> canonical label. ``-1`` is 137 kHz and ``0``
#: is 475 kHz, per the wsprnet convention. Codes 3 and 5 each cover two dial
#: frequencies (3.570/3.594 and 5.289/5.366); both collapse to one band here,
#: which is the intent.
WSPRNET_MHZ_TO_LABEL: dict[int, str] = {
    -1: "2200m",
    0: "630m",
    1: "160m",
    3: "80m",
    5: "60m",
    7: "40m",
    10: "30m",
    13: "22m",
    14: "20m",
    18: "17m",
    21: "15m",
    24: "12m",
    28: "10m",
    40: "8m",
    50: "6m",
    70: "4m",
    144: "2m",
    145: "2m",
    430: "70cm",
    432: "70cm",
    1296: "23cm",
    2301: "13cm",
    2400: "13cm",
}

#: ``wsprdaemon.spots``: wavelength-in-meters code -> canonical label. Note that
#: ``band = 8`` and ``band = 22`` carry ``band_m = 9999`` in the source table
#: (non-standard segments at 40.68 and 13.555 MHz) yet ``band`` itself is a
#: sensible wavelength, so ``band`` is the column to decode.
WSPRDAEMON_METERS_TO_LABEL: dict[int, str] = {
    2200: "2200m",
    630: "630m",
    160: "160m",
    80: "80m",
    60: "60m",
    40: "40m",
    30: "30m",
    22: "22m",
    20: "20m",
    17: "17m",
    15: "15m",
    12: "12m",
    10: "10m",
    8: "8m",
    6: "6m",
    4: "4m",
    2: "2m",
}

#: Decoder registry keyed by the :class:`~wspr_station_census.queries.TableSpec`
#: ``band_convention`` value.
_MAPS: dict[str, dict[int, str]] = {
    "mhz_int": WSPRNET_MHZ_TO_LABEL,
    "meters": WSPRDAEMON_METERS_TO_LABEL,
}


def decode_band(raw: int | float | str | None, convention: str) -> str:
    """Map a raw ``band`` code to a canonical label.

    Parameters
    ----------
    raw : int or float or str or None
        The raw ``band`` value as returned by the database.
    convention : {'mhz_int', 'meters'}
        ``'mhz_int'`` for ``wspr.rx``, ``'meters'`` for ``wsprdaemon.spots``.

    Returns
    -------
    str
        A canonical label from :data:`BANDS`, or :data:`OTHER` if the code is
        not a recognised band in that convention.

    Raises
    ------
    KeyError
        If ``convention`` is unknown.

    Examples
    --------
    >>> decode_band(14, "mhz_int")
    '20m'
    >>> decode_band(40, "mhz_int")     # 40 MHz in wsprnet's coding
    '8m'
    >>> decode_band(40, "meters")      # 40 metres in wsprdaemon's coding
    '40m'
    >>> decode_band(9, "mhz_int")      # spurious out-of-band decode
    'other'
    """
    table = _MAPS[convention]
    if raw is None:
        return OTHER
    try:
        code = int(raw)
    except (TypeError, ValueError):
        return OTHER
    return table.get(code, OTHER)


def sort_key(label: str) -> tuple[int, float]:
    """Return a sort key ordering bands by wavelength, longest first.

    ``other`` sorts last. Longest-first puts 2200 m at the top, matching how
    band tables are conventionally printed.
    """
    band = BY_LABEL.get(label)
    if band is None:
        return (1, 0.0)
    return (0, -band.wavelength_m)


def format_band_list(labels: object) -> str:
    """Render an iterable of band labels as a sorted, pipe-delimited string.

    Parameters
    ----------
    labels : iterable of str
        Band labels, possibly with duplicates. Non-iterable or empty input
        yields an empty string.

    Returns
    -------
    str
        e.g. ``"80m|40m|30m|20m"``.
    """
    if labels is None or isinstance(labels, str):
        return "" if not labels else str(labels)
    try:
        unique = {str(label) for label in labels}  # type: ignore[union-attr]
    except TypeError:
        return ""
    return "|".join(sorted(unique, key=sort_key))
