"""Polar stereographic maps of WSPR station locations.

Consumes the summary CSVs written by the ``wspr-station-census`` sub-project and
draws one map per hemisphere showing where transmitters and receivers actually
were over the study period.

Three decisions are baked in here because getting them wrong makes the figure
misleading rather than merely ugly.

**Stations are collapsed to sites.** ``HB9VQQ/RS``, ``/RL`` and ``/KE`` are three
callsigns at one antenna field in JN47kh; ``W7WKR``, ``W7WKR-1``, ``w7wkr-2`` and
``W7WKR-K1`` are one station in Washington. Plotting callsigns would draw a
cluster where there is one dot's worth of ground truth, over-weighting
multi-receiver sites exactly where they are densest.

**Transmitters are filtered by quality tier, and the filter is stated on the
figure.** An unfiltered transmitter map is fiction: for June 2026 the raw data
places 32,757 transmitters above 70° absolute latitude, split almost evenly
between hemispheres, from balloon-telemetry callsigns carrying fabricated grid
squares. The census exposes ``quality_tier`` for exactly this purpose. Rejected
stations are not silently dropped — the count that was excluded is printed in the
caption, so a reader can see the filter rather than trust it.

**Geomagnetic latitude is drawn, not just geographic.** For high-latitude
propagation the auroral oval is the relevant boundary, and it does not follow
geographic latitude. At the census epoch VY0ERC sits at geographic 80.1° N but
CGM 86.5° (deep polar cap), while DP0GVN at −70.6° is only CGM −60.7°,
equatorward of the typical oval. Over the whole census, 234 receivers are
poleward of 60° geographic but equatorward of 60° CGM, and 35 are the reverse. A
geographic-only map would misrepresent which stations are magnetically polar.

**WsprDaemon receivers are a separate series.** They are the only sites carrying a
calibrated per-spot noise measurement, so whether a receiver reports extended
spots decides what science it can support — an ordinary WSPRNet receiver gives an
SNR with no independent noise term. They are also rare (~400 sites of ~22,000),
so they are drawn last, enlarged, and in their own hue.

Colours come from the ``dataviz`` skill's reference palette, slots 1-3, which pass
its all-pairs gates (the applicable pairlist for a scatter form) with worst CVD
ΔE 9.2 and worst normal-vision ΔE 24.0 in light mode. Slot 3 (aqua) measures
2.74:1 against the light surface, below the 3:1 gate, so the relief rule applies:
the WsprDaemon marker carries a dark outline, a distinct shape, a legend entry
and a table view in the notebook. Every series differs in shape as well as hue,
so identity never rests on colour alone.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

Hemisphere = Literal["N", "S"]

# ---------------------------------------------------------------------- palette
# dataviz reference palette, light mode. Slots 1-2 only: a scatter map uses the
# all-pairs pairlist, where the documented palette caps at three slots.
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#8a8981"
RX_COLOR = "#2a78d6"  # slot 1, blue   -- receiver, WSPRNet only
TX_COLOR = "#eb6834"  # slot 2, orange -- transmitter
WD_COLOR = "#1baf7a"  # slot 3, aqua   -- receiver reporting WsprDaemon extended spots
#: Aqua measures 2.74:1 against the light surface, under the 3:1 gate, so the
#: WsprDaemon marker carries a dark outline and a distinct shape to compensate.
WD_EDGE = "#0f6b4a"
#: WsprDaemon sites are enlarged: they are the scientifically distinguished
#: population (calibrated per-spot noise) and number only ~400 of ~22,000.
WD_SIZE_SCALE = 1.45

#: Dark-mode steps for the same two hues, should a dark figure ever be wanted.
#: Validated as a pair against the dark surface (#1a1a19): CVD ΔE 26.8.
RX_COLOR_DARK = "#3987e5"
TX_COLOR_DARK = "#d95926"

#: Quality tiers in descending confidence. Rendered by size and opacity — an
#: ordinal property must not be encoded as hue.
TIER_ORDER = ("A", "B", "C", "D", "rejected")
TIER_RANK = {tier: rank for rank, tier in enumerate(TIER_ORDER)}

#: (marker area in points^2, alpha) per tier. Kept small deliberately: a
#: full-hemisphere map carries ~13,000 receiver sites and ~7,000 transmitter
#: sites, and marks sized for a sparse plot merge into solid colour at
#: mid-latitudes, hiding the high-latitude stations the study is about.
TIER_STYLE: dict[str, tuple[float, float]] = {
    "A": (26.0, 0.85),
    "B": (12.0, 0.60),
    "C": (6.0, 0.40),
    "D": (3.5, 0.28),
}

#: Transmitters are drawn smaller and fainter than receivers of the same tier,
#: and underneath them. Both choices are deliberate: the receiving network is the
#: instrument this study is about, transmitters outnumber it several-fold, and
#: transmitter positions are the less trustworthy of the two (they are
#: self-reported by stations the census cannot cross-check as thoroughly).
TX_SIZE_SCALE = 0.62
TX_ALPHA_SCALE = 0.72

#: WSPRSondes reuse the transmitter hue rather than taking a fourth categorical
#: slot. That is both a palette constraint and the semantically correct choice: a
#: WSPRSonde *is* a transmitter, just a controlled one, so it reads as a
#: subcategory distinguished by shape and outline rather than as a new kind of
#: thing. (The documented palette caps categorical hues at three for an
#: all-pairs form like a scatter map; its fourth slot fails the all-pairs floors
#: against the orange already in use.)
SONDE_COLOR = TX_COLOR
SONDE_EDGE = INK
SONDE_SIZE = 210.0

#: Stations to label directly on the map. Selective direct labelling only — a
#: label on every point would be unreadable at these densities.
HIGHLIGHT = ("DP0GVN", "VY0ERC")

#: Default WSPRSonde record statuses to draw. ``pending_shipment`` and
#: ``in_transit`` are excluded deliberately: those rows describe installations
#: that have not happened, several at city-level 4-character precision, and the
#: upstream README is explicit that they must not be drawn as though deployed.
SONDE_STATUSES = ("deployed",)


def summary_dir(explicit: str | Path | None = None) -> Path:
    """Locate a census summary directory.

    Parameters
    ----------
    explicit : str or pathlib.Path, optional
        Use this directory if given.

    Returns
    -------
    pathlib.Path

    Raises
    ------
    FileNotFoundError
        If no summary directory can be found, with the expected layout named.
    """
    if explicit is not None:
        path = Path(explicit).expanduser()
        if not path.is_dir():
            raise FileNotFoundError(f"no such summary directory: {path}")
        return path
    root = Path(os.environ.get("POLAR_PSWS_DATA_DIR", Path.home() / "polar_psws_data"))
    candidates = sorted((root / "wspr_station_census").glob("*/summary"))
    if not candidates:
        raise FileNotFoundError(
            f"no census output under {root / 'wspr_station_census'}. "
            "Run `wspr-census run` first."
        )
    return candidates[-1]


def load_census(directory: str | Path | None = None) -> dict[str, pd.DataFrame]:
    """Load the census summary CSVs.

    Parameters
    ----------
    directory : str or pathlib.Path, optional
        Summary directory; discovered from ``$POLAR_PSWS_DATA_DIR`` if omitted.

    Returns
    -------
    dict
        Table name to frame, plus ``_dir`` giving the resolved path.
    """
    path = summary_dir(directory)
    names = (
        "rx_stations",
        "tx_stations",
        "rx_station_band",
        "tx_station_band",
        "rx_station_month",
        "tx_station_month",
        "wd_receivers",
        "population",
    )
    out: dict[str, pd.DataFrame] = {}
    for name in names:
        csv = path / f"{name}.csv"
        if csv.exists():
            out[name] = pd.read_csv(csv)
    out["_dir"] = path  # type: ignore[assignment]
    return out


#: Vendored WSPRSonde snapshot, relative to the repository root.
SONDE_RELPATH = Path("wsprsonde-stations") / "wsprsonde_locations.csv"


def load_wsprsondes(path: str | Path | None = None) -> pd.DataFrame:
    """Load the vendored WSPRSonde network snapshot.

    WSPRSondes are the *controlled* transmitters in the WSPR population:
    8-band, GPS-disciplined, ~1 W per band, transmitting continuously from a
    known position. Everything else on these maps is an uncontrolled transmitter
    whose power, frequency and grid are self-reported — which is why the census
    needs quality tiers at all. Overlaying the sondes marks the reference points.

    Parameters
    ----------
    path : str or pathlib.Path, optional
        CSV to read. Defaults to ``wsprsonde-stations/wsprsonde_locations.csv``
        at the repository root.

    Returns
    -------
    pandas.DataFrame
        Empty if the snapshot is absent, so the maps still build without it.
    """
    if path is None:
        path = Path(__file__).resolve().parents[2] / SONDE_RELPATH
    path = Path(path).expanduser()
    if not path.is_file():
        return pd.DataFrame()
    return pd.read_csv(path)


def sonde_sites(
    sondes: pd.DataFrame,
    statuses: tuple[str, ...] = SONDE_STATUSES,
) -> pd.DataFrame:
    """Collapse WSPRSonde units to one row per site.

    Grouped by ``site_id`` rather than ``call``, because WB6CXC runs two WS-8
    units at one location in CM88mj and they are one dot's worth of ground truth.

    Parameters
    ----------
    sondes : pandas.DataFrame
        Output of :func:`load_wsprsondes`.
    statuses : tuple of str, optional
        ``record_status`` values to keep. See :data:`SONDE_STATUSES` for why
        pending and in-transit units are excluded by default.

    Returns
    -------
    pandas.DataFrame
        Columns ``site_id``, ``call``, ``grid``, ``grid_precision``, ``lat``,
        ``lon``, ``on_air_status``, ``n_units``.
    """
    if sondes.empty:
        return pd.DataFrame(
            columns=["site_id", "call", "grid", "grid_precision", "lat", "lon",
                     "on_air_status", "n_units"]
        )
    work = sondes[sondes["record_status"].isin(statuses)].copy()
    work = work.dropna(subset=["lat", "lon"])
    if work.empty:
        return pd.DataFrame(
            columns=["site_id", "call", "grid", "grid_precision", "lat", "lon",
                     "on_air_status", "n_units"]
        )
    # A site counts as active if any unit there is being heard.
    rank = {"active": 0, "intermittent": 1, "silent": 2}
    work["_rank"] = work["on_air_status"].map(rank).fillna(3)
    best = work.sort_values("_rank").drop_duplicates("site_id")
    units = work.groupby("site_id")["station_id"].nunique().rename("n_units")
    out = best.set_index("site_id").join(units)
    keep = ["call", "grid", "grid_precision", "lat", "lon", "on_air_status", "n_units"]
    return out[keep].reset_index()


# ------------------------------------------------------------------ aggregation


def to_sites(
    stations: pd.DataFrame,
    role: Literal["rx", "tx"],
    tiers: tuple[str, ...] | None = None,
) -> pd.DataFrame:
    """Collapse per-callsign rows to one row per physical site.

    Position, tier and grid are taken from the site's **highest-spot-count**
    callsign rather than averaged: averaging coordinates across a site's
    callsigns would invent a location between them, and averaging tiers is
    meaningless. Spot counts are summed.

    Parameters
    ----------
    stations : pandas.DataFrame
        ``rx_stations`` or ``tx_stations``.
    role : {'rx', 'tx'}
    tiers : tuple of str, optional
        Keep only these ``quality_tier`` values. ``None`` keeps everything.

    Returns
    -------
    pandas.DataFrame
        One row per site, with ``site``, ``lat``, ``lon``, ``cgm_lat``,
        ``quality_tier``, ``n_spots``, ``n_callsigns``, ``n_days_active``.
    """
    sign_col = f"{role}_sign"
    site_col = "site_call" if role == "rx" else "base_call"
    spots_col = "n_spots_wsprnet"

    work = stations.copy()
    if tiers is not None:
        work = work[work["quality_tier"].isin(tiers)]
    work = work.dropna(subset=["lat", "lon"])
    if work.empty:
        return pd.DataFrame(
            columns=["site", "lat", "lon", "cgm_lat", "quality_tier", "n_spots",
                     "n_callsigns", "n_days_active"]
        )

    work["_spots"] = pd.to_numeric(work.get(spots_col), errors="coerce").fillna(0)
    work["_rank"] = work["quality_tier"].map(TIER_RANK).fillna(len(TIER_ORDER))
    work[site_col] = work[site_col].fillna(work[sign_col])

    # Representative row per site: best tier first, then most spots.
    best = (
        work.sort_values(["_rank", "_spots"], ascending=[True, False])
        .drop_duplicates(site_col)
        .set_index(site_col)
    )
    totals = work.groupby(site_col).agg(
        n_spots=("_spots", "sum"),
        n_callsigns=(sign_col, "nunique"),
        n_days_active=("n_days_active", "max"),
    )
    out = pd.DataFrame(
        {
            "site": best.index,
            "lat": best["lat"].to_numpy(),
            "lon": best["lon"].to_numpy(),
            "cgm_lat": best.get("cgm_lat", pd.Series(np.nan, index=best.index)).to_numpy(),
            "quality_tier": best["quality_tier"].to_numpy(),
            "grid": best["modal_grid"].to_numpy(),
        }
    ).set_index("site")

    # WsprDaemon status is a property of the *site*, so a site counts as a
    # WsprDaemon site if any of its callsigns appears in wsprdaemon.spots. Taking
    # it from the representative callsign alone would miss a site whose busiest
    # receiver reports only to wsprnet while a sibling receiver reports extended
    # spots -- which is exactly the multi-receiver case this table exists to fold.
    if "in_wsprdaemon" in work.columns:
        flag = work["in_wsprdaemon"].astype(str).str.lower().isin({"true", "1"})
        out["is_wsprdaemon"] = flag.groupby(work[site_col]).any().reindex(out.index).fillna(False)
        out["source_class"] = (
            work.groupby(site_col)["source_class"]
            .agg(lambda s: "wsprdaemon_only" if (s == "wsprdaemon_only").all()
                 else ("both" if (s == "both").any() else "wsprnet_only"))
            .reindex(out.index)
        )
    else:
        out["is_wsprdaemon"] = False
        out["source_class"] = "wsprnet_only"
    if "sw_class" in work.columns:
        out["sw_class"] = best["sw_class"].to_numpy()

    out = out.join(totals).reset_index()
    return out.sort_values("n_spots", ascending=False, ignore_index=True)


def in_hemisphere(sites: pd.DataFrame, hemisphere: Hemisphere, lat_limit: float) -> pd.DataFrame:
    """Select sites visible on a hemisphere's map."""
    lat = pd.to_numeric(sites["lat"], errors="coerce")
    return sites[lat >= lat_limit] if hemisphere == "N" else sites[lat <= -lat_limit]


