# Changelog

All notable changes to `wspr-station-census`. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is
[semantic](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — 2026-07-28

First release, built for the HamSCI polar-PSWS transmitter/receiver census over
2025-06-01 → 2026-06-30.

### Added
- `wspr-census` CLI with `plan`, `extract`, `summarize`, `run` and `status`.
- Server-side aggregation over `wspr.rx` and `wsprdaemon.spots` in three query
  families (`stats`, `detail`, `population`), split by grain because distinct
  counts are only meaningful at the grain they are computed at.
- Windowed queries with automatic bisection on nginx `504`, month-aligned so that
  station-month rollups are exact.
- Resumable extraction: the cache stores the literal gzipped server response per
  `(family, window)`, so re-summarising costs no queries and the record of what
  the database returned stays auditable.
- Eight output tables plus `extraction_manifest.json` recording provenance, every
  filter threshold with its rationale, tier populations and caveats.
- Transmitter quality tiers (`A`/`B`/`C`/`rejected`) and receiver coverage tiers,
  applied as *columns* rather than as row filters.
- Canonical band decoding, table-specific because the raw codes collide.
- Maidenhead-derived positions, with optional AACGM-v2 geomagnetic latitude
  behind the `geomag` extra.
- 122 tests and doctests, no network access required.

- Manifest reconciliation: summed station spot counts are cross-checked against
  the independent `population` census, and station-month totals against station
  totals, with the result recorded under `results.reconciliation`.

### Fixed during development
- **Count inflation across multi-month periods.** Incremental compaction reduced
  the numeric accumulator to one row per station and then re-attached the month
  column, fanning each station out to one row per month; the next compaction round
  summed those duplicated totals. Over thirteen months this inflated spot counts
  by roughly six orders of magnitude (DP0GVN read 3.0e11 spots against a true
  1.5e6, when the whole `wspr.rx` table holds ~2.8e9 rows). Compaction now reduces
  to `(station, month)`, which is idempotent. The bug was invisible over a
  single-month test period, so the regression tests span thirteen months and force
  several compaction rounds.

### Data-quality issues found and handled during development
- Latitude/longitude columns carry a `-999` sentinel (1.4% of
  `wsprdaemon.spots`) and out-of-range latitudes in both tables, so positions are
  derived from the Maidenhead locator instead.
- `wsprdaemon.spots` stores some receiver callsigns in lower case while
  `wspr.rx` stores them upper case; station keys are normalised so the
  `source_class` column reports real coverage differences rather than a casing
  artefact.
- `version` is NULL for 95 of 100 receivers in `wsprdaemon.spots`, so the
  software classification is driven from `wspr.rx`.
- Six rows have the site and receiver fields swapped (`rx_sign = '0'`); flagged
  rather than dropped.
