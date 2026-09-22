"""Extract the committed slim census from the full WSPR station census cache.

The full census summary is ~240 MB, dominated by ``tx_stations.csv`` (610k rows,
176 MB), and lives outside any repository in ``$POLAR_PSWS_DATA_DIR`` by the
policy stated in ``polar-psws/CLAUDE.md``. The station maps need a small part of
it: a dozen columns, and transmitters only at quality tiers A/B. That subset is
~2.4 MB, small enough to commit, which is what makes the figures in this
repository reproducible without a ClickHouse round trip.

Rerun this after regenerating the census; commit the result.

Run:
    .venv/bin/python maps/scripts/build_slim_census.py
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parents[1]
DEST = BASE / "data" / "census"

DEFAULT_PERIOD = "2025-06-01_2026-07-01"

#: Transmitter tiers kept. An unfiltered transmitter map is fiction — the raw
#: data places tens of thousands of balloon-telemetry callsigns with fabricated
#: grid squares at high latitude. See the module docstring of ``maps.py``.
TX_TIERS = ("A", "B")

#: Columns ``maps.to_sites`` and its callers read, plus the identity columns
#: needed to collapse callsigns to physical sites. Everything else in the census
#: is for analyses that do not run here.
RX_COLUMNS = [
    "rx_sign", "site_call", "in_wsprnet", "in_wsprdaemon", "source_class",
    "sw_class", "modal_grid", "grid_precision", "lat", "lon", "hemisphere",
    "abs_lat", "cgm_lat", "abs_cgm_lat", "quality_tier", "n_days_active",
    "n_spots_wsprnet", "n_spots_wd", "noise_valid_frac",
]
TX_COLUMNS = [
    "tx_sign", "base_call", "in_wsprnet", "in_wsprdaemon", "source_class",
    "quality_tier", "modal_grid", "grid_precision", "lat", "lon", "hemisphere",
    "abs_lat", "cgm_lat", "abs_cgm_lat", "n_days_active", "n_spots_wsprnet",
]


def to_sites_rejected_counts(tx: pd.DataFrame) -> dict[str, int]:
    """Count rejected transmitter *sites* per hemisphere, poleward of the map limit.

    Mirrors what ``maps.station_figure`` would compute from the full frame:
    collapse rejected callsigns to sites, then keep those on each map.
    """
    sys.path.insert(0, str(BASE / "src"))
    from movie_maps import maps as M

    sites = M.to_sites(tx, "tx", tiers=("rejected",))
    spec = M.MapSpec(hemisphere="N")
    return {
        h: int(len(M.in_hemisphere(sites, h, spec.lat_limit))) for h in ("N", "S")
    }


def source_dir(period: str) -> Path:
    """Return the full census summary directory for ``period``."""
    root = Path(os.environ.get("POLAR_PSWS_DATA_DIR", "~/polar_psws_data"))
    return root.expanduser() / "wspr_station_census" / period / "summary"


def main() -> None:
    """Write the slim census CSVs into ``maps/data/census``."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--period", default=DEFAULT_PERIOD)
    args = ap.parse_args()

    src = source_dir(args.period)
    if not src.is_dir():
        raise SystemExit(
            f"census not found: {src}\n"
            "Set POLAR_PSWS_DATA_DIR, or regenerate it with the wspr-station-census "
            "tool in the polar-psws repository."
        )
    DEST.mkdir(parents=True, exist_ok=True)

    rx = pd.read_csv(src / "rx_stations.csv", low_memory=False)
    tx = pd.read_csv(src / "tx_stations.csv", low_memory=False)

    rx_out = rx[[c for c in RX_COLUMNS if c in rx.columns]]
    tx_out = tx[tx["quality_tier"].isin(TX_TIERS)]
    tx_out = tx_out[[c for c in TX_COLUMNS if c in tx_out.columns]]

    rx_out.to_csv(DEST / "rx_stations.csv", index=False)
    tx_out.to_csv(DEST / "tx_stations.csv", index=False)
    shutil.copy2(src / "wd_receivers.csv", DEST / "wd_receivers.csv")
    shutil.copy2(src / "extraction_manifest.json", DEST / "extraction_manifest.json")

    # The figure caption states how many transmitter sites the tier filter excluded,
    # so the filter is visible rather than implied. That count cannot be recovered
    # from the extract above, which drops those rows — there are ~468k of them and
    # they are never plotted, so carrying them would cost ~40 MB to preserve two
    # integers. Compute them here instead and hand them to the figure.
    rejected = to_sites_rejected_counts(tx)
    (DEST / "census_counts.json").write_text(
        json.dumps({"tx_rejected_sites": rejected}, indent=2) + "\n"
    )

    dropped = len(tx) - len(tx_out)
    print(f"rx  {len(rx):>7,} rows -> {len(rx_out):>7,}  ({len(rx_out.columns)} cols)")
    print(f"tx  {len(tx):>7,} rows -> {len(tx_out):>7,}  ({len(tx_out.columns)} cols)"
          f"  [{dropped:,} below tier {'/'.join(TX_TIERS)} dropped]")
    total = sum(f.stat().st_size for f in DEST.iterdir() if f.is_file())
    print(f"total {total / 1e6:.1f} MB in {DEST.relative_to(BASE)}")


if __name__ == "__main__":
    main()
