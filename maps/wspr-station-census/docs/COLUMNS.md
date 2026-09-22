# Output column dictionary

Every threshold and caveat below is backed by a measurement against
`wd10.wsprdaemon.org`. Where a number appears, it was queried, not recalled.
Measurement date: 2026-07-28. Reference month for single-month figures: June 2026.

---

## Conventions used throughout

**Station keys are upper-cased.** Callsigns are case-insensitive, but the two
tables disagree: `wsprdaemon.spots` stores `w7wkr-2` and `w7wkr-k1` in lower case
(865,034 rows in June 2026) while `wspr.rx` stores the same receivers as
`W7WKR-2` / `W7WKR-K1` and has no lower-case callsigns at all. Without
normalisation these receivers appear in only one table — masquerading as exactly
the finding that `source_class` is meant to report.

**Positions come from the Maidenhead locator, not from the latitude/longitude
columns.** Those columns are unreliable:

| Pathology | Where | Extent (June 2026) |
|---|---|---|
| `-999` sentinel in lat/lon | `wsprdaemon.spots` | 1,220,928 of 88,185,059 rows (1.4%) — the same rows carry negative `distance`/`azimuth` |
| Latitude out of range | both tables | `max(tx_lat)` = 157.3 (`wsprdaemon.spots`), 166.06 (`wspr.rx`) |
| Empty or short grid | `wspr.rx`, `wsprdaemon.spots` | zero in 88 million rows |

Filtering on `rx_lat <= -60` without knowing about the sentinel yields 148 phantom
"Antarctic" receivers. Grid decoding is validated against the data: `ER60tb`
(VY0ERC, Eureka) decodes to 80.0625° N, which is exactly `max(rx_lat)` in
`wspr.rx` for the period — wsprnet derives its coordinates the same way.

**Metric columns are suffixed by source table.** `_wsprnet` from `wspr.rx`,
`_wd` (receivers) or `_wd_raw` (transmitters) from `wsprdaemon.spots`. Columns
describing the *station* rather than a table's view of it — position, activity,
bands, tiers — are unsuffixed and combine both sources.

**Spot counts between the two tables are not comparable.** `wspr.rx` keeps one row
per `(time, tx_sign, rx_sign, band)` — the strongest decode at a site — while
`wsprdaemon.spots` keeps every receiver instance's decode as its own row. WA2TP
runs nine receivers, so its raw WsprDaemon count is roughly nine times its wsprnet
count. The ratio is exposed as `wd_inflation` in the band tables rather than
hidden by normalising.

**Band codes are decoded per table, because they collide.** `band = 40` is the
8 m band (40.68 MHz) in `wspr.rx` but the 40 m band (7.04 MHz) in
`wsprdaemon.spots`; `band = 6` is out-of-band junk near 6–7 MHz in the former and
the real 6 m band in the latter. Both are mapped to canonical labels (`20m`,
`40m`, …); unrecognised codes become `other`. In `wspr.rx`, `other` is almost
entirely spurious out-of-band decodes (codes 2, 9, 15, 19, 27, 30, 46, 107, …, a
few dozen spots each in a month of 218 million).

---

## `rx_stations.csv` — one row per receiving callsign

### Identity

| Column | Meaning |
|---|---|
| `rx_sign` | Receiving callsign as reported, upper-cased. The primary key. |
| `site_call` | Base callsign with portable and receiver labels stripped. **Group by this to plot one dot per physical site**: `HB9VQQ/RS`, `/RL`, `/KE` are one station in JN47kh; `W7WKR`, `W7WKR-1`, `w7wkr-2`, `W7WKR-K1` are one station. |
| `n_sign_variants_at_site` | How many `rx_sign` values share this `site_call`. |
| `callsign_class` | `amateur`, `synthetic_telemetry`, `hashed`, `nonstandard`. For receivers this is diagnostic rather than a filter — `nonstandard` includes real sites such as `KFS` that have no call-area digit. |

### Provenance — "is it a WsprDaemon or a plain WSPR receiver?"

Two independent answers, because they disagree in informative ways.

| Column | Meaning |
|---|---|
| `in_wsprnet`, `in_wsprdaemon` | Present in `wspr.rx` / `wsprdaemon.spots`. |
| `source_class` | `both`, `wsprnet_only`, `wsprdaemon_only`. **`wsprdaemon_only` is a wsprnet-drop candidate**: the WsprDaemon path is loss-resistant by design while wsprnet ingest is known to drop spots silently. |
| `sw_class` | Reporting software: `wsprdaemon`, `kiwisdr`, `web888`, `wsjtx`, `other`, `unknown`. `other` means *recognised but unclassified*, not junk. |
| `modal_version`, `modal_version_frac`, `versions`, `n_versions`, `n_distinct_version` | Raw `version` strings, so the mapping can be refined without re-querying. |
| `n_rx_ids`, `rx_ids` | WsprDaemon receiver instances at this site (`KA9Q_0`, `KIWI_3`, …). |