# -------------------------------------------------------------- CGM latitude grid


def cgm_latitude_grid(
    hemisphere: Hemisphere,
    epoch: str,
    lat_limit: float,
    lat_step: float = 1.0,
    lon_step: float = 3.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """Evaluate AACGM-v2 geomagnetic latitude on a lon/lat grid.

    Parameters
    ----------
    hemisphere : {'N', 'S'}
    epoch : str
        ``YYYY-MM-DD``; use the epoch recorded in the census manifest so the
        overlay and the ``cgm_lat`` column agree.
    lat_limit : float
        Equatorward edge of the map, in absolute degrees.
    lat_step, lon_step : float, optional
        Grid resolution in degrees.

    Returns
    -------
    tuple of numpy.ndarray or None
        ``(lon2d, lat2d, cgm_lat2d)``, or ``None`` if :mod:`aacgmv2` is missing
        or the model fails — callers should then simply skip the overlay.
    """
    try:
        import aacgmv2
    except ImportError:
        return None
    from datetime import datetime

    when = datetime.strptime(epoch, "%Y-%m-%d")
    if hemisphere == "N":
        lats = np.arange(lat_limit, 90.0 + lat_step, lat_step)
    else:
        lats = np.arange(-90.0, -lat_limit + lat_step, lat_step)
    lons = np.arange(-180.0, 180.0 + lon_step, lon_step)
    lon2d, lat2d = np.meshgrid(lons, lats)
    try:
        mlat, _mlon, _r = aacgmv2.convert_latlon_arr(
            lat2d.ravel(), lon2d.ravel(), np.zeros(lat2d.size), when, method_code="G2A"
        )
    except Exception:
        return None
    return lon2d, lat2d, np.asarray(mlat, dtype=float).reshape(lat2d.shape)


def l_shell(cgm_lat: float) -> float:
    """Return the dipole L-shell for a geomagnetic latitude.

    Uses the centred-dipole relation ``L = 1 / cos²(Λ)``, where ``Λ`` is the
    invariant (here AACGM) latitude. This is the standard first-order mapping
    from an ionospheric footpoint to the equatorial crossing distance of the
    field line, in Earth radii.

    It is an **approximation**, and deliberately labelled as one on the figure.
    A true McIlwain L computed from a realistic internal field plus external
    contributions differs from the dipole value, increasingly so at high L where
    the field lines reach far enough to be distorted by the magnetopause and the
    tail current. The dipole value is nonetheless the right thing for a
    figure annotation: it is what makes 60° versus 70° geomagnetic latitude
    legible as "L≈4 versus L≈8.5", i.e. inner versus outer magnetosphere.

    Parameters
    ----------
    cgm_lat : float
        Geomagnetic latitude in degrees; the sign is ignored.

    Returns
    -------
    float
        L in Earth radii.

    Examples
    --------
    >>> f"{l_shell(60.0):.2f}"
    '4.00'
    >>> f"{l_shell(-70.0):.2f}"
    '8.55'
    """
    import math

    lam = math.radians(abs(float(cgm_lat)))
    return 1.0 / (math.cos(lam) ** 2)


def format_cgm_label(cgm_lat: float) -> str:
    """Format a contour label as geomagnetic latitude with L-shell in parentheses.

    Examples
    --------
    >>> format_cgm_label(60.0)
    '60° (L 4.0)'
    >>> format_cgm_label(80.0)
    '80° (L 33)'
    """
    value = l_shell(cgm_lat)
    shown = f"{value:.1f}" if value < 10 else f"{value:.0f}"
    return f"{abs(cgm_lat):.0f}° (L {shown})"


def choose_label_longitude(
    avoid_longitudes: list[float],
    candidates: tuple[float, ...] = (150.0, -150.0, 120.0, -120.0, 90.0, -90.0, 180.0),
) -> float:
    """Pick the candidate meridian farthest from every longitude to avoid.

    The geomagnetic contour labels sit on a single meridian. If that meridian
    passes near a station callout box, the innermost label is drawn underneath it
    and vanishes. Choosing the meridian with the greatest minimum angular
    separation from the callouts avoids that without hand-tuning per hemisphere.

    Parameters
    ----------
    avoid_longitudes : list of float
        Longitudes of the directly-labelled stations, degrees east.
    candidates : tuple of float, optional
        Meridians to choose between.

    Returns
    -------
    float
        The chosen meridian, degrees east.

    Examples
    --------
    >>> choose_label_longitude([-86.4])          # VY0ERC on the northern map
    90.0
    >>> choose_label_longitude([])
    150.0
    """
    if not avoid_longitudes:
        return candidates[0]

    def separation(candidate: float) -> float:
        return min(
            abs((candidate - lon + 180.0) % 360.0 - 180.0) for lon in avoid_longitudes
        )

    return max(candidates, key=separation)


def cgm_meridian_crossings(
    hemisphere: Hemisphere,
    epoch: str,
    levels: tuple[float, ...],
    longitude: float,
    lat_step: float = 0.1,
) -> dict[float, float]:
    """Find the geographic latitude where each CGM level crosses one meridian.

    Used to place a single, correctly-matched label per geomagnetic-latitude
    contour. Solving along a meridian is exact and deterministic, unlike matching
    a label position to the nearest contour, which fails on these eccentric
    contours.

    Parameters
    ----------
    hemisphere : {'N', 'S'}
    epoch : str
        ``YYYY-MM-DD``.
    levels : tuple of float
        Absolute CGM latitudes to locate.
    longitude : float
        Meridian to solve along, degrees east.
    lat_step : float, optional
        Search resolution in degrees.

    Returns
    -------
    dict
        ``{absolute level: geographic latitude}``, omitting levels that do not
        cross this meridian within the hemisphere.
    """
    try:
        import aacgmv2
    except ImportError:
        return {}
    from datetime import datetime

    when = datetime.strptime(epoch, "%Y-%m-%d")
    sign = 1 if hemisphere == "N" else -1
    lats = (
        np.arange(20.0, 90.0, lat_step) if hemisphere == "N"
        else np.arange(-89.9, -20.0, lat_step)
    )
    try:
        mlat, _mlon, _r = aacgmv2.convert_latlon_arr(
            lats, np.full(lats.size, longitude), np.zeros(lats.size), when, method_code="G2A"
        )
    except Exception:
        return {}
    mlat = np.asarray(mlat, dtype=float)
    good = np.isfinite(mlat)
    if not good.any():
        return {}
    lats, mlat = lats[good], mlat[good]

    out: dict[float, float] = {}
    for level in levels:
        target = sign * abs(level)
        idx = int(np.argmin(np.abs(mlat - target)))
        # Reject when the meridian never actually reaches this level.
        if abs(mlat[idx] - target) <= 0.5:
            out[abs(level)] = float(lats[idx])
    return out


def cgm_label_positions(
    hemisphere: Hemisphere,
    epoch: str,
    levels: tuple[float, ...],
    preferred_longitude: float,
    fallbacks: tuple[float, ...] = (150.0, -150.0, 120.0, -120.0, 90.0, -90.0, 180.0, 60.0),
    spread_deg: float = 24.0,
) -> dict[float, tuple[float, float]]:
    """Place one label per geomagnetic-latitude level, fanned across meridians.

    Two failure modes are avoided here.

    A single meridian does not necessarily cross every level: the geomagnetic
    poles are offset from the geographic ones, so along some meridians the
    geomagnetic latitude never reaches 80 degrees. Each level the assigned
    meridian misses is retried on the fallbacks.

    And putting every label on *one* meridian stacks them along a single radial
    line. On a polar stereographic projection that line can render horizontally,
    so five labels of the form "60 deg (L 4.0)" abut and read as one run-on
    string. Successive levels are therefore fanned by ``spread_deg`` about the
    preferred meridian, which separates them azimuthally as well as radially.

    Parameters
    ----------
    hemisphere : {'N', 'S'}
    epoch : str
        ``YYYY-MM-DD``.
    levels : tuple of float
        Absolute geomagnetic latitudes to label.
    preferred_longitude : float
        Meridian to use where possible, keeping the labels aligned.
    fallbacks : tuple of float, optional
        Meridians to try for levels the preferred one misses.

    Returns
    -------
    dict
        ``{absolute level: (longitude, geographic latitude)}``, omitting only
        levels that no candidate meridian crosses.
    """
    out: dict[float, tuple[float, float]] = {}
    ordered = sorted(levels)
    centre = (len(ordered) - 1) / 2.0
    for index, level in enumerate(ordered):
        assigned = preferred_longitude + (index - centre) * spread_deg
        assigned = (assigned + 180.0) % 360.0 - 180.0
        for longitude in (assigned, preferred_longitude, *fallbacks):
            found = cgm_meridian_crossings(hemisphere, epoch, (level,), longitude)
            if level in found:
                out[level] = (longitude, found[level])
                break
    return out


# ------------------------------------------------------------------------ figure


@dataclass
class MapSpec:
    """Parameters for one hemisphere map.

    Parameters
    ----------
    hemisphere : {'N', 'S'}
    lat_limit : float
        Equatorward edge, absolute degrees. The default of 22 deg is set so the
        northern map contains the whole of CONUS -- Key West, the southernmost
        point, is at 24.55 deg N -- with a small margin, and is applied
        symmetrically to the southern map so the two are directly comparable.
        The cost is a smaller polar cap: a polar stereographic projection
        compresses toward its edge, so extending the extent shrinks the region
        of interest within the disc.
    epoch : str
        AACGM epoch, from the census manifest.
    rx_tiers, tx_tiers : tuple of str
        Quality tiers to draw. The transmitter default of ``('A', 'B')`` is the
        documented recommendation; ``'rejected'`` must never be included on a
        geographic plot.
    cgm_levels : tuple of float
        Absolute CGM latitudes to contour. Each is labelled with its dipole
        L-shell in parentheses; see :func:`l_shell`.
    sonde_statuses : tuple of str
        WSPRSonde ``record_status`` values to draw; see :data:`SONDE_STATUSES`.
    sonde_encode_on_air : bool, optional
        Encode ``on_air_status`` as marker fill, hollow for anything not
        ``active``. LOCAL DELTA vs HamSCI/polar-psws — see analysis/README.md.
        ``False`` draws every deployed unit filled and drops the corresponding
        legend entry and caption clause.
    label_longitude : float or None
        Meridian along which the geomagnetic contour labels are placed. ``None``
        (the default) chooses it automatically, as the candidate meridian
        farthest from the directly-labelled stations -- otherwise the innermost
        contour label lands underneath a station's callout box and silently
        disappears, which is what happened to the 80 deg label on the northern
        map once the extent was widened.
    """

    hemisphere: Hemisphere
    lat_limit: float = 22.0
    epoch: str = "2025-12-15"
    rx_tiers: tuple[str, ...] = ("A", "B", "C", "D")
    tx_tiers: tuple[str, ...] = ("A", "B")
    cgm_levels: tuple[float, ...] = (40.0, 50.0, 60.0, 70.0, 80.0)
    label_longitude: float | None = None
    sonde_statuses: tuple[str, ...] = SONDE_STATUSES
    sonde_encode_on_air: bool = True
    highlight: tuple[str, ...] = field(default_factory=lambda: HIGHLIGHT)


def _tier_style(tier: str, role: Literal["rx", "tx"]) -> tuple[float, float]:
    """Return the (size, alpha) for one tier and role.

    Transmitters get :data:`TX_SIZE_SCALE` / :data:`TX_ALPHA_SCALE` applied, so
    the denser and less trustworthy population recedes behind the receiving
    network. The legend uses this same function, so it always matches the marks.
    """
    size, alpha = TIER_STYLE.get(str(tier), (3.5, 0.28))
    if role == "tx":
        return size * TX_SIZE_SCALE, alpha * TX_ALPHA_SCALE
    return size, alpha


def _tier_arrays(
    sites: pd.DataFrame, role: Literal["rx", "tx"]
) -> tuple[np.ndarray, np.ndarray]:
    """Return per-row marker sizes and alphas from ``quality_tier``."""
    styles = [_tier_style(tier, role) for tier in sites["quality_tier"]]
    return (
        np.array([s for s, _ in styles], dtype=float),
        np.array([a for _, a in styles], dtype=float),
    )


def draw_map(
    ax,
    rx_sites: pd.DataFrame,
    tx_sites: pd.DataFrame,
    spec: MapSpec,
    sondes: pd.DataFrame | None = None,
):
    """Draw one hemisphere map onto a cartopy axes.

    Parameters
    ----------
    ax : cartopy.mpl.geoaxes.GeoAxes
        Axes created with :class:`cartopy.crs.NorthPolarStereo` or
        :class:`~cartopy.crs.SouthPolarStereo`.
    rx_sites, tx_sites : pandas.DataFrame
        Output of :func:`to_sites`, already restricted to this hemisphere.
    spec : MapSpec
    sondes : pandas.DataFrame, optional
        Output of :func:`sonde_sites`, already restricted to this hemisphere.

    Returns
    -------
    dict
        Counts actually plotted, for the caption.
    """
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
    import matplotlib.path as mpath

    plate = ccrs.PlateCarree()
    sign = 1 if spec.hemisphere == "N" else -1
    if spec.hemisphere == "N":
        ax.set_extent([-180, 180, spec.lat_limit, 90], plate)
    else:
        ax.set_extent([-180, 180, -90, -spec.lat_limit], plate)

    # Circular boundary: a square frame on a polar projection wastes a third of
    # the panel and implies coverage where the projection has none.
    theta = np.linspace(0, 2 * np.pi, 200)
    ax.set_boundary(mpath.Path(np.c_[0.5 + 0.5 * np.sin(theta), 0.5 + 0.5 * np.cos(theta)]),
                    transform=ax.transAxes)

    # Recessive basemap: geography is context, not the data.
    ax.add_feature(cfeature.OCEAN, facecolor="#f5f5f1", edgecolor="none", zorder=0)
    ax.add_feature(cfeature.LAND, facecolor="#dcd9cd", edgecolor="none", zorder=0)
    ax.add_feature(cfeature.COASTLINE, edgecolor="#a9a89f", linewidth=0.5, zorder=1)

    from matplotlib.ticker import FixedLocator

    gl = ax.gridlines(
        draw_labels=False, linewidth=0.4, color="#cfcec7", alpha=1.0, linestyle=":", zorder=2
    )
    gl.ylocator = FixedLocator([sign * v for v in (30, 40, 50, 60, 70, 80)])

    # Resolve the contour-label meridian before drawing, from the stations that
    # will get callout boxes, so a label is never hidden beneath one.
    highlight_lons = [
        float(row["lon"])
        for frame in (rx_sites, tx_sites)
        for _, row in frame[frame["site"].isin(spec.highlight)].iterrows()
    ]
    label_lon = (
        spec.label_longitude
        if spec.label_longitude is not None
        else choose_label_longitude(highlight_lons)
    )

    # Geomagnetic latitude overlay.
    grid = cgm_latitude_grid(spec.hemisphere, spec.epoch, spec.lat_limit)
    if grid is not None:
        lon2d, lat2d, cgm = grid
        levels = sorted(sign * level for level in spec.cgm_levels)
        with np.errstate(invalid="ignore"):
            ax.contour(
                lon2d, lat2d, cgm, levels=levels, transform=plate,
                colors=INK_MUTED, linewidths=0.9, linestyles="--", zorder=3,
            )
        # Labels are placed by solving for where each CGM level crosses a chosen
        # meridian, rather than with clabel's `manual` positions. The CGM
        # contours are eccentric -- the geomagnetic poles are offset from the
        # geographic ones -- so nearest-contour matching mislabels them: it
        # produced two "80" labels and dropped "60" entirely on the southern map.
        for level, (lon_at, lat_at) in cgm_label_positions(
            spec.hemisphere, spec.epoch, spec.cgm_levels, label_lon
        ).items():
            ax.text(
                lon_at, lat_at, format_cgm_label(level),
                transform=plate, fontsize=7.5, color=INK_SECONDARY, zorder=8.5,
                ha="center", va="center",
                bbox={"boxstyle": "round,pad=0.18", "facecolor": SURFACE,
                      "edgecolor": "none", "alpha": 0.93},
            )

    counts = {}
    # Transmitters are drawn *beneath* receivers. There are far more of them, and
    # receivers are the scarce, instrumented population this study is about; the
    # other order buries them.
    # WsprDaemon receivers are split into their own series and drawn last: they
    # are the only sites carrying a calibrated per-spot noise measurement, so
    # "which receivers are WsprDaemon receivers" is the question the figure most
    # needs to answer at a glance. They are also rare (~400 of 22,000), so they
    # must sit on top or the mid-latitude stipple swallows them.
    wd_mask = (
        rx_sites["is_wsprdaemon"].fillna(False).astype(bool)
        if "is_wsprdaemon" in rx_sites.columns
        else pd.Series(False, index=rx_sites.index)
    )
    rx_plain, rx_wd = rx_sites[~wd_mask], rx_sites[wd_mask]

    for sites, color, marker, key, zorder, edge in (
        (tx_sites, TX_COLOR, "^", "tx", 5, SURFACE),
        (rx_plain, RX_COLOR, "o", "rx", 6, SURFACE),
        # A dark edge, not a surface-coloured one: aqua sits at 2.74:1 against
        # this surface, below the 3:1 gate, so the outline carries the contrast
        # the fill cannot. Shape differs too, so identity never rests on hue.
        (rx_wd, WD_COLOR, "D", "rx_wd", 7, WD_EDGE),
    ):
        counts[key] = len(sites)
        if sites.empty:
            continue
        sizes, alphas = _tier_arrays(sites, "tx" if key == "tx" else "rx")
        if key == "rx_wd":
            sizes = sizes * WD_SIZE_SCALE
            alphas = np.minimum(1.0, alphas + 0.10)
        ax.scatter(
            sites["lon"], sites["lat"], s=sizes, c=color, marker=marker,
            alpha=alphas, linewidths=0.9 if key == "rx_wd" else 0.5, edgecolors=edge,
            transform=plate, zorder=zorder,
        )
    counts["rx_total"] = counts["rx"] + counts["rx_wd"]

    # WSPRSondes: a handful of controlled reference transmitters, so they must sit
    # above the census stipple to be findable. on_air_status is encoded by fill --
    # a deployed-but-unheard unit is a work item, not a measurement, and a filled
    # marker would assert otherwise.
    #
    # LOCAL DELTA vs HamSCI/polar-psws -- see analysis/README.md. Sondes stay on
    # top, but any WsprDaemon receiver at the same site is re-drawn above the star
    # (below). A site can be both, and DP0GVN is: the 210 pt star covered its
    # 38 pt diamond and hid the fact that Neumayer III is one of only nine
    # WsprDaemon receivers in the southern hemisphere. Dropping the star beneath
    # the receiver layer was tried first and is worse -- in the dense North
    # American clusters the star is then buried by *neighbouring* diamonds, not
    # just its own.
    counts["sonde"] = 0
    counts["sonde_silent"] = 0
    if sondes is not None and not sondes.empty:
        heard = sondes["on_air_status"].eq("active")
        if not spec.sonde_encode_on_air:
            heard = pd.Series(True, index=sondes.index)
        for subset, filled in ((sondes[heard], True), (sondes[~heard], False)):
            if subset.empty:
                continue
            ax.scatter(
                subset["lon"], subset["lat"], s=SONDE_SIZE, marker="*",
                facecolors=SONDE_COLOR if filled else "none",
                edgecolors=SONDE_EDGE, linewidths=1.1,
                transform=plate, zorder=8,
            )
        counts["sonde"] = int(len(sondes))
        counts["sonde_silent"] = int((~heard).sum())

        # Sites that are both a WSPRSonde and a WsprDaemon receiver: re-draw the
        # diamond over the star so neither role is lost. The star is ~5x the area
        # of the diamond, so its points still read around it.
        if not rx_wd.empty and "call" in sondes.columns:
            calls = {str(c).upper() for c in sondes["call"].dropna()}
            both = rx_wd[rx_wd["site"].astype(str).str.upper().isin(calls)]
            if not both.empty:
                sizes, alphas = _tier_arrays(both, "rx")
                ax.scatter(
                    both["lon"], both["lat"], s=sizes * WD_SIZE_SCALE, c=WD_COLOR,
                    marker="D", alpha=np.minimum(1.0, alphas + 0.10), linewidths=0.9,
                    edgecolors=WD_EDGE, transform=plate, zorder=9,
                )
                counts["sonde_and_wd"] = int(len(both))

    # Selective direct labels for the polar PSWS stations. Deduplicated across
    # roles, preferring the receiver row: these are receiving instruments, and
    # their receive-side locator is the better-resolved one. VY0ERC reports
    # ER60tb (6-character) receiving but EQ79 (4-character) transmitting, which
    # straddles the 80 degree field boundary -- about 63 km apart. Labelling both
    # would draw two overlapping boxes for one station.
    labelled: set[str] = set()
    for frame in (rx_sites, tx_sites):
        for _, row in frame[frame["site"].isin(spec.highlight)].iterrows():
            if row["site"] in labelled:
                continue
            labelled.add(row["site"])
            ax.scatter(
                row["lon"], row["lat"], s=150, facecolors="none", edgecolors=INK,
                linewidths=1.3, transform=plate, zorder=9,
            )
            # Offset the callout radially outward, away from the pole. Near the
            # pole every meridian converges, so a fixed up-right offset lands on
            # top of the innermost geomagnetic labels no matter which meridian
            # they are fanned onto.
            px, py = ax.projection.transform_point(row["lon"], row["lat"], plate)
            ox, oy = ax.projection.transform_point(0.0, sign * 90.0, plate)
            dx, dy = px - ox, py - oy
            norm = (dx * dx + dy * dy) ** 0.5
            if norm < 1.0:  # station sits essentially on the pole
                dx, dy, norm = 0.0, -1.0, 1.0
            offset = (34.0 * dx / norm, 34.0 * dy / norm)
            ax.annotate(
                f"{row['site']}  {row['grid']}",
                xy=(row["lon"], row["lat"]), xycoords=plate._as_mpl_transform(ax),
                xytext=offset, textcoords="offset points",
                ha="center" if abs(dx) < abs(dy) else ("left" if dx > 0 else "right"),
                va="center" if abs(dx) >= abs(dy) else ("bottom" if dy > 0 else "top"),
                fontsize=8, fontweight="semibold", color=INK, zorder=10,
                arrowprops={"arrowstyle": "-", "color": INK, "linewidth": 0.8,
                            "shrinkA": 0, "shrinkB": 7},
                bbox={"boxstyle": "round,pad=0.3", "facecolor": SURFACE,
                      "edgecolor": "#c9c8c1", "linewidth": 0.6, "alpha": 0.95},
            )
    counts["labelled"] = len(labelled)
    return counts


def rx_tier_rules(period_days: int) -> dict[str, str]:
    """Return the receiver tier definitions as short, figure-ready strings.

    The tier-A threshold is *relative* — half the study period — so it must be
    derived from ``period_days`` rather than hard-coded, or the legend will lie
    on any period other than the one it was written for.

    Parameters
    ----------
    period_days : int
        Length of the census period in days.

    Returns
    -------
    dict
        ``{tier: rule text}``.

    Examples
    --------
    >>> rx_tier_rules(395)["A"]
    'active ≥198 d (½ period)'
    """
    half = round(0.5 * period_days)
    return {
        "A": f"active ≥{half} d (½ period)",
        "B": "active ≥30 d",
        "C": "active ≥7 d",
        "D": "active <7 d",
    }


#: Transmitter tier definitions. Fixed thresholds, so no period dependence.
TX_TIER_RULES: dict[str, str] = {
    "A": "B + ≥10 d, one grid ≥90% of spots, ≤3 power values",
    "B": "bar + ≥3 d, ≤3 grids, amateur/hashed callsign",
    "C": "passes bar, fails a plausibility test",
    "rejected": "fails bar (≥10 spots, ≥2 rx, ≥2 d, valid grid) or telemetry callsign",
}


def tier_legend_handles(
    role: Literal["rx", "tx"],
    tiers: tuple[str, ...],
    rules: dict[str, str] | None = None,
):
    """Build legend handles showing what the tier size ramp means.

    Parameters
    ----------
    role : {'rx', 'tx'}
    tiers : tuple of str
        Tiers to include, in display order.
    rules : dict, optional
        ``{tier: rule text}``; when given, each label states the threshold rather
        than only naming the tier, so the figure is readable without the docs.
    """
    from matplotlib.lines import Line2D

    color = RX_COLOR if role == "rx" else TX_COLOR
    marker = "o" if role == "rx" else "^"
    handles = []
    for tier in tiers:
        size, alpha = _tier_style(tier, role)
        rule = (rules or {}).get(tier)
        handles.append(
            Line2D([], [], marker=marker, linestyle="none", color=color, alpha=alpha,
                   markersize=np.sqrt(size), markeredgecolor=SURFACE, markeredgewidth=0.6,
                   label=f"{tier} — {rule}" if rule else f"tier {tier}")
        )
    return handles


def sonde_snapshot_utc(path: str | Path | None = None) -> str | None:
    """Return ``generated_utc`` from the WSPRSonde snapshot manifest.

    The upstream README requires this in any figure caption: ``on_air_status`` is
    a statement about a two-day window ending at a specific instant, and calling a
    station "active" is meaningless without saying when that was measured.
    """
    import json

    if path is None:
        path = (
            Path(__file__).resolve().parents[2]
            / "wsprsonde-stations"
            / "wsprsonde_locations_manifest.json"
        )
    path = Path(path)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text()).get("generated_utc")
    except (OSError, ValueError):
        return None


