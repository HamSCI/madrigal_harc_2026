"""First-order siting estimate: which new receiver sites buy auroral-zone sampling.

Answers "how many strategically placed receivers, and where" at the level the
committed data supports: for a set of plausible Canadian/Alaskan host towns, pair
each candidate against the deployed WSPRSonde fleet and count single-hop paths
whose midpoints land in (or bracket) the 60-75 deg CGM auroral zone.

This is the geometric floor, not the full value of a site. A receiver also hears
the ambient WSPR population -- census receivers above 55 deg CGM hear a median of
647 distinct transmitters -- but only sonde pairs carry the controlled-transmitter,
measured-noise combination, so they are the sampling you can put error bars on.

The conjunction-weighted siting maps remain the Phase B deliverable; this script
is the first-order answer they will refine.

Run:
    .venv/bin/python maps/scripts/estimate_siting.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "src"))

from movie_maps import maps as M  # noqa: E402
from movie_maps import sampling as S  # noqa: E402

SONDE_CSV = BASE / "wsprsonde-stations" / "wsprsonde_locations.csv"
ZONE = (60.0, 75.0)
EPOCH = "2025-12-15"

#: Candidate hosts: towns with road access, grid power and internet, in or near the
#: auroral zone, in aurora-chaser country. Coordinates are town centres; siting
#: within ~50 km of a town does not change any conclusion at this precision.
CANDIDATES = [
    ("Fairbanks AK", 64.84, -147.72),
    ("Anchorage AK", 61.22, -149.90),
    ("Whitehorse YT", 60.72, -135.06),
    ("Yellowknife NT", 62.45, -114.37),
    ("Fort McMurray AB", 56.73, -111.38),
    ("La Ronge SK", 55.10, -105.28),
    ("Thompson MB", 55.74, -97.86),
    ("Churchill MB", 58.77, -94.17),
    ("Timmins ON", 48.48, -81.33),
    ("Iqaluit NU", 63.75, -68.52),
    ("Goose Bay NL", 53.30, -60.33),
    ("St. John's NL", 47.56, -52.71),
]


def main() -> None:
    """Print the per-candidate yield table."""
    sondes = M.sonde_sites(M.load_wsprsondes(SONDE_CSV))
    north = sondes[sondes["lat"] > 20]
    cand = pd.DataFrame(CANDIDATES, columns=["site", "lat", "lon"])
    pairs = S.add_midpoint_cgm(S.sonde_receiver_pairs(north, cand), EPOCH)

    one_hop = pairs[pairs["single_hop"]]
    in_band = one_hop[one_hop["mid_cgm_lat"].between(*ZONE)]
    poleward = one_hop[one_hop["mid_cgm_lat"] > ZONE[1]]
    adjacent = one_hop[one_hop["mid_cgm_lat"].between(50.0, ZONE[0], inclusive="left")]

    print(f"deployed northern sondes: {', '.join(north['call'])}")
    print(f"single-hop threshold {S.SINGLE_HOP_KM:,.0f} km · zone {ZONE[0]:.0f}-{ZONE[1]:.0f} CGM\n")
    print(f"{'candidate':18s} {'in-band':>8s} {'poleward':>9s} {'adjacent':>9s}   in-band via")
    for site in cand["site"]:
        i = in_band[in_band["rx"] == site]
        p = poleward[poleward["rx"] == site]
        a = adjacent[adjacent["rx"] == site]
        via = ", ".join(f"{r.tx} ({r.mid_cgm_lat:.0f}°)" for r in i.itertuples())
        print(f"{site:18s} {len(i):>8d} {len(p):>9d} {len(a):>9d}   {via}")

    print("\npoleward = single-hop midpoint above the band (VY0ERC pairs, polar-cap edge)")
    print("adjacent = single-hop midpoint 50-60 CGM (the 'adjacent/outside' control)")


if __name__ == "__main__":
    main()
