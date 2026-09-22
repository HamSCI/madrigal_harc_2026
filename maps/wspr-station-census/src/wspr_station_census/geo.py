"""Maidenhead locator decoding, position validation, and optional geomagnetic latitude.

Positions in the census are derived from the **Maidenhead grid square**, not from
the ``rx_lat``/``rx_lon``/``tx_lat``/``tx_lon`` columns. That is a deliberate
choice forced by the data (all figures measured 2026-07-28):

* ``wsprdaemon.spots`` uses **-999 as a sentinel** in the latitude and longitude
  columns for 1.4% of rows (1,220,928 of 88,185,059 in June 2026 — exactly the
  rows that also carry negative ``distance`` and ``azimuth``). Filtering on
  ``rx_lat <= -60`` without knowing this yields 148 phantom "Antarctic"
  receivers.
* Both tables contain **impossible latitudes**: ``max(tx_lat)`` is 157.3 in
  ``wsprdaemon.spots`` and 166.06 in ``wspr.rx``.
* The grid columns, by contrast, were clean over the same month: zero empty and
  zero short ``rx_loc`` values in 88 million rows.

The table's own coordinates are still read, but only to compute an agreement
check (``latlon_col_agrees``) and an invalid-row count, both reported as output
columns.
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import NamedTuple

#: A Maidenhead locator of 2, 4, 6 or 8 characters. Case-insensitive: the data
#: contains ``ER60tb``, ``IO91XK`` and ``DM45`` alike.
GRID_RE = re.compile(r"^[A-R]{2}(?:[0-9]{2}(?:[A-X]{2}(?:[0-9]{2})?)?)?$", re.IGNORECASE)

#: Degrees of longitude and latitude spanned by a cell, by locator length.
_CELL_SIZE: dict[int, tuple[float, float]] = {
    2: (20.0, 10.0),
    4: (2.0, 1.0),
    6: (2.0 / 24.0, 1.0 / 24.0),
    8: (2.0 / 240.0, 1.0 / 240.0),
}


class GridPosition(NamedTuple):
    """Decoded Maidenhead locator.

    Attributes
    ----------
    lat, lon : float
        Latitude and longitude of the **cell centre**, in degrees.
    precision : int
        Locator length actually used (2, 4, 6 or 8).
    cell_lon_deg, cell_lat_deg : float
        Cell extent in degrees, i.e. the positional uncertainty. A 4-character
        locator is 1° of latitude tall — about 111 km — which matters when
        plotting stations at high latitude.
    """

    lat: float
    lon: float
    precision: int
    cell_lon_deg: float
    cell_lat_deg: float


def is_valid_grid(grid: str | None) -> bool:
    """Return True if ``grid`` is a syntactically valid 2/4/6/8-character locator."""
    if not grid:
        return False
    text = str(grid).strip()
    return bool(GRID_RE.match(text)) and len(text) in _CELL_SIZE


def decode_grid(grid: str | None) -> GridPosition | None:
    """Decode a Maidenhead locator to the latitude/longitude of its centre.

    Parameters
    ----------
    grid : str or None
        Locator of 2, 4, 6 or 8 characters, any case. Odd lengths and malformed
        locators return ``None`` rather than a guess.

    Returns
    -------
    GridPosition or None

    Examples
    --------
    ``ER60tb`` is VY0ERC at Eureka, Ellesmere Island. The result agrees exactly
    with the ``rx_lat`` maximum of 80.0625 observed in ``wspr.rx``, confirming
    that wsprnet derives its coordinates the same way:

    >>> pos = decode_grid("ER60tb")
    >>> f"{pos.lat:.4f} {pos.lon:.4f}"
    '80.0625 -86.3750'

    ``IB59`` is the 4-character locator of DP0GVN at Neumayer Station,
    Antarctica, whose 1° cell is 111 km tall:

    >>> pos = decode_grid("IB59")
    >>> f"{pos.lat:.4f} {pos.lon:.4f} {pos.cell_lat_deg:.1f}"
    '-70.5000 -9.0000 1.0'

    >>> decode_grid("nonsense") is None
    True
    """
    if not is_valid_grid(grid):
        return None
    text = str(grid).strip()
    length = len(text)

    lon = -180.0
    lat = -90.0

    lon += (ord(text[0].upper()) - ord("A")) * 20.0
    lat += (ord(text[1].upper()) - ord("A")) * 10.0
    if length >= 4:
        lon += int(text[2]) * 2.0
        lat += int(text[3]) * 1.0
    if length >= 6:
        lon += (ord(text[4].upper()) - ord("A")) * (2.0 / 24.0)
        lat += (ord(text[5].upper()) - ord("A")) * (1.0 / 24.0)
    if length >= 8:
        lon += int(text[6]) * (2.0 / 240.0)
        lat += int(text[7]) * (1.0 / 240.0)

    cell_lon, cell_lat = _CELL_SIZE[length]
    return GridPosition(
        lat=lat + cell_lat / 2.0,
        lon=lon + cell_lon / 2.0,
        precision=length,
        cell_lon_deg=cell_lon,
        cell_lat_deg=cell_lat,
    )


def is_valid_latlon(lat: float | None, lon: float | None) -> bool:
    """Return True if ``lat``/``lon`` are finite and physically in range.

    Catches both the ``-999`` sentinel used by ``wsprdaemon.spots`` and the
    out-of-range latitudes present in both tables.
    """
    if lat is None or lon is None:
        return False
    try:
        lat_f = float(lat)
        lon_f = float(lon)
    except (TypeError, ValueError):
        return False
    if not (math.isfinite(lat_f) and math.isfinite(lon_f)):
        return False
    return -90.0 <= lat_f <= 90.0 and -180.0 <= lon_f <= 180.0


def great_circle_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km on a spherical Earth (R = 6371.0 km)."""
    radius = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * radius * math.asin(min(1.0, math.sqrt(a)))


