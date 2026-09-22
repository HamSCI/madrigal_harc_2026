# wspr-station-census

Station-level census of WSPR **transmitters** and **receivers** from the WsprDaemon
ClickHouse mirrors, built for the HamSCI polar-PSWS study
([HamSCI/polar-psws](https://github.com/HamSCI/polar-psws)) but useful for any
WSPR-based investigation that needs to know *who was on the air, where, when, and
on what*.

It answers, per station and for a whole study period:

- **Where is it?** — position derived from the Maidenhead locator, with hemisphere,
  absolute latitude, cell size, and optional AACGM-v2 geomagnetic latitude.
- **Transmitter, receiver, or both?** — separate tables, with cross-reference flags.
- **Does it report extended WsprDaemon data?** — i.e. is a per-spot *noise*
  measurement available, answered two independent ways: membership of
  `wsprdaemon.spots`, and the reporting-software class parsed from `wspr.rx`.
- **Which bands?** — canonical labels, per-station lists and per-station-per-band
  spot counts.
- **When, and how continuously?** — exact active-day counts, longest activity gap,
  monthly breakdown.
- **How much should you trust it?** — quality tiers, callsign classification, grid
  consistency, noise-calibration flags.

## Why the quality columns exist

A naive census is dominated by artefacts, and they are not subtle. Over
2025-06-01 → 2026-07-01, `wsprdaemon.spots` contains **772,810 distinct
transmitter callsigns**. The real population is smaller by two orders of
magnitude; the excess is balloon-telemetry callsigns that encode data in the
callsign, grid and power fields, plus one-off false decodes.

The consequence for a polar study is direct. For June 2026, `wspr.rx` unfiltered
reports **32,757 transmitters above 70° absolute latitude**, split almost evenly
between hemispheres — pure fiction, from fabricated grid squares. Gate on
persistence and callsign shape and the same month gives **12**.

So this package does not present a filtered list as truth. It emits every metric a
filter would use as a **column**, assigns a `quality_tier`, records what was
discarded in a manifest, and leaves the choice in your notebook where it is
visible. See [`docs/COLUMNS.md`](docs/COLUMNS.md) for the full dictionary and the
measurements behind each threshold.

## Install

```bash
cd wspr-station-census
pip install -e .                    # or: pip install -e '.[geomag,dev]'
```

Requires Python ≥ 3.10, `pandas` and `requests`. Nothing else is mandatory — the
query cache stores gzipped raw server responses, so there is no parquet or
database dependency.

Optional extras:

| Extra | Adds |
|---|---|
| `geomag` | `aacgmv2`, enabling the `cgm_lat` / `abs_cgm_lat` columns |
| `dev` | `pytest`, `ruff` |

The package also runs straight from a clone without installing:
`PYTHONPATH=src python -m wspr_station_census.cli …`

## Quickstart

```bash
# What would run, and how much of it (no network access)
wspr-census plan

# Query the database into a local cache (resumable; ~20 min for 13 months)
wspr-census extract --start 2025-06-01 --end 2026-07-01

# Build the CSVs from the cache (no network access; re-run freely)
wspr-census summarize --start 2025-06-01 --end 2026-07-01

# Both at once
wspr-census run
```

Outputs land outside the repository, under
`$POLAR_PSWS_DATA_DIR/wspr_station_census/<period>/summary/` (default
`~/polar_psws_data`), because the parent project deliberately does not track bulk
data. Override with `--data-dir`.

```python
import pandas as pd

rx = pd.read_csv("rx_stations.csv")
tx = pd.read_csv("tx_stations.csv")

# Receivers that can support noise-based analysis
rx[rx.in_wsprdaemon & (rx.noise_valid_frac > 0.9)]

# Transmitters safe to put on a map
tx[tx.quality_tier.isin(["A", "B"])]

# Southern-hemisphere polar stations
rx[(rx.hemisphere == "S") & (rx.abs_lat >= 60)]
```

## Output files

| File | Grain | Purpose |
|---|---|---|
| `rx_stations.csv` | one row per receiving callsign | the receiver map and inventory |
| `tx_stations.csv` | one row per transmitting callsign | the transmitter map, with tiers |
| `rx_station_band.csv` / `tx_station_band.csv` | station × band | which bands, how much |
| `rx_station_month.csv` / `tx_station_month.csv` | station × month | activity through the period |
| `wd_receivers.csv` | `(rx_sign, rx_id)` | per-receiver noise capability |
| `population.csv` | table × role × month | the *unfiltered* population, for context |
| `extraction_manifest.json` | — | provenance, thresholds, caveats, tier counts |

## How it works

Aggregation happens **server-side**; raw spots are never pulled. The study period
holds 1.02 × 10⁹ rows in `wsprdaemon.spots` and ~2.8 × 10⁹ in `wspr.rx`, and
ClickHouse reduces either in a second or two.

Three query families, split by grain because distinct counts are only meaningful
at the grain they are computed at:

- **`stats`** (station × ~10-day window) — distinct slots and partners, plus the
  exact *sets* of active dates, bands, grids, versions and powers via
  `groupUniqArray`. Returning sets rather than counts is what makes the
  period-level day count and the activity-gap analysis exact.
- **`detail`** (station × band × grid × extras × ~15-day window) — additive counts
  only, so it is cheap. Source of modal grid, per-band counts, modal power, and
  per-receiver noise statistics.
- **`population`** (window) — the unfiltered counts, so the manifest can state
  what was filtered out.

### Server constraints this encodes

Measured against `wd10.wsprdaemon.org`, 2026-07-27/28:

- GET only; a POST body is rejected by nginx with `403`.
- Plain HTTP on port 80 — not HTTPS, not port 8123.
- No authentication for reads.
- **A ~10 s nginx read timeout.** A single-month `GROUP BY rx_sign` over
  `wspr.rx` took 8.5 s; the same query over 10 days took 1.4 s. Every query is
  therefore windowed, and a `504` triggers automatic window bisection rather than
  a retry — retrying cannot fix a window that is too wide.
- Windows are clipped to month boundaries, which is what makes the station-month
  rollups exact.

### Being a good neighbour

These are volunteer-run servers carrying a live operational load. The client
paces requests (`--pace`, default 0.5 s), runs strictly one at a time, always
bounds `time`, selects only what it needs, and identifies itself in the
`User-Agent`. Extraction is cached and resumable so a re-run costs nothing. For a
sustained multi-year pull, contact Rob Robinett (AI6VN) via
<https://wsprdaemon.org> first.

## Development

```bash
pytest                                        # 120+ tests, no network access
pytest --doctest-modules src/wspr_station_census
ruff check . && ruff format --check .
```

The tests deliberately encode real values sampled from the database — telemetry
callsigns, the `-999` sentinel, the band-code collision, the mixed-case receiver
names — so that a regression against the actual data shape fails loudly.

## Provenance

Every schema fact, row count, timing and threshold in this package and in
`docs/COLUMNS.md` was obtained by direct query against `wd10.wsprdaemon.org`, not
from recollection or secondary documentation. Schemas on a live volunteer-run
system can change; re-check with:

```bash
curl -s -G --data-urlencode "query=DESCRIBE wsprdaemon.spots" http://wd10.wsprdaemon.org/
```

Background reading, both in the parent repository:

- [`docs/wsprdaemon_extended_spots_access.md`](../docs/wsprdaemon_extended_spots_access.md)
  — collaborator-facing access guide, worked queries, data-quality caveats.
- [`docs/wspr_live_database_reference.md`](../docs/wspr_live_database_reference.md)
  — which servers and tables exist and how they differ.

G. Griffiths (G3ZIL), R. Robinett (AI6VN), G. Elmore (N6GN), "Estimating LF – HF
Band Noise While Acquiring WSPR Spots," *QEX*, Sep/Oct 2020, pp. 25–33, documents
the two noise estimators and the calibration caveats.
