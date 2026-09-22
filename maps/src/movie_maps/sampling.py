"""Great-circle geometry for WSPRSonde-to-WsprDaemon sampling maps.

The station maps answer "where are the instruments". This module answers the
question SQ3 actually asks: **where does the network sample the ionosphere**.

A WSPR link sounds the ionosphere along the whole path, not at its endpoints, and
for a single-hop path the reflection happens near the middle. The midpoint is
therefore the conventional proxy for the sampled region — and it is an
approximation whose quality degrades with distance. Below roughly 3,000 km of
ground range a path is plausibly single-hop and the midpoint is a fair stand-in
for one reflection region. Beyond that, multi-hop becomes likely, the path
samples several regions, and a single midpoint misrepresents it. That threshold
is carried through this module as :data:`SINGLE_HOP_KM` and is encoded on the
figure rather than assumed silently; ray tracing would be the way to replace the
rule of thumb with something defensible.

The pairs of interest are **WSPRSonde transmitters against WsprDaemon receivers**.
Sondes are the controlled transmitters — known position, known power, eight bands,
GPS-timed, reference-locked — and WsprDaemon receivers are the only ones reporting an
independent per-spot noise measurement. A path between the two is the only kind on which the
absorption metric in SQ3 can actually be computed, so their midpoints are the
network's high-quality sampling.

**These are geometric pairs, not observed links.** The committed census is
station-level and carries no transmitter-receiver pair table, so this module
draws the paths that *could* close, not the ones that did. That makes it a
capability map, which is the right object for a siting question, but it is not an
occurrence rate. Confirming which pairs actually close needs a spot-level query
against the WsprDaemon mirrors.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

#: Earth radius, km. Spherical is ample here: the midpoint error against WGS-84
#: is well under the ~80 km Maidenhead cell the endpoints are quantised to.
EARTH_RADIUS_KM = 6371.0

#: Ground range below which a path is plausibly single-hop and its midpoint is a
#: fair proxy for the reflection region. A rule of thumb, not a derived bound.
SINGLE_HOP_KM = 3000.0


def ground_range_km(lat1, lon1, lat2, lon2):
    """Great-circle ground range in km. Arrays broadcast."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp = p2 - p1
    dl = np.radians(np.asarray(lon2) - np.asarray(lon1))
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def great_circle_midpoint(lat1, lon1, lat2, lon2):
    """Midpoint of the great-circle arc, in degrees.

    Computed in Cartesian coordinates rather than by averaging latitude and
    longitude. The naive average is wrong everywhere and badly wrong at high
    latitude and across the antimeridian, which is precisely where these paths
    run.
    """
    p1, p2 = np.radians(lat1), np.radians(lat2)
    l1, l2 = np.radians(lon1), np.radians(lon2)
    x = np.cos(p1) * np.cos(l1) + np.cos(p2) * np.cos(l2)
    y = np.cos(p1) * np.sin(l1) + np.cos(p2) * np.sin(l2)
    z = np.sin(p1) + np.sin(p2)
    return (
        np.degrees(np.arctan2(z, np.hypot(x, y))),
        np.degrees(np.arctan2(y, x)),
    )


def sonde_receiver_pairs(sondes: pd.DataFrame, receivers: pd.DataFrame) -> pd.DataFrame:
    """Every WSPRSonde-to-receiver pair, with range and midpoint.

    Parameters
    ----------
    sondes : pandas.DataFrame
        Output of :func:`maps.sonde_sites`; needs ``call``, ``lat``, ``lon``.
    receivers : pandas.DataFrame
        WsprDaemon receiver sites; needs ``site``, ``lat``, ``lon``.

    Returns
    -------
    pandas.DataFrame
        One row per pair: ``tx``, ``rx``, ``tx_lat``, ``tx_lon``, ``rx_lat``,
        ``rx_lon``, ``range_km``, ``mid_lat``, ``mid_lon``, ``single_hop``.
    """
    if sondes.empty or receivers.empty:
        return pd.DataFrame(
            columns=["tx", "rx", "tx_lat", "tx_lon", "rx_lat", "rx_lon",
                     "range_km", "mid_lat", "mid_lon", "single_hop"]
        )
    s = sondes.dropna(subset=["lat", "lon"])
    r = receivers.dropna(subset=["lat", "lon"])
    si, ri = np.meshgrid(np.arange(len(s)), np.arange(len(r)), indexing="ij")
    si, ri = si.ravel(), ri.ravel()

    tx_lat = s["lat"].to_numpy()[si]
    tx_lon = s["lon"].to_numpy()[si]
    rx_lat = r["lat"].to_numpy()[ri]
    rx_lon = r["lon"].to_numpy()[ri]
    rng = ground_range_km(tx_lat, tx_lon, rx_lat, rx_lon)
    mid_lat, mid_lon = great_circle_midpoint(tx_lat, tx_lon, rx_lat, rx_lon)

    out = pd.DataFrame({
        "tx": s["call"].to_numpy()[si] if "call" in s.columns else s.index.to_numpy()[si],
        "rx": r["site"].to_numpy()[ri] if "site" in r.columns else r.index.to_numpy()[ri],
        "tx_lat": tx_lat, "tx_lon": tx_lon, "rx_lat": rx_lat, "rx_lon": rx_lon,
        "range_km": rng, "mid_lat": mid_lat, "mid_lon": mid_lon,
    })
    # A sonde co-located with a receiver is not a path.
    out = out[out["range_km"] > 1.0].reset_index(drop=True)
    out["single_hop"] = out["range_km"] <= SINGLE_HOP_KM
    return out


def add_midpoint_cgm(pairs: pd.DataFrame, epoch: str) -> pd.DataFrame:
    """Add ``mid_cgm_lat`` using AACGM-v2 at ``epoch`` (``YYYY-MM-DD``).

    Returns the frame unchanged if :mod:`aacgmv2` is unavailable, so callers
    should check for the column rather than assume it.
    """
    try:
        import aacgmv2
    except ImportError:
        return pairs
    from datetime import datetime

    when = datetime.fromisoformat(epoch)
    lat, lon = pairs["mid_lat"].to_numpy(), pairs["mid_lon"].to_numpy()
    cgm = np.full(len(pairs), np.nan)
    for i in range(len(pairs)):
        try:
            cgm[i] = aacgmv2.get_aacgm_coord(lat[i], lon[i], 300.0, when)[0]
        except Exception:
            pass
    out = pairs.copy()
    out["mid_cgm_lat"] = cgm
    return out


def auroral_zone_summary(
    pairs: pd.DataFrame, lo: float = 60.0, hi: float = 75.0
) -> dict[str, int]:
    """Count paths whose midpoint lands in the auroral zone, per hemisphere.

    The number that matters for SQ3 sufficiency: how much of the high-quality
    sampling actually falls where the aurora is, and how much of that is on
    paths short enough for the midpoint to mean something.
    """
    if "mid_cgm_lat" not in pairs.columns:
        return {}
    cgm = pairs["mid_cgm_lat"]
    out: dict[str, int] = {"pairs": len(pairs), "single_hop": int(pairs["single_hop"].sum())}
    for name, mask in (("north", cgm.between(lo, hi)), ("south", cgm.between(-hi, -lo))):
        out[f"{name}_zone"] = int(mask.sum())
        out[f"{name}_zone_single_hop"] = int((mask & pairs["single_hop"]).sum())
    return out