def caption(
    hemisphere: Hemisphere,
    counts: dict[str, int],
    spec: MapSpec,
    tx_rejected: int,
    period: str,
    sondes_offmap: int = 0,
) -> str:
    """Compose an honest figure caption.

    States the filter, the excluded population, and the observational bias that
    a transmitter map cannot avoid — a reader should not have to reconstruct any
    of it from the code.
    """
    where = "Northern" if hemisphere == "N" else "Southern"
    rx_total = counts.get("rx_total", counts["rx"])
    wd = counts.get("rx_wd", 0)
    return (
        f"{where} hemisphere WSPR stations, {period}. Callsigns are collapsed to "
        f"physical sites: {rx_total:,} receiver sites (of which {wd:,} report "
        f"WsprDaemon extended spots, i.e. carry a calibrated per-spot noise "
        f"measurement) and {counts['tx']:,} transmitter sites poleward of "
        f"{spec.lat_limit:.0f}°. Marker size and opacity encode census quality tier, "
        f"defined in the legend; transmitters are restricted to tiers "
        f"{'/'.join(spec.tx_tiers)}, excluding {tx_rejected:,} rejected transmitter "
        f"callsigns — predominantly balloon-telemetry callsigns carrying fabricated "
        f"grid squares, which would otherwise place thousands of phantom stations at "
        f"high latitude. Dashed contours are AACGM-v2 geomagnetic latitude at epoch "
        f"{spec.epoch}. Note that transmitters are observable only through "
        f"receptions, so transmitter density is convolved with receiver coverage and "
        f"propagation; this map is in part a map of who was listening."
        + _sonde_caption(counts, spec, sondes_offmap)
    )