def hemisphere(lat: float | None) -> str:
    """Return ``'N'``, ``'S'`` or ``''`` for an unusable latitude."""
    if lat is None or not math.isfinite(float(lat)):
        return ""
    return "N" if float(lat) >= 0 else "S"


# ------------------------------------------------------- geomagnetic (optional)


def aacgm_available() -> bool:
    """Return True if :mod:`aacgmv2` can be imported.

    Geomagnetic latitude is optional because ``aacgmv2`` needs a compiler and
    the IGRF coefficient files. Install with ``pip install
    'wspr-station-census[geomag]'``.
    """
    try:
        import aacgmv2  # noqa: F401
    except Exception:
        return False
    return True


def aacgm_latitudes(
    lats: list[float | None],
    lons: list[float | None],
    epoch: str,
    height_km: float = 0.0,
) -> list[float | None]:
    """Convert geographic to AACGM-v2 geomagnetic latitude.

    For a polar study this is the coordinate that matters: geographic latitude
    does not tell you whether a station sits inside the auroral oval. The
    difference is not academic — at the census mid-period epoch, VY0ERC at
    geographic 80.06° N is CGM 86.5° (deep polar cap) while DP0GVN at −70.65°
    is only CGM −60.7°, equatorward of the typical auroral oval. A
    geographic-latitude map would present those two stations as comparably
    "polar".

    Two things make this fast enough to run over the whole census. The
    conversion is done with :func:`aacgmv2.convert_latlon_arr`, which is
    vectorised, rather than per-station calls. And it is evaluated only on
    **unique coordinate pairs**: stations are located by Maidenhead cell, so
    hundreds of thousands of transmitters collapse to far fewer distinct
    positions. Element-wise calls over 600,000 transmitters take many minutes;
    this takes seconds.

    Parameters
    ----------
    lats, lons : list of float or None
        Geographic coordinates in degrees. Unusable entries yield ``None``.
    epoch : str
        ``YYYY-MM-DD`` date at which to evaluate the model. AACGM coefficients
        are time dependent; a single mid-period epoch is used for the whole
        census and is recorded in the run manifest.
    height_km : float, optional
        Altitude for the conversion. Ground stations: 0.

    Returns
    -------
    list of float or None
        Geomagnetic latitude in degrees, in the same order as the input,
        ``None`` where the conversion failed or the input was unusable.

    Raises
    ------
    ImportError
        If :mod:`aacgmv2` is not installed.
    """
    import aacgmv2  # noqa: PLC0415  -- optional dependency, imported on demand
    import numpy as np  # noqa: PLC0415

    when = datetime.strptime(epoch, "%Y-%m-%d")

    # Collapse to unique positions before calling the model.
    keys: list[tuple[float, float] | None] = [
        (round(float(lat), 6), round(float(lon), 6)) if is_valid_latlon(lat, lon) else None
        for lat, lon in zip(lats, lons, strict=True)
    ]
    unique = sorted({key for key in keys if key is not None})
    if not unique:
        return [None] * len(keys)

    lookup: dict[tuple[float, float], float | None] = {}
    try:
        mlat, _mlon, _r = aacgmv2.convert_latlon_arr(
            np.array([key[0] for key in unique]),
            np.array([key[1] for key in unique]),
            np.full(len(unique), height_km),
            when,
            method_code="G2A",
        )
        for key, value in zip(unique, np.asarray(mlat, dtype=float), strict=True):
            lookup[key] = None if not math.isfinite(value) else round(float(value), 6)
    except Exception:  # pragma: no cover - model or coefficient-file failure
        return [None] * len(keys)

    return [None if key is None else lookup.get(key) for key in keys]
