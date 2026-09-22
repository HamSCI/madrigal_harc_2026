# SQ3 figures — method notes

Everything that used to be printed on the figures themselves. The figures now carry a
title, a one-line subtitle, a legend, and nothing else; a reader who needs the method
comes here.

Regenerate both sets with:

```bash
.venv/bin/python maps/scripts/make_station_maps.py     # network coverage
.venv/bin/python maps/scripts/make_sampling_map.py     # where the network samples
```

Code and data provenance: [`maps/README.md`](../README.md).

---

## Common to both figures

**Period.** WSPR census, 2025-06-01 to 2026-07-01 (exclusive), 395 days, from the
WSPRDaemon ClickHouse mirrors.

**Callsigns are collapsed to physical sites.** `HB9VQQ/RS`, `/RL` and `/KE` are three
callsigns at one antenna field; `W7WKR`, `W7WKR-1`, `w7wkr-2` and `W7WKR-K1` are one
station. Plotting callsigns would draw a cluster where there is one dot of ground truth,
over-weighting multi-receiver sites exactly where they are densest.

**Site counts are not the callsign counts quoted in Feasibility.** These maps collapse
to sites and cover latitudes poleward of 22°: 18,597 northern and 1,297 southern
receiver sites correspond to the 22,015 receiving callsigns in the text, and 116 + 9
WSPRDaemon sites to the 149 quality-gated receivers with validated noise.

**The auroral zone is 60–75° corrected geomagnetic latitude**, AACGM-v2 at epoch
2025-12-15, shaded on both figures. Geomagnetic rather than geographic because the oval
follows the field: VY0ERC is at geographic 80.1° N but CGM 86.5° (polar cap), while
DP0GVN at −70.6° is only CGM −60.7°. Over the census, 234 receivers are poleward of 60°
geographic but equatorward of 60° CGM, and 35 are the reverse. The band is offset toward
North America because the geomagnetic pole is.

**WSPRSonde status** is from a vendored snapshot generated 2026-08-13 20:34:25 UTC. Only
`deployed` units are drawn. Every deployed unit is drawn filled: the fill would otherwise
encode a two-day reception window, and the only unit that would separate is N4RVE, whose
`intermittent` flag is a threshold artifact — 3.89 days since last spot against a 2-day
cutoff, after 3,460,430 spots on 9 bands with no missed day across the census. TI4JWC
(10.06° N, Costa Rica) is deployed and active but equatorward of 22°, so it appears on
neither map.

---

## `wspr_stations_{north,south}.png` — network coverage

**What it shows.** Every WSPR receiver and transmitter site in the census, with
receivers split by whether they report WSPRDaemon extended spots.

**Why the split matters.** WSPRDaemon receivers are the only sites reporting an
independent per-spot noise measurement. WSPR and FST4W reception records report an SNR estimate but
no noise term, so a change in reported SNR conflates a change in the received signal with
a change in band noise. Absorption is only quantifiable where the noise is measured.

**Transmitters are filtered by quality tier, and the filter is stated.** An unfiltered
transmitter map is fiction: for June 2026 the raw data places 32,757 transmitters above
70° absolute latitude, split almost evenly between hemispheres, from balloon-telemetry
callsigns carrying fabricated grid squares. Tiers A/B are plotted; 284,566 rejected
northern and 183,385 rejected southern sites are excluded, and those counts are printed
so the filter is visible rather than trusted.

**Transmitter density is not a map of transmitters.** A transmitter is observable only
through a reception, so its density is convolved with receiver coverage and propagation.
This layer is in part a map of who was listening.

**Marker size and opacity encode census quality tier.** Receivers: A active ≥198 d (half
the period), B ≥30 d, C ≥7 d, D <7 d. Transmitters: A = B + ≥10 d, one grid ≥90% of
spots, ≤3 power values; B = above the bar + ≥3 d, ≤3 grids, amateur/hashed callsign.

---

## `wspr_sampling_{north,south}.png` — where the network samples

**What it shows.** Great-circle paths between WSPRSonde transmitters and WSPRDaemon
receivers, and the path midpoints standing in for the regions they sound. The wider
WSPR population — 28,651 other receiver and transmitter sites on the northern panel —
is drawn beneath as a pale undifferentiated stipple: dropping it entirely overstated how
sparse the picture is, but it is context and does not carry the argument.

**Why these pairs.** Sondes are the controlled transmitters: known position, fixed
nominal power (1 W per band, eight bands), GPS-timed frame starts, and transmit
frequency locked to an external 10 MHz reference and known to the millihertz
(WS-8 Quick-Start v0.1; registry `offset_*` columns). The reference is a GPS-disciplined
oscillator at the five sites where the registry records one; at KH2R, DP0GVN, and
VY0ERC the `gpsdo` field is blank — some reference exists (the WS-8 faults without
one), but GNSS discipline there is unverified. WSPRDaemon receivers are the only
noise-measuring ones. A
path between the two is the only kind on which SQ3's absorption metric can actually be
computed, so these are the network's high-quality sampling.