def _sonde_caption(counts: dict[str, int], spec: MapSpec, sondes_offmap: int) -> str:
    """Compose the WSPRSonde sentence appended to the main caption."""
    n = counts.get("sonde", 0)
    if not n:
        return ""
    silent = counts.get("sonde_silent", 0)
    stamp = sonde_snapshot_utc()
    text = (
        f" Stars mark HamSCI WSPRSonde sites ({n} here) — controlled, GPS-disciplined "
        f"8-band transmitters at known positions, the reference points against which the "
        f"uncontrolled population should be read"
    )
    if silent and spec.sonde_encode_on_air:
        text += f"; {silent} {'is' if silent == 1 else 'are'} deployed but not currently heard"
    text += ". WSPRSonde positions are from the vendored snapshot"
    text += f"{' generated ' + stamp + ' UTC' if stamp else ''}"
    if spec.sonde_encode_on_air:
        text += ", and status reflects a two-day reception window ending at that instant"
    text += f". Only {'/'.join(spec.sonde_statuses)} units are drawn"
    if sondes_offmap:
        text += (
            f"; {sondes_offmap} positioned unit"
            f"{'s lie' if sondes_offmap != 1 else ' lies'} equatorward of "
            f"{spec.lat_limit:.0f}° and "
            f"{'appear' if sondes_offmap != 1 else 'appears'} on neither map"
        )
    return text + "."


