"""Build the hemisphere WSPR station maps used in the SQ3 PDR appendix.

Reads the slim census committed under ``maps/data/census`` and writes PNG + PDF
into ``docs/planning/figures``.

``maps.py`` is kept byte-identical to its upstream in ``HamSCI/polar-psws`` so it can
be re-synced without a merge. Two consequences for layout: the census directory is
passed explicitly here, and the WSPRSonde snapshot must sit at
``maps/wsprsonde-stations/``, because ``station_figure`` loads it itself from a
path relative to ``maps.py``.

Run:
    .venv/bin/python maps/scripts/make_station_maps.py
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "src"))

from movie_maps import branding as B  # noqa: E402
from movie_maps import maps as M  # noqa: E402

CENSUS_DIR = BASE / "data" / "census"
SONDE_CSV = BASE / "wsprsonde-stations" / "wsprsonde_locations.csv"
FIG_DIR = BASE / "figures"

#: The title names the *data*, not the map's author. These receivers are owned and
#: operated by individual amateurs; HamSCI made the map, and neither deployed nor
#: coordinates the network. Attribution goes in the credit mark instead, which is
#: why it reads "Map by" rather than carrying the logo alone.
TITLE = "Amateur Radio WSPR Network"

#: Auroral zone, absolute CGM latitude. Shared with make_sampling_map.py.
ZONE = (60.0, 75.0)
ZONE_FILL = "#f0bc4e"


def shade_auroral_zone(ax, hemisphere, epoch, lat_limit) -> None:
    """Fill the 60-75 deg CGM band. Alpha is set to read over land, not only ocean."""
    import cartopy.crs as ccrs
    import numpy as np

    grid = M.cgm_latitude_grid(hemisphere, epoch, lat_limit)
    if grid is None:
        return
    lon2d, lat2d, cgm = grid
    ax.contourf(lon2d, lat2d, np.abs(cgm), levels=[ZONE[0], ZONE[1]],
                colors=[ZONE_FILL], alpha=0.45,
                transform=ccrs.PlateCarree(), zorder=1)




#: Foot band. The credit sits on the baseline; the legends ride above it rather
#: than beside it, because this figure has two legends and the right-hand one
#: would otherwise land on top of the credit block.
FOOT_BOTTOM = 0.030

#: Legend labels, shortened and aligned with make_sampling_map.py's wording.
RELABEL = {
    "Receiver — WsprDaemon (extended spots + noise)": "HamSCI WSPRDaemon receivers",
    "Receiver — WSPRNet only (SNR, no noise)": "WSPRNet-only receivers",
    "Transmitter": "Transmitters",
    "WSPRSonde — controlled TX": "HamSCI WSPRSonde transmitters",
    "Geomagnetic latitude, AACGM-v2 (dipole L in parentheses)":
        "Geomagnetic latitude (AACGM-v2)",
}


def tighten_layout(fig, ax) -> None:
    """Reclaim the vertical space the suppressed caption left behind.

    ``station_figure`` places everything in fixed figure fractions sized for a
    150-word caption. With ``show_caption=False`` that leaves a dead band roughly
    1.7 in tall between the tier note and the foot of the page. Rather than add
    another local delta to ``maps.py``, move the pieces here: the legends and the
    tier note come down to sit just above the credit block, and the map takes the
    space they vacate.
    """
    # The transmitter tier rules are method text, and method text now lives in
    # docs/planning/figures/README.md with the rest of it. Leaving it here also
    # made it run under the credit block.
    for text in fig.texts:
        if text.get_text().startswith("Transmitter tier"):
            text.set_visible(False)

    # Anchors are set explicitly rather than read back from the legend:
    # ``get_bbox_to_anchor`` reports display coordinates, so reusing its x through
    # a figure transform puts the legend somewhere arbitrary. The tier legend is
    # the one with a title, and it is the right-hand of the pair.
    # station_figure attaches these with fig.legend, so they are figure-level
    # artists and are not among the axes' children.
    # Shorter labels, matching the sampling map's wording. Upstream's are written
    # for a figure with a whole page of caption under them; here they have to share
    # one row with a second legend and the credit block, and the longest of them
    # ("Geomagnetic latitude, AACGM-v2 (dipole L in parentheses)") alone made that
    # impossible. The definitions they drop are in docs/planning/figures/README.md.
    for old, new in RELABEL.items():
        for leg in fig.legends:
            for text in leg.get_texts():
                if text.get_text() == old:
                    text.set_text(new)

    fig.canvas.draw()  # legend extents are only real once laid out
    renderer = fig.canvas.get_renderer()
    # One row across the foot: series legend left, tier legend centre, credit
    # right, all sharing a baseline. Stacking the credit under the legends instead
    # costs its full height again, and that height comes straight out of the disc,
    # which is inscribed in the axes and so is limited by whatever vertical room
    # is left over.
    tallest = 0.0
    for leg in fig.legends:
        height = leg.get_window_extent(renderer).height / fig.bbox.height
        tallest = max(tallest, height)
        x = 0.660 if leg.get_title().get_text() else 0.030
        # loc is "upper *", so the anchor sets the top: put the top one height
        # above the shared baseline and the bottom lands exactly on it.
        leg.set_bbox_to_anchor((x, FOOT_BOTTOM + height), transform=fig.transFigure)

    top_of_foot = FOOT_BOTTOM + max(tallest, B.credit_height(fig)) + 0.016
    # The disc is inscribed in the axes, so its size follows the axes *height*.
    # Give it everything between the foot band and the subtitle.
    ax.set_position((0.05, top_of_foot, 0.90, 0.912 - top_of_foot))


def require_aacgmv2() -> None:
    """Fail loudly if the geomagnetic-latitude model is missing.

    ``maps.cgm_latitude_grid`` returns ``None`` when :mod:`aacgmv2` is absent and
    the caller then skips the overlay, so the figure still builds — silently,
    without the AACGM-v2 contours, while the legend still advertises them. That
    happened once and shipped into a .docx before anyone noticed. Check up front.
    """
    try:
        import aacgmv2  # noqa: F401
    except ImportError:
        raise SystemExit(
            "aacgmv2 is not installed, so the geomagnetic latitude contours would "
            "be silently omitted.\n"
            "Install it with:  .venv/bin/pip install -r maps/requirements.txt"
        ) from None


def main() -> None:
    """Build and save both hemisphere maps."""
    require_aacgmv2()
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    census = M.load_census(CENSUS_DIR)
    manifest = json.loads((CENSUS_DIR / "extraction_manifest.json").read_text())

    epoch = manifest["geomagnetic"]["epoch"]
    start = date.fromisoformat(manifest["query"]["period_start"])
    end = date.fromisoformat(manifest["query"]["period_end_exclusive"])
    period_days = manifest["query"]["period_days"]
    # Month precision, and the last month is derived from the *inclusive* last day.
    # The manifest end date is exclusive: at day precision "to 2026-Jul-01" is
    # right, but as a month "July 2026" would claim a month holding no data.
    last_day = end - timedelta(days=1)
    period = f'{start.strftime("%B %Y")} – {last_day.strftime("%B %Y")}'

    # Precomputed by build_slim_census.py; see the LOCAL DELTA note in maps.py.
    rejected = json.loads((CENSUS_DIR / "census_counts.json").read_text())
    rejected = rejected["tx_rejected_sites"]

    sondes = M.load_wsprsondes(SONDE_CSV)
    print(f"census  {CENSUS_DIR.relative_to(BASE)}")
    print(f"period  {period}  ({period_days} days)")
    print(f"epoch   {epoch}")
    print(f"sondes  {len(sondes)} units\n")

    for hemisphere in ("N", "S"):
        # No direct callouts for VY0ERC / DP0GVN. Upstream highlights them because
        # polar-psws is a study *of* those two sites; here they are two WSPRSondes
        # among several and the stars already mark them. The legend entry drops
        # with them, and the geomagnetic contour labels fall back to their default
        # meridian, which is what choose_label_longitude([]) returns.
        # sonde_encode_on_air=False: draw every deployed sonde filled. The only
        # unit the split would separate here is N4RVE, and its "intermittent"
        # status is a threshold artifact — 3.89 days since last spot against a
        # 2-day cutoff, after 3.46M spots on 9 bands with max_gap_days = 0 across
        # the census. A hollow star would read as "never heard" and understate it.
        spec = M.MapSpec(
            hemisphere=hemisphere, epoch=epoch, highlight=(),
            sonde_encode_on_air=False,
        )
        where = "Northern" if hemisphere == "N" else "Southern"
        fig, ax, info = M.station_figure(
            census, spec, period, period_days,
            tx_rejected=rejected[hemisphere],
            title=f"{TITLE} — {where} hemisphere",
            show_caption=False,
            figsize=(9.2, 9.8),
        )
        # The claim this figure carries is "the noise-measuring receivers are not where
        # the aurora is". Shading the band states it; leaving the reader to
        # interpolate between contour rings makes them derive it.
        shade_auroral_zone(ax, hemisphere, epoch, spec.lat_limit)
        tighten_layout(fig, ax)
        B.add_credit(fig, bottom=FOOT_BOTTOM)
        name = "north" if hemisphere == "N" else "south"
        stem = FIG_DIR / f"wspr_stations_{name}"
        for suffix in (".png", ".pdf"):
            fig.savefig(stem.with_suffix(suffix), dpi=200, facecolor=M.SURFACE)
        plt.close(fig)
        counts = info["counts"]
        print(
            f"{hemisphere}: receivers {counts['rx_total']:,} "
            f"({counts['rx_wd']:,} WsprDaemon, {counts['rx']:,} WSPRNet-only) "
            f"transmitters {counts['tx']:,} -> {stem.name}.png/.pdf"
        )


if __name__ == "__main__":
    main()