The two axes are not redundant. In June 2026, 93 receivers were `both` + running
WsprDaemon software, but **32 more ran WsprDaemon software while appearing only in
`wspr.rx`** — a site can run the software without its extended spots reaching the
table you are querying. Note also that `version` is **NULL for 95 of 100
receivers in `wsprdaemon.spots`**, so `sw_class` must be driven from `wspr.rx`.

### Position

| Column | Meaning |
|---|---|
| `modal_grid` | Spot-count-weighted modal Maidenhead locator. |
| `modal_grid_frac` | Fraction of spots from `modal_grid`. Below ~0.9 means the station moved or reported inconsistently. |
| `n_distinct_grid`, `grids` | All locators seen (from the detail family / the window sets respectively). |
| `n_grids` | Locator count from the windowed `groupUniqArray` sets, capped at 8 per window. Compare with `n_distinct_grid` to detect truncation. |
| `grid_valid` | `modal_grid` is a syntactically valid 2/4/6/8-character locator. |
| `grid_precision` | Locator length actually used. |
| `grid_cell_lat_deg` | Positional uncertainty in latitude. A 4-character locator is 1° tall — about 111 km — which matters when plotting at high latitude. |
| `lat`, `lon` | Centre of `modal_grid`, degrees. |
| `hemisphere`, `abs_lat` | `N`/`S`, and \|latitude\|. |
| `cgm_lat`, `abs_cgm_lat` | AACGM-v2 geomagnetic latitude at the manifest's epoch. **Empty unless `aacgmv2` is installed.** For a polar study this is the coordinate that matters: geographic latitude does not tell you whether a station is inside the auroral oval. |

### Activity

Combined across both tables — a spot in either is evidence the station was
operating.

| Column | Meaning |
|---|---|
| `n_days_active` | **Exact** distinct UTC days with at least one spot. Derived from unioned day *sets*, not summed counts. |
| `duty_frac` | `n_days_active / period_days`. |
| `n_months_active` | Distinct months with activity. |
| `max_gap_days` | Longest run of consecutive silent days between the first and last appearance. `0` means it was heard every day. Directly useful for the station up/down timeline of polar-psws issue #11. |
| `first_spot`, `last_spot`, `first_day`, `last_day` | Period bounds. |
| `quality_tier` | `A` ≥ half the period's days; `B` ≥ 30 days; `C` ≥ 7; `D` fewer. **Receivers are never rejected** — they are registered installations, and the tier describes coverage, not plausibility. |
| `geo_suspect` | Set independently of the tier when `grid_valid` is false or more than three locators were reported. |

### Volume and reach

| Column | Meaning |
|---|---|
| `n_spots_wsprnet`, `n_spots_wd` | Spot counts per table — see the incomparability note above. |
| `n_slots_wsprnet`, `n_slots_wd` | Distinct 2-minute slots with a decode. A better activity measure than spot count for multi-receiver sites. |
| `n_partners_max_window_*` | Distinct transmitters heard in the **busiest single window** — exact for that window. |
| `n_partners_sum_windows_*` | Sum over windows: an **upper bound** on the period figure, not the figure itself. |
| `dist_mean_km_*`, `dist_max_km_*` | Exact. |
| `dist_p50_approx_km_*`, `dist_p90_approx_km_*` | Approximate — see "Exactness" below. |
| `snr_mean_*`, `snr_min_*`, `snr_max_*` | Exact. `snr` is integer-quantised to 1 dB and referenced to 2500 Hz, so most values are negative by construction; the median WSPR spot sits near −17 dB. |
| `snr_p50_approx_*` | Approximate. |
| `n_geo_invalid_frac_*` | Fraction of rows with an unusable latitude/longitude. |

**Why there is no exact period-wide distinct-partner count.** Distinct counts are
not additive across windows, and an unbounded `uniqExact` over the period exceeds
the proxy timeout — the 13-month version of a two-`uniqExact` query on `wspr.rx`
returns `504`. Rather than present a wrong number, both bounds are given and
neither is named as the period value.

### Noise capability (WsprDaemon receivers only)

