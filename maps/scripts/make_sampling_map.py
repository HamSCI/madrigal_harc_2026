"""Where the high-quality WSPR network samples the ionosphere.

The station maps show instruments. This shows **sampling**: great-circle paths
between WSPRSonde transmitters and WsprDaemon receivers — the only pairs on which
SQ3's noise-referenced absorption metric can be computed — and the path midpoints that
stand in for the reflection regions they sound.

Two things are deliberately visible on the figure:

* the auroral zone, shaded, so "does the sampling reach the aurora" is a question
  the reader answers by looking rather than by interpolating between contours;
* the single-hop threshold, because a midpoint only means something on a path
  short enough to be one hop. Long paths are drawn, since they are real links,
  but their midpoints are not asserted as sample locations.

Run:
    .venv/bin/python maps/scripts/make_sampling_map.py
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "src"))

from movie_maps import branding as B  # noqa: E402
from movie_maps import maps as M  # noqa: E402
from movie_maps import sampling as S  # noqa: E402

CENSUS_DIR = BASE / "data" / "census"
SONDE_CSV = BASE / "wsprsonde-stations" / "wsprsonde_locations.csv"
FIG_DIR = BASE / "figures"

#: Auroral zone, absolute corrected geomagnetic latitude. The band SQ3 is about.
ZONE = (60.0, 75.0)

#: Foot band: legend and credit share this baseline, and the map sits above it.
#: The band's top is derived from the credit block, not fixed, so resizing the
#: logos cannot push them into the map.
FOOT_BOTTOM = 0.030
AX_TOP = 0.928

BACKDROP = "#6f6e69"     # the wider WSPR population, context only
PATH_SHORT = "#2a78d6"   # single hop: midpoint is a fair proxy
PATH_LONG = "#8a8981"    # multi hop: drawn as connectivity, not as sampling
ZONE_FILL = "#f0bc4e"   # must read against LAND #dcd9cd, not just ocean


def load_inputs():
    """Return (sondes, wsprdaemon_receivers, pairs, epoch, period)."""
    census = M.load_census(CENSUS_DIR)
    manifest = json.loads((CENSUS_DIR / "extraction_manifest.json").read_text())
    epoch = manifest["geomagnetic"]["epoch"]
    start = date.fromisoformat(manifest["query"]["period_start"])
    end = date.fromisoformat(manifest["query"]["period_end_exclusive"])
    # Month precision, and the last month is derived from the *inclusive* last day.
    # The manifest end date is exclusive: at day precision "to 2026-Jul-01" is
    # right, but as a month "July 2026" would claim a month holding no data.
    last_day = end - timedelta(days=1)
    period = f'{start.strftime("%B %Y")} – {last_day.strftime("%B %Y")}'

    rx = M.to_sites(census["rx_stations"], "rx").reset_index()
    tx = M.to_sites(census["tx_stations"], "tx").reset_index()
    wd = rx[rx["is_wsprdaemon"].fillna(False).astype(bool)]
    other = pd.concat([rx[~rx.index.isin(wd.index)], tx], ignore_index=True)
    sondes = M.sonde_sites(M.load_wsprsondes(SONDE_CSV))
    pairs = S.add_midpoint_cgm(S.sonde_receiver_pairs(sondes, wd), epoch)
    return sondes, wd, other, pairs, epoch, period


def shade_auroral_zone(ax, hemisphere, epoch, lat_limit):
    """Fill the auroral zone band so the reader need not interpolate contours."""
    import cartopy.crs as ccrs

    grid = M.cgm_latitude_grid(hemisphere, epoch, lat_limit)
    if grid is None:
        return False
    lon2d, lat2d, cgm = grid
    band = np.abs(cgm)
    ax.contourf(
        lon2d, lat2d, band, levels=[ZONE[0], ZONE[1]],
        colors=[ZONE_FILL], alpha=0.58, transform=ccrs.PlateCarree(), zorder=1,
    )
    return True


def draw_background(ax, other, hemisphere, lat_limit):
    """Draw the wider WSPR population as a faint ground layer.

    The argument here is about the WSPRSonde-to-WsprDaemon pairs, but those pairs
    sit inside a network of ~29,000 other stations and dropping them entirely
    overstates how sparse the picture is. They go down first, small and pale and
    in one undifferentiated series: present as context, not competing for the eye.
    """
    import cartopy.crs as ccrs

    panel = M.in_hemisphere(other, hemisphere, lat_limit)
    if panel.empty:
        return 0
    ax.scatter(panel["lon"], panel["lat"], s=1.6, c=BACKDROP, marker="o",
               linewidths=0, alpha=0.30, transform=ccrs.PlateCarree(), zorder=1.5)
    return len(panel)


def draw_paths(ax, pairs, hemisphere, lat_limit):
    """Draw great circles, split at the single-hop threshold.

    Long paths go down first and very faint: they are real links and show the
    network's reach, but they are context, not the claim.
    """
    import cartopy.crs as ccrs

    geodetic = ccrs.Geodetic()
    sign = 1.0 if hemisphere == "N" else -1.0
    # Keep a path if either end or its midpoint is on this hemisphere's panel.
    on_panel = (
        (sign * pairs["tx_lat"] >= lat_limit)
        | (sign * pairs["rx_lat"] >= lat_limit)
        | (sign * pairs["mid_lat"] >= lat_limit)
    )
    view = pairs[on_panel]
    for subset, color, alpha, lw, z in (
        (view[~view["single_hop"]], PATH_LONG, 0.16, 0.45, 2),
        (view[view["single_hop"]], PATH_SHORT, 0.55, 0.95, 3),
    ):
        for _, p in subset.iterrows():
            ax.plot([p["tx_lon"], p["rx_lon"]], [p["tx_lat"], p["rx_lat"]],
                    transform=geodetic, color=color, alpha=alpha, linewidth=lw,
                    solid_capstyle="round", zorder=z)
    return view


def draw_midpoints(ax, view):
    """Plot midpoints for single-hop paths only.

    Multi-hop midpoints are not drawn at all. A midpoint is a claim about where a
    path sounded the ionosphere, and on a multi-hop path that claim is not one the
    geometry supports; plotting it open still put a mark on the map at a place
    nothing was measured. The multi-hop *paths* stay, because the links are real.
    """
    import cartopy.crs as ccrs

    plate = ccrs.PlateCarree()
    short_mid = view[view["single_hop"]]
    if not short_mid.empty:
        ax.scatter(short_mid["mid_lon"], short_mid["mid_lat"], s=26,
                   c=PATH_SHORT, edgecolors=M.SURFACE, linewidths=0.5,
                   alpha=0.95, transform=plate, zorder=6)


def legend_handles():
    """Minimal legend: four marks, no tier ramp, no method text."""
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    km = f"{S.SINGLE_HOP_KM:,.0f} km"
    return [
        Line2D([], [], color=PATH_SHORT, lw=1.2, label=f"Path ≤ {km} — single hop"),
        Line2D([], [], marker="o", linestyle="none", color=PATH_SHORT, markersize=5,
               markeredgecolor=M.SURFACE, label="Midpoint — single-hop (assumed)"),
        Line2D([], [], color=PATH_LONG, lw=0.9, alpha=0.6,
               label=f"Path > {km} — multi-hop"),
        Line2D([], [], marker="*", linestyle="none", color=M.SONDE_COLOR, markersize=10,
               markeredgecolor=M.SONDE_EDGE, label="HamSCI WSPRSonde transmitters"),
        Line2D([], [], marker="D", linestyle="none", color=M.WD_COLOR, markersize=6,
               markeredgecolor=M.WD_EDGE, label="HamSCI WSPRDaemon receivers"),
        Line2D([], [], marker="o", linestyle="none", color=BACKDROP, markersize=2.5,
               alpha=0.55, label="Other WSPR stations"),
        Patch(facecolor=ZONE_FILL, edgecolor="none",
              label=f"Auroral zone, {ZONE[0]:.0f}–{ZONE[1]:.0f}° CGM"),
    ]




def build(hemisphere, sondes, wd, other, pairs, epoch, period):
    """Build one hemisphere panel."""
    import cartopy.crs as ccrs

    # Default CGM ladder (40/50/60/70/80), matching the station maps: the shaded
    # band already marks the auroral zone, so the rings are free to give the reader
    # the wider latitude frame instead of duplicating the band edges.
    spec = M.MapSpec(hemisphere=hemisphere, epoch=epoch, highlight=(),
                     sonde_encode_on_air=False)
    lat_limit = spec.lat_limit
    projection = (ccrs.NorthPolarStereo(central_longitude=0.0) if hemisphere == "N"
                  else ccrs.SouthPolarStereo(central_longitude=0.0))

    # Vertical budget, 8.6 in tall: ~0.65 in of heading, the disc, then a single
    # ~1.45 in band at the foot holding the legend (left) and the credit block
    # (right) side by side, so that band costs the height of one of them, not both.
    # Taller than it looks like it needs to be: the disc is inscribed in the axes
    # and is therefore height-limited, so vertical room is what makes the map
    # bigger. Extra height goes into the disc, which then fills the bottom
    # corners the legend and credit sit in, rather than into whitespace.
    fig = plt.figure(figsize=(9.2, 10.1), facecolor=M.SURFACE)
    foot_top = FOOT_BOTTOM + B.credit_height(fig) + 0.012
    ax = fig.add_axes((0.05, foot_top, 0.90, AX_TOP - foot_top),
                      projection=projection)
    ax.set_facecolor(M.SURFACE)

    # Reuse the base map, but with the census series suppressed: an empty
    # transmitter frame and receivers restricted to WsprDaemon sites. The 18k
    # WSPRNet-only receivers and 10k transmitters carry none of this argument
    # and would bury the paths.
    wd_panel = M.in_hemisphere(wd, hemisphere, lat_limit)
    empty_tx = wd_panel.iloc[0:0]
    sonde_panel = M.in_hemisphere(sondes, hemisphere, lat_limit)
    M.draw_map(ax, wd_panel, empty_tx, spec, sondes=sonde_panel)
    n_other = draw_background(ax, other, hemisphere, lat_limit)

    shade_auroral_zone(ax, hemisphere, epoch, lat_limit)
    view = draw_paths(ax, pairs, hemisphere, lat_limit)
    draw_midpoints(ax, view)

    where = "Northern" if hemisphere == "N" else "Southern"
    n_short = int(view["single_hop"].sum())
    zone = view[view["mid_cgm_lat"].abs().between(*ZONE)] if "mid_cgm_lat" in view else view.iloc[0:0]
    n_zone_short = int(zone["single_hop"].sum()) if not zone.empty else 0
    fig.text(0.5, 0.966, f"Amateur Radio WSPR Network — {where} hemisphere",
             ha="center", va="bottom", fontsize=15, color=M.INK, fontweight="semibold")
    fig.text(0.5, 0.942,
             f"{period} · {len(view):,} HamSCI WSPRSonde–WSPRDaemon Paths "
             f"({n_short:,} Single Hop)",
             ha="center", va="bottom", fontsize=10.5, color=M.INK_SECONDARY)

    # Anchored by its *lower* left so the legend and the credit block share one
    # baseline. Anchoring the top instead would leave the alignment dependent on
    # however tall the legend happened to render.
    ax.legend(handles=legend_handles(), loc="lower left",
              bbox_to_anchor=(0.025, FOOT_BOTTOM), bbox_transform=fig.transFigure,
              frameon=True, framealpha=0.95, edgecolor="#d8d7d0",
              fontsize=8.0, labelcolor=M.INK_SECONDARY, borderpad=0.6,
              handletextpad=0.8, labelspacing=0.38)
    B.add_credit(fig, bottom=FOOT_BOTTOM)
    return fig, view, zone


def main() -> None:
    """Build both hemisphere sampling maps."""
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    sondes, wd, other, pairs, epoch, period = load_inputs()
    print(f"sondes {len(sondes)}  wsprdaemon receivers {len(wd)}  pairs {len(pairs):,}")
    print(f"summary {S.auroral_zone_summary(pairs, *ZONE)}\n")

    for hemisphere in ("N", "S"):
        fig, view, zone = build(hemisphere, sondes, wd, other, pairs, epoch, period)
        name = "north" if hemisphere == "N" else "south"
        stem = FIG_DIR / f"wspr_sampling_{name}"
        for suffix in (".png", ".pdf"):
            fig.savefig(stem.with_suffix(suffix), dpi=200, facecolor=M.SURFACE)
        plt.close(fig)
        shortest = zone["range_km"].min() if not zone.empty else float("nan")
        n_zone_short = int(zone["single_hop"].sum()) if not zone.empty else 0
        print(f"{hemisphere}: paths {len(view):,}  single-hop {int(view['single_hop'].sum()):,}  "
              f"zone midpoints {len(zone):,} ({n_zone_short:,} single-hop)  "
              f"shortest in zone {shortest:,.0f} km "
              f"-> {stem.name}.png/.pdf")


if __name__ == "__main__":
    main()
