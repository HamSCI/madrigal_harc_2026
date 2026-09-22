# maps

Hemisphere maps of the amateur radio WSPR network: receiver and transmitter sites, the
subset of receivers reporting WSPRDaemon extended spots, and the deployed HamSCI
WSPRSondes, over a shaded 60 to 75 degree AACGM-v2 auroral zone. A second pair of figures
shows where the high-quality pairs actually sound the ionosphere.

Copied here on 2026-09-22 so the Madrigal HARC 2026 paper can adapt the figures without
disturbing the study that produced them.

## Provenance

| Component | Source | Pinned at |
| --- | --- | --- |
| `src/`, `scripts/`, `data/`, `assets/`, `requirements.txt`, `figures/README.md` | [`HamSCI/HamSCI-MOVIE-CINEMA`](https://github.com/HamSCI/HamSCI-MOVIE-CINEMA), `analysis/` | `00ed541c807e8088dc6451e4d3a5db398e7231fb` |
| `src/movie_maps/maps.py`, `branding.py` | originally [`HamSCI/polar-psws`](https://github.com/HamSCI/polar-psws), vendored through MOVIE-CINEMA | as above |
| `wsprsonde-stations/` | [`HamSCI/wsprsonde.hamsci.org`](https://github.com/HamSCI/wsprsonde.hamsci.org) snapshot, generated 2026-08-13 20:34:25 UTC | `16af0f063ba8c50dce2ef4689022906590323b12` |
| `wspr-station-census/` | vendored ClickHouse extraction tool, via MOVIE-CINEMA | as above |

`data/census/` is a slim extract of the WSPR station census, 2025-06-01 to 2026-07-01
(395 days), drawn from the WSPRDaemon ClickHouse mirrors. It is 3.3 MB and committed, so
the figures rebuild without a ClickHouse round trip.

`data/.upstream_commit` carries the abbreviated SHA `76cb2af`, inherited from upstream. No
code in this tree reads or writes it, and the SHA resolves in neither MOVIE-CINEMA nor this
repository; it presumably names a `polar-psws` commit. It is kept as-is rather than
interpreted. Resolve it against `polar-psws` before relying on it as provenance.

## What was changed on copy

`src/movie_maps/maps.py` and `branding.py` are **byte-identical to their upstream**, which
is the condition that lets them be re-synced from `polar-psws` without a merge. They locate
the WSPRSonde snapshot and the credit logos through `Path(__file__).resolve().parents[2]`,
so the layout under `src/`, `wsprsonde-stations/` and `assets/` has to stay as it is here.
Verify before re-syncing:

```bash
cmp src/movie_maps/maps.py <upstream>/analysis/src/movie_maps/maps.py
```

Only the four driver scripts were edited. Each had a `REPO` constant pointing at the
MOVIE-CINEMA repository root and reaching down through an `analysis/` segment; each now has
a `BASE` constant pointing at this directory. Figure output moved from
`docs/planning/figures/` to `figures/` here.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r maps/requirements.txt
.venv/bin/pip install -e "maps/wspr-station-census[geomag]"   # only to regenerate the census
```

`aacgmv2` is required, not optional. Without it `maps.cgm_latitude_grid` returns `None`,
the caller skips the overlay, and the figures build cleanly but lose the geomagnetic
latitude contours while the legend still lists them. That failure reached a document once
upstream, so `make_station_maps.py` checks for the module and refuses to run without it.
The package needs a C compiler.

`cartopy` downloads Natural Earth coastline data on first use and caches it in
`~/.local/share/cartopy`. With that cache populated the build runs offline.

## Rebuilding the figures

```bash
.venv/bin/python maps/scripts/make_station_maps.py    # figures/wspr_stations_{north,south}
.venv/bin/python maps/scripts/make_sampling_map.py    # figures/wspr_sampling_{north,south}
```

Both write PNG and PDF into `figures/`, about a minute each. Method notes, including the
site-collapsing rule, the auroral zone definition, and the WSPRSonde status caveats, are in
[`figures/README.md`](figures/README.md).

To refresh the census from the full cache after re-running the extraction upstream:

```bash
.venv/bin/python maps/scripts/build_slim_census.py
```

## Layout

```
maps/
├── requirements.txt
├── src/movie_maps/
│   ├── maps.py              ← figure engine, byte-identical to upstream
│   ├── sampling.py          ← great-circle pairs, midpoints, single-hop test
│   └── branding.py          ← "Map by" credit block
├── scripts/
│   ├── make_station_maps.py ← coverage figures
│   ├── make_sampling_map.py ← sampling figures
│   ├── build_slim_census.py ← full census cache -> committed slim extract
│   └── estimate_siting.py   ← siting test over candidate Canadian sites
├── data/census/             ← slim extract, 3.3 MB, committed
├── wsprsonde-stations/      ← vendored WSPRSonde snapshot
├── wspr-station-census/     ← vendored census tool (ClickHouse -> cache -> CSVs)
├── assets/                  ← credit logos
└── figures/                 ← rendered output, plus the method notes
```

## Caveats carried over

WSPRSonde positions are Maidenhead cell centres from the curated list, not surveyed
coordinates; a 4-character locator is about 78 km across at 40 degrees latitude. The
`on_air_status` field is evidence of reception rather than of transmitter health. The
snapshot's `ok_to_list_public` column is `unknown` for most stations, so check consent
before publishing a station. The full set is in
`wsprsonde-stations/wsprsonde_locations_manifest.json`.