**Why midpoints.** A WSPR link sounds the ionosphere along the path, not at its ends,
and for a single-hop path the reflection is near the middle. The midpoint is the
conventional proxy for the sampled region. Midpoints are computed on the sphere, not by
averaging latitude and longitude — the naive average is wrong everywhere and badly wrong
at high latitude and across the antimeridian, which is exactly where these paths run.

**Why 3,000 km.** Below roughly that ground range a path is plausibly single hop and one
midpoint is a fair stand-in for one reflection region. Beyond it multi-hop becomes
likely, the path samples several regions, and a single midpoint misrepresents it. **This
is a rule of thumb, not a derived bound** — ray tracing is the way to replace it with
something defensible, and doing so is open work. Long paths are drawn, because the links
are real, but **their midpoints are not plotted at all**: a midpoint is a claim about
where a path sounded the ionosphere, and on a multi-hop path the geometry does not
support that claim. Drawing it open still put a mark where nothing was measured. The
229 multi-hop midpoints that fall in the auroral zone are therefore a computed number
reported here, not a feature of the figure.

**These are geometric pairs, not observed links.** The committed census is station-level
and carries no transmitter-receiver pair table, so the figure draws the paths that
*could* close, not the ones that did. That is the right object for a siting question —
it is a capability map — but it is **not an occurrence rate**, and it should not be
described as one. Confirming which pairs actually close needs a spot-level query against
the WSPRDaemon mirrors.

### The result

| | Northern |
|---|---|
| WSPRSonde–WSPRDaemon paths on panel | 1,006 |
| of those, ≤ 3,000 km (single hop) | 279 |
| midpoints inside 60–75° CGM | 229 |
| **of those, single hop** | **0** |
| shortest path with a midpoint in the zone | 3,585 km |
| median path length in the zone | 5,336 km |

Of the 279 single-hop paths anywhere, only 3 have midpoints above 55° CGM.

The southern hemisphere has 191 paths, **no single-hop paths at all**, and no midpoints
in the southern auroral zone — DP0GVN is the only sonde south of the equator.

The same geometry, run against the three funded Antarctic sites, ranks them for SQ3:

| Path from DP0GVN | Ground range | Single hop | Midpoint CGM |
|---|---|---|---|
| SPA, Amundsen-Scott | 2,152 km | yes | **−68.4°, inside the auroral zone** |
| PLR, Palmer | 2,346 km | yes | −58.2°, just equatorward |
| MCM, McMurdo | 3,500 km | no | −73.0°, in the zone but not attributable |

A receiver at the South Pole would give the southern hemisphere its first single-hop
path with a midpoint inside the auroral zone. McMurdo is only just over the 3,000 km
rule of thumb, so it is exactly the case ray tracing could recover.

### Northern siting estimate

`maps/scripts/estimate_siting.py` runs the same test over twelve plausible Canadian
and Alaskan host towns against the deployed sonde fleet. Per candidate: single-hop
midpoints inside the band, poleward of it (the VY0ERC / polar-cap edge pairs at 75–79°
CGM), and in the 50–60° CGM adjacent band that serves the inside/adjacent/outside
comparison as control:

| Candidate | In band | Poleward edge | Adjacent control | In-band via |
|---|---|---|---|---|
| Fairbanks AK | 1 | 1 | 0 | N4RVE (61°) |
| Yellowknife NT | 1 | 1 | 2 | N4RVE (62°) |
| Churchill MB | 1 | 1 | 3 | N4RVE (62°) |
| Thompson MB | 1 | 1 | 4 | N4RVE (60°) |
| Iqaluit NU | 1 | 1 | 0 | KH2R (61°) |
| Fort McMurray AB | 0 | 1 | 4 | — |
| La Ronge SK | 0 | 1 | 5 | — |
| Whitehorse YT | 0 | 1 | 2 | — |
| Anchorage AK | 0 | 1 | 1 | — |
| Goose Bay NL / Timmins ON / St. John's NL | 0 | 0 | 1–3 | — |

Two things fall out. **VY0ERC brackets the zone from the pole side**: its single-hop
midpoints to every northern candidate land at 75–79° CGM, so any Canadian receiver
samples both the band's equatorward flank (via the CONUS sondes) and the polar-cap
boundary (via Eureka). And **the five pending-shipment sondes do not reach the band** —
all sit at 28–38° N, too far south for any candidate; AC0G's planned North Dakota
installation is the one to re-run once it has a position.

These are controlled-pair floors. Every candidate additionally hears the ambient WSPR
population (census median 647 transmitters for receivers above 55° CGM), which supplies
the statistical bulk; the sonde pairs are the subset you can put error bars on.

**Read it this way.** The network does reach the auroral zone: 229 midpoints land in the
band. But every one of them is on a path long enough that the midpoint cannot be trusted
to locate the sampling, while the paths short enough to attribute cluster immediately
equatorward of the band. The gap is in **attributable** coverage — a geometry
problem a new receiver can fix, because what closes it is a
transmitter and a receiver bracketing the zone within one hop of each other.
