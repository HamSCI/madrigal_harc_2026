# wsprsonde-stations

**A vendored snapshot of the HamSCI WSPRSonde network**, for overlay on the polar
station maps in [`notebooks/figures/`](../notebooks/figures/).

WSPRSondes are the *controlled* transmitters in the WSPR population: 8-band,
GPS-disciplined, ~1 W per band, transmitting WSPR and FST4W continuously from a known
position. Everything else in the census is an uncontrolled transmitter whose power,
frequency and grid are self-reported and often wrong — which is why
[`wspr-station-census`](../wspr-station-census/) needs quality tiers at all. This
directory is the short list of transmitters that do not need them.

Two of the eight currently active units are the polar PSWS sites this study is about:
**DP0GVN** (Neumayer III, Antarctica) and **VY0ERC** (Eureka, Nunavut).

---

## Provenance

**This is a copy. Do not edit it here.**

| | |
| --- | --- |
| Upstream | [`HamSCI/wsprsonde.hamsci.org`](https://github.com/HamSCI/wsprsonde.hamsci.org) |
| Snapshot taken | 2026-08-13 |
| Rebuild upstream with | `PYTHONPATH=src python3 -m wsprsonde.build_locations` |

Corrections belong upstream in `data/wsprsonde_stations.csv`; re-copy the outputs here
afterwards. Keeping a copy rather than a submodule is deliberate — this is three small
text files that a figure depends on, and pinning them by commit gives reproducible
figures without adding a submodule to a repository that has none.

The upstream registry reconciles four sources that did not agree with each other:

- `G3ZIL_WsprSonde_Metadata_V1-1.xlsx` — Gwyn Griffiths (G3ZIL) and Paul Elliott
  (WB6CXC), revised 2025-05-11
- the `wsprsonde` table on `wd10.wsprdaemon.org` (PostgreSQL `tutorial`), 139 rows of
  per-band transmit frequency to 1 mHz with validity intervals, maintained by G3ZIL
- frequency-offset assignments circulated by Paul Elliott, 2026-08-06
- WSPRSonde Shipping List #1, Gary Mikitin (AF8A), 2026-08-06

...and then checks the result against live observations from `wspr.rx` on the WsprDaemon
ClickHouse endpoint. Approximate dates, uncertain hardware attributions and city-derived
grid squares are all flagged in the `notes` column rather than silently cleaned.

---

## Files

### `wsprsonde_locations.csv` — use this one for plotting

One row per **unit**, with position and status resolved. This is the join of the curated
registry against the on-air record.

Columns that matter for a map:

| Column | Meaning |
| --- | --- |
| `station_id` | Stable per-unit key, independent of callsign |
| `site_id` | Groups units at one location into one dot. WB6CXC runs two WS-8s at CM88mj |
| `call` | Current transmitting callsign |
| `lat`, `lon` | Maidenhead **cell centre**, degrees. Blank where no locator is on record |
| `grid`, `grid_precision` | Locator and its length — see [Positions](#positions) |
| `record_status` | What the project believes: `deployed`, `retired`, `in_transit`, `pending_shipment`, `unknown` |
| `on_air_status` | What the radio is doing: `active`, `intermittent`, `silent` |
| `ok_to_list_public` | **Publication consent. See the warning below** |
| `offset_assigned_hz`, `offset_observed_hz`, `offset_check` | Coordinated channel vs. measured channel |
| `spots_in_window`, `reporters_in_window`, `bands_in_window` | Reception evidence over the manifest's window |

`record_status` and `on_air_status` are kept separate on purpose. A `deployed` station
that is `silent` is a work item, not a datum, and collapsing them would hide exactly the
rows worth looking at.

### `wsprsonde_stations.csv` — the curated registry

The hand-maintained administrative record `wsprsonde_locations.csv` derives from. Carries
no coordinates (positions are Maidenhead locators) and no observations. Included so this
directory is self-contained and so a reviewer can see what was asserted before anything
was measured.

### `wsprsonde_locations_manifest.json` — provenance for the above

Generation time, endpoint, query windows, every threshold applied, resulting counts, and
the caveats. **Cite the `generated_utc` field in any figure caption** — "active" is a
statement about a 2-day window ending at a specific instant, and it is meaningless
without it.

---

## Snapshot state, 2026-08-13

21 units at 20 sites: **8 active, 1 intermittent, 12 silent** (of which 3 retired, 1 in
transit, 5 awaiting shipment, 3 status unknown).

| Callsign | Site | Grid | Status |
| --- | --- | --- | --- |
| DP0GVN | Neumayer III, Antarctica | IB59ui | active |
| VY0ERC | Eureka, Nunavut | EQ79 | active |
| WB6CXC | Occidental, CA (×2 units) | CM88mj | active |
| WW0WWV | WWVB, Fort Collins, CO | DN70lq | active |
| KH2R | Kerhonkson, NY | FN21us | active |
| TI4JWC | Santa Barbara, Costa Rica | EK70wb | active |
| KD0EAG | Columbia, MO | EM38uw | active |
| N4RVE | Friday Harbor, WA | CN88ln | intermittent — last heard 2026-08-09 |

Five NSF-funded units shipped in August 2026 (AC0G, WB4DH, K7ZOO, KB0KFH, WA4KFZ) and
appear as `pending_shipment`. Only city-level 4-character grids are known for four of
them, and AC0G has no position at all — the unit ships to Missouri for installation in
North Dakota. **Do not plot `pending_shipment` rows as if they were deployed stations.**

---

## Positions

Same caveat as the rest of this study, and it bites harder here because the sample is
small enough that one wrong dot is visible.

Positions are **Maidenhead cell centres**, not surveyed coordinates. A 4-character
locator is 2° of longitude by 1° of latitude — about 78 km across at 40° latitude.
Two concrete cases in this file:

- **KH2R** reports only `FN21` to WSPRNet, whose centre is near Scranton, PA. The station
  is really at `FN21us` (Kerhonkson, NY), **~65 km away**. The curated 6-character
  locator is what is in `lat`/`lon` here; the WSPRNet one would put the dot in the wrong
  place.
- **VY0ERC** has only `EQ79` on record, at 80° N, where a 1°-tall cell straddles the
  field boundary — the same problem already documented for its receive-side locator in
  [`src/polar_psws/maps.py`](../src/polar_psws/maps.py).

Check `grid_precision` before treating a position as good to better than ~80 km.

---

## ⚠ Publication consent

`ok_to_list_public` carries the consent state inherited from the "OK to list on HamSCI?"
column of the G3ZIL metadata. **It reads `unknown` for all but two stations**, because
the source column was blank.

Callsigns and grid squares are public in the WSPR record, so plotting a station that is
already transmitting reveals nothing new. But the association of a callsign with a named
host, a funding source, or a HamSCI deployment is *not* public, and the pending-shipment
rows describe installations that have not happened yet.

**Before any figure from this file goes into a paper, poster or talk**, resolve the
consent state upstream. Nothing in this directory contains host names, addresses, phone
numbers or email addresses, and nothing derived from it should.

---

## Using it with the station maps

The station maps in [`src/polar_psws/maps.py`](../src/polar_psws/maps.py) already
collapse callsigns to sites and draw receivers and transmitters as separate series.
WSPRSondes are a natural fourth series: a handful of points, scientifically distinguished
from everything else on the plot, so the same treatment
[`maps.py`](../src/polar_psws/maps.py) gives WsprDaemon receivers applies — drawn last,
enlarged, its own marker shape, and a legend entry that says what it means.

Group by `site_id`, not `call`, to avoid drawing WB6CXC's two Occidental units as two
dots. Filter on `record_status == 'deployed'` unless you specifically want to show the
planned expansion, and if you do, give pending sites a distinct, obviously-provisional
mark — they are city-level positions for stations that are not yet transmitting.

Against the maps' current 22° latitude limit, all but one positioned unit is on-figure:
**DP0GVN** is the only southern-hemisphere station, and everything else is on the
northern map — including all four positioned pending sites, the southernmost of which is
WB4DH at 28.5° N. The single exception is **TI4JWC** at 10.06° N (Costa Rica), which is
equatorward of both maps and needs the full-network view rather than the polar panels.