def role_legend_handles(show_highlight: bool = True, show_sonde_unheard: bool = True):
    """Legend handles for the plotted series.

    Each carries a distinct hue **and** a distinct marker shape, so the series
    remain separable for a colour-blind reader and in greyscale print.

    Parameters
    ----------
    show_highlight : bool, optional
        Include the "Polar PSWS station" entry. LOCAL DELTA vs
        HamSCI/polar-psws — pass ``False`` when ``MapSpec.highlight`` is empty,
        so the legend does not advertise a marker that is not drawn.
    """
    from matplotlib.lines import Line2D

    return [
        Line2D([], [], marker="D", linestyle="none", color=WD_COLOR, markersize=7.5,
               markeredgecolor=WD_EDGE, markeredgewidth=1.0,
               label="Receiver — WsprDaemon (extended spots + noise)"),
        Line2D([], [], marker="o", linestyle="none", color=RX_COLOR, markersize=7,
               markeredgecolor=SURFACE, markeredgewidth=0.6,
               label="Receiver — WSPRNet only (SNR, no noise)"),
        Line2D([], [], marker="^", linestyle="none", color=TX_COLOR, markersize=6.5,
               markeredgecolor=SURFACE, markeredgewidth=0.6, label="Transmitter"),
        Line2D([], [], marker="*", linestyle="none", color=SONDE_COLOR,
               markersize=13, markeredgecolor=SONDE_EDGE, markeredgewidth=1.0,
               label="WSPRSonde — controlled TX, heard" if show_sonde_unheard
                     else "WSPRSonde — controlled TX"),
        *(
            [Line2D([], [], marker="*", linestyle="none", markerfacecolor="none",
                    markeredgecolor=SONDE_EDGE, markersize=13, markeredgewidth=1.0,
                    label="WSPRSonde — deployed, not heard")]
            if show_sonde_unheard else []
        ),
        *(
            [Line2D([], [], marker="o", linestyle="none", markerfacecolor="none",
                    markeredgecolor=INK, markersize=9, markeredgewidth=1.3,
                    label="Polar PSWS station")]
            if show_highlight else []
        ),
        Line2D([], [], linestyle="--", color=INK_MUTED, linewidth=0.9,
               label="Geomagnetic latitude, AACGM-v2 (dipole L in parentheses)"),
    ]