| Column | Meaning |
|---|---|
| `noise_valid_frac` | Fraction of spots whose `rms_noise` passes `−200 … −50` dBm/Hz. **Low values mean this receiver cannot support noise analysis.** |
| `rms_noise_mean`, `c2_noise_mean` | Mean of the two estimators over valid spots, dBm in 1 Hz. |
| `noise_disagree_mean` | Mean \|`c2` − `rms`\|. Above ~10–15 dB indicates a broken `c2_noise`. |
| `ov_frac` | Fraction of spots with a front-end overload, which biases noise high. |
| `n_noise_ok` | Count of valid-noise spots. |

The validity window removes three distinct measured pathologies at once:
`rms_noise`/`c2_noise` exactly `0` (one receiver, `AC0G/S`, 100% of its rows),
`−999` (scattered single-slot failures), and `−49.9 … −45` (one receiver, `VK5EI`,
about 85 dB above the population median — an uncalibrated site, not a sentinel).
Physically plausible 20 m values span roughly −153 to −111 dBm/Hz.

**Absolute noise levels are not comparable between sites** without antenna
factors that are generally unknown; Griffiths et al. (2020) measured a ≥12 dB
offset between two sites using nominally identical equipment. Use relative
variation at one site, or difference each site against its own baseline.

---

## `tx_stations.csv` — one row per transmitting callsign

Identity, position, activity and volume columns match the receiver table, with
`base_call` in place of `site_call` (a `/P` suffix on a *transmitter* may be a
genuinely different location, so transmitters are not collapsed to sites).
Additional columns:

| Column | Meaning |
|---|---|
| `modal_power`, `modal_power_frac`, `n_distinct_power`, `powers`, `n_powers` | Reported TX power, dBm. A wide spread is a telemetry tell: those protocols cycle the power field to encode data. |
| `quality_tier` | `A`, `B`, `C`, `rejected` — see below. |
| `reject_reason` | Empty unless rejected: `synthetic_telemetry_callsign`, `too_few_spots`, `too_few_receivers`, `too_few_days`, `undecodable_grid`. |
| `n_spots_wd_raw` | Spots from WsprDaemon receivers; the `in_wsprdaemon` flag's backing number. |

### The transmitter quality gate

**Nothing is dropped.** The tier is a column so the notebook's filter is visible
and revisable without re-querying — these thresholds are judgement calls, not
physics.

| Tier | Criteria |
|---|---|
| `A` | Tier B, plus ≥ 10 active days, `modal_grid_frac` ≥ 0.9, ≤ 3 distinct powers. |
| `B` | Passes the minimum bar, plus ≥ 3 active days, ≤ 3 locators, `callsign_class` in {`amateur`, `hashed`}. |
| `C` | Passes the minimum bar but fails a plausibility test — many locators, or a callsign shape the ITU does not allocate. |
| `rejected` | Fails the minimum bar (≥ 10 spots, ≥ 2 receivers in a window, ≥ 2 days, decodable grid), **or** has a synthetic-telemetry callsign shape. |

**For any geographic plot, exclude `rejected`.** This is not a cosmetic
preference. Measured for June 2026 on `wspr.rx`:

| Filter | Distinct TX callsigns | Above \|70°\| latitude |
|---|---|---|
| None | 166,977 | 32,757 (N 17,215 / S 15,542) |
| Callsign shape only (drop leading `0`, `1`, `Q`) | 67,265 | 13,510 |
| ≥ 10 spots, ≥ 3 receivers | 53,708 | — |
| …**and ≥ 3 distinct days** | 4,629 | — |
| Full gate | 2,562 | **12** (N 4 / S 8) |

The decisive threshold is distinct days: 53,708 → 4,629. Telemetry callsigns are
ephemeral by construction — a new synthetic callsign per transmission cycle — so
persistence separates them from real stations far more cleanly than callsign shape
does. Callsign shape is corroboration, not the primary filter.

Why the callsign heuristic is principled rather than arbitrary: every synthetic
callsign observed in sampling (`Q51MXZ`, `0I5WQZ`, `128UAB`, `065MIT`, `177JWD`,
`1P9LXC`, …) begins with `0`, `1` or `Q`. The ITU allocates no prefix beginning
with `0` or `1`, and reserves the `Q` block for Q-codes, so none of these can be a
licensed station.

### The bias you must state when using this table

**Transmitter activity is only observable through receptions.** A transmitter
appears in the census only if someone heard it, so transmitter counts are
convolved with propagation and with receiver density. A polar transmitter map is
partly a map of who was listening — worth saying out loud in any figure caption,
particularly for the southern hemisphere where receiver coverage is sparse
(`wspr.rx` had **no receivers south of 43.8° S** in June 2026).