def tx_tier_note(spec: "MapSpec") -> str:
    """One-line statement of the transmitter tier rules actually applied."""
    shown = [f"{t} = {TX_TIER_RULES[t]}" for t in spec.tx_tiers if t in TX_TIER_RULES]
    hidden = [t for t in ("C", "rejected") if t not in spec.tx_tiers]
    text = "Transmitter tier (plotted: " + "/".join(spec.tx_tiers) + ")   " + ";   ".join(shown)
    if hidden:
        text += f".   Not plotted: {', '.join(hidden)}."
    return text


def station_figure(
    census: dict[str, pd.DataFrame],
    spec: MapSpec,
    period: str,
    period_days: int,
    figsize: tuple[float, float] = (9.2, 11.2),
    title: str | None = None,
    show_caption: bool = True,
    tx_rejected: int | None = None,
):
    """Build a complete, self-explaining figure for one hemisphere.

    This is the single figure builder; both the notebook and
    ``notebooks/polar_station_maps.py`` call it, so the two cannot drift apart.

    The legend and the tier definitions are part of the *figure*, not of the
    surrounding prose. A PNG dropped into a slide deck or emailed to a
    collaborator has to carry its own filter disclosure and its own definition of
    what tier A means, or the disclosure is lost the moment the file leaves the
    notebook.

    Receiver tier thresholds are computed from ``period_days`` rather than
    hard-coded, because the tier-A rule is relative (half the study period).

    Parameters
    ----------
    census : dict
        Output of :func:`load_census`.
    spec : MapSpec
        Hemisphere and drawing parameters.
    period : str
        Human-readable study period, for the subtitle and caption.
    period_days : int
        Study-period length, for the receiver tier thresholds.
    figsize : tuple of float, optional
    title : str, optional
        Overrides the default title.
    show_caption : bool, optional
        Draw the caption beneath the map.

    Returns
    -------
    tuple
        ``(fig, ax, info)``; ``info`` holds the plotted counts, the excluded
        transmitter count, and the site frames actually drawn.
    """
    import textwrap

    import cartopy.crs as ccrs
    import matplotlib.pyplot as plt

    rx_all = to_sites(census["rx_stations"], "rx", tiers=spec.rx_tiers)
    tx_all = to_sites(census["tx_stations"], "tx", tiers=spec.tx_tiers)
    rx_sites = in_hemisphere(rx_all, spec.hemisphere, spec.lat_limit)
    tx_sites = in_hemisphere(tx_all, spec.hemisphere, spec.lat_limit)

    sonde_all = sonde_sites(load_wsprsondes(), statuses=spec.sonde_statuses)
    sondes = (
        in_hemisphere(sonde_all, spec.hemisphere, spec.lat_limit)
        if not sonde_all.empty
        else sonde_all
    )
    sondes_offmap = len(sonde_all) - sum(
        len(in_hemisphere(sonde_all, h, spec.lat_limit)) for h in ("N", "S")
    )

    # How many transmitters this hemisphere's filter removed, for the caption.
    # LOCAL DELTA vs HamSCI/polar-psws — see analysis/README.md. The rejected count
    # is printed in the caption so the tier filter is visible rather than implied,
    # but recomputing it needs the ~468k rejected rows, which this repository does
    # not carry: its census extract is pre-filtered to the tiers actually plotted.
    # When the count is supplied, use it; otherwise fall back to the upstream path.
    if tx_rejected is None:
        tx_rejected_all = to_sites(census["tx_stations"], "tx", tiers=("rejected",))
        tx_rejected = len(in_hemisphere(tx_rejected_all, spec.hemisphere, spec.lat_limit))

    projection = (
        ccrs.NorthPolarStereo(central_longitude=0.0)
        if spec.hemisphere == "N"
        else ccrs.SouthPolarStereo(central_longitude=0.0)
    )
    fig = plt.figure(figsize=figsize, facecolor=SURFACE)
    ax = fig.add_axes([0.05, 0.380, 0.90, 0.530], projection=projection)
    ax.set_facecolor(SURFACE)

    counts = draw_map(ax, rx_sites, tx_sites, spec, sondes=sondes)

    where = "Northern" if spec.hemisphere == "N" else "Southern"
    fig.text(
        0.5, 0.958, title or f"WSPR station coverage — {where} hemisphere",
        ha="center", va="bottom", fontsize=15, color=INK, fontweight="semibold",
    )
    fig.text(
        0.5, 0.933,
        f"{counts.get('rx_total', counts['rx']):,} receiver sites "
        f"({counts.get('rx_wd', 0):,} WsprDaemon) \u00b7 {counts['tx']:,} transmitter sites "
        f"\u00b7 {period}",
        ha="center", va="bottom", fontsize=10, color=INK_SECONDARY,
    )

    # Series legend, upper left of the panel area.
    role_legend = fig.legend(
        handles=role_legend_handles(
            show_highlight=bool(spec.highlight),
            show_sonde_unheard=spec.sonde_encode_on_air,
        ),
        loc="upper left", bbox_to_anchor=(0.045, 0.370),
        frameon=True, framealpha=0.96, facecolor=SURFACE, edgecolor="#d6d5ce",
        fontsize=8.4, labelcolor=INK_SECONDARY, borderpad=0.7, handletextpad=0.7,
    )
    role_legend.set_zorder(20)

    # Receiver tier legend: the size ramp *and* the rule that defines each tier.
    tier_legend = fig.legend(
        handles=tier_legend_handles("rx", spec.rx_tiers, rules=rx_tier_rules(period_days)),
        loc="upper right", bbox_to_anchor=(0.955, 0.370),
        title=f"Receiver tier (marker size) — days active of {period_days}",
        frameon=True, framealpha=0.96, facecolor=SURFACE, edgecolor="#d6d5ce",
        fontsize=8.4, labelcolor=INK_SECONDARY, title_fontsize=8.4,
        borderpad=0.7, handletextpad=0.7,
    )
    tier_legend.get_title().set_color(INK_SECONDARY)
    tier_legend.set_zorder(20)

    # Transmitter tier rules, stated explicitly rather than left to the docs.
    fig.text(
        0.5, 0.215, "\n".join(textwrap.wrap(tx_tier_note(spec), 140)),
        ha="center", va="top", fontsize=7.8, color=INK_SECONDARY, linespacing=1.5,
    )

    if show_caption:
        fig.text(
            0.5, 0.178,
            "\n".join(textwrap.wrap(
                caption(spec.hemisphere, counts, spec, tx_rejected, period,
                        sondes_offmap), 140)),
            ha="center", va="top", fontsize=7.6, color=INK_SECONDARY, linespacing=1.55,
        )

    info = {
        "counts": counts,
        "tx_rejected": tx_rejected,
        "rx_sites": rx_sites,
        "tx_sites": tx_sites,
        "sondes": sondes,
        "sondes_offmap": sondes_offmap,
    }
    return fig, ax, info