---

## `rx_station_band.csv` / `tx_station_band.csv` — station × band

| Column | Meaning |
|---|---|
| `rx_sign` / `tx_sign`, `band` | Key. `band` is a canonical label. |
| `n_spots_wsprnet`, `n_spots_wd_raw` | Per-band spot counts per table. |
| `wd_inflation` | `n_spots_wd_raw / n_spots_wsprnet` — approximately the number of active receiver instances at the site. |
| `n_months_wsprnet`, `n_months_wd` | Months in which this station used this band. |
| `first_spot_*`, `last_spot_*` | Per-band bounds. |

Rows are ordered by station then descending wavelength (2200 m first).

## `rx_station_month.csv` / `tx_station_month.csv` — station × month

Additive monthly metrics: `n_spots_*`, `n_slots_*`, `n_partners_max_window_*`,
`n_bands_*`. Exact because query windows never straddle a month boundary. Use
these for activity timelines and for spotting the month a station came on or went
off the air.

## `wd_receivers.csv` — one row per `(rx_sign, rx_id)`

`rx_id` is only unique **within** a site: values like `KA9Q_0` and `KIWI_1` recur
across many sites, so always key on the pair.

| Column | Meaning |
|---|---|
| `rx_sign`, `rx_id` | Key. |
| `n_spots`, `n_bands`, `bands` | Volume and coverage for this receiver instance. |
| `noise_valid_frac`, `rms_noise_mean`, `c2_noise_mean`, `ov_frac`, `n_noise_ok` | Noise capability, at the grain where the noise columns are actually meaningful. |
| `noise_usable` | `noise_valid_frac ≥ 0.5`. |
| `first_spot`, `last_spot` | Bounds. |

This table exists because a site can run one well-calibrated receiver and one
reporting nonsense, and a site-level average would hide it. Measured examples from
June 2026: `OE9GHV`/`KIWI_5` at −142.96 dBm/Hz (healthy) against `VK5EI`/`KA9Q_0`
at −54.59 with only 15% of its spots passing validity, and `W2KGY`/`KA9Q_0` at
−64.64 — both roughly 70–80 dB above the population median.

## `population.csv` — table × role × month

Unfiltered `n_stations_all`, `n_spots_all`, `n_grids_all` per month, so any
filtered figure can be quoted against the raw population it came from. This is
what keeps the census honest: a table of 2,562 transmitters means something very
different once you know 166,977 callsigns were present.

## `extraction_manifest.json`

Generation timestamp, package version, Python and platform, parent-repository git
commit, endpoint, period, chunking policy, per-family window counts and cache
completeness, every filter threshold with its rationale, output row counts, tier
populations, rejection-reason breakdown, the AACGM epoch, and the caveat list.

---

## Exactness of each metric

| Class | Metrics | Why |
|---|---|---|
| **Exact — additive** | spot counts, slot counts, SNR and distance sums and hence means, minima, maxima, invalid-geometry counts, overload counts | A window contributes each row exactly once |
| **Exact — set union** | active days, `max_gap_days`, bands, locators, versions, receiver instances, powers | `groupUniqArray` returns sets, not counts |
| **Bounded, not exact** | distinct partner counts | Not additive across windows; an unbounded `uniqExact` exceeds the proxy timeout. Both bounds reported, neither named as the period value |
| **Approximate** | `*_p50_approx`, `*_p90_approx` | Server-side `quantileTDigest` (bounded memory — exact quantiles over 200-million-row groups would be an unkind request to a volunteer-run host), then a spot-count-weighted mean of per-window medians. Use for ranking; use the exact means when precision matters |

## Known data pathologies this census surfaces rather than hides

1. **`rx_sign = '0'` with `rx_loc = 'K3LR'` / `'KPH2'`** — the site and receiver
   fields are swapped in 6 of 88 million rows. Flagged by `grid_valid = False`
   and `geo_suspect = True`.
2. **Mixed-case callsigns** across the two tables (see conventions above).
3. **`AC0G/B4`, `AC0G/B6`** — over a million extended spots in
   `wsprdaemon.spots` with no `wspr.rx` counterpart, i.e. substantial wsprnet
   drops. Surfaced as `source_class = wsprdaemon_only`.
4. **`snr` values of −99** appear at otherwise-healthy receivers; treat extreme
   `snr_min` with suspicion.
5. **The extended-spot record begins 2020-03-29**, so noise-based methods cannot
   reach earlier for any station. `wspr.rx` goes back to 2008 but has no noise.
