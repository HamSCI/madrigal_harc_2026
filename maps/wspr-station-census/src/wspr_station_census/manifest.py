"""Run provenance: what was queried, when, with what code, and what was filtered.

The parent project's AI-governance and NSF reporting rules require that factual
claims be traceable to a source. A summary CSV on its own cannot be audited: a
file listing 2,562 transmitters gives no indication that 166,977 callsigns were
present in the raw month, that a pre-gate ran server-side, or which mirror
answered. The manifest records all of it next to the CSVs.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from . import __version__, extract, geo
from .config import CensusConfig
from .summarize import CensusTables


def _git_commit(repo: Path) -> str | None:
    """Return the short HEAD commit of ``repo``, or None if unavailable."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def _tier_counts(frame: pd.DataFrame) -> dict[str, int]:
    """Return ``{tier: count}`` for a summary frame, empty if untiered."""
    if frame is None or frame.empty or "quality_tier" not in frame.columns:
        return {}
    return {str(k): int(v) for k, v in frame["quality_tier"].value_counts().items()}


def _reason_counts(frame: pd.DataFrame) -> dict[str, int]:
    """Return ``{reject_reason: count}`` for rejected rows."""
    if frame is None or frame.empty or "reject_reason" not in frame.columns:
        return {}
    reasons = frame.loc[frame["reject_reason"].astype(str) != "", "reject_reason"]
    return {str(k): int(v) for k, v in reasons.value_counts().items()}


def reconcile(tables: CensusTables) -> dict[str, Any]:
    """Cross-check the summary tables against the independent population census.

    The ``population`` family counts spots with a plain ``count()`` per month,
    computed by the database and never passed through the rollup logic. Summing
    ``n_spots`` over the receiver table must reproduce it exactly, because every
    spot in the period is attributed to exactly one receiver.

    This is a genuine end-to-end check on the rollup arithmetic rather than a
    formality: during development a compaction bug inflated these sums by six
    orders of magnitude, and this comparison is what makes such a failure
    self-evident in the manifest instead of silently plausible in a CSV.

    Parameters
    ----------
    tables : CensusTables

    Returns
    -------
    dict
        Per source table: the population total, the summed station total, their
        ratio, and an ``ok`` flag (exact agreement).
    """
    out: dict[str, Any] = {}
    population = tables.population
    receivers = tables.rx_stations
    if population is None or population.empty or receivers is None or receivers.empty:
        return out
    for table_key, column in (("wsprnet", "n_spots_wsprnet"), ("wsprdaemon", "n_spots_wd")):
        rows = population[(population["table"] == table_key) & (population["role"] == "rx")]
        if rows.empty or column not in receivers.columns:
            continue
        expected = int(rows["n_spots_all"].sum())
        actual = int(pd.to_numeric(receivers[column], errors="coerce").fillna(0).sum())
        out[table_key] = {
            "population_count_spots": expected,
            "rx_stations_sum_spots": actual,
            "ratio": round(actual / expected, 6) if expected else None,
            "ok": actual == expected,
        }
    months = tables.rx_station_month
    if months is not None and not months.empty and "n_spots_wsprnet" in months.columns:
        by_station = months.groupby("rx_sign")["n_spots_wsprnet"].sum()
        station_totals = receivers.set_index("rx_sign")["n_spots_wsprnet"]
        joined = pd.concat([by_station, station_totals], axis=1, keys=["m", "s"]).dropna()
        diff = (joined["m"] - joined["s"]).abs().max() if not joined.empty else 0
        out["station_month_vs_station"] = {
            "receivers_compared": int(len(joined)),
            "max_abs_difference": int(diff),
            "ok": bool(diff == 0),
        }
    return out


def build_manifest(
    config: CensusConfig,
    tables: CensusTables,
    extraction: dict[str, Any] | None = None,
    written: dict[str, str] | None = None,
    geomag_epoch: str | None = None,
) -> dict[str, Any]:
    """Assemble the run manifest.

    Parameters
    ----------
    config : CensusConfig
    tables : CensusTables
        The summary tables, for row counts and tier populations.
    extraction : dict, optional
        Return value of :func:`~wspr_station_census.extract.extract`.
    written : dict, optional
        ``{name: path}`` from :func:`~wspr_station_census.summarize.write_tables`.
    geomag_epoch : str, optional
        Epoch used for the AACGM-v2 conversion.

    Returns
    -------
    dict
        JSON-serialisable manifest.
    """
    period_days = (config.end_date - config.start_date).days
    families = extract.build_families(config)
    cache = extract.cache_report(config)

    population = tables.population
    pop_summary: dict[str, Any] = {}
    if population is not None and not population.empty:
        for (table, role), group in population.groupby(["table", "role"]):
            pop_summary[f"{table}.{role}"] = {
                "months": int(len(group)),
                "max_monthly_distinct_stations": int(group["n_stations_all"].max()),
                "total_spots": int(group["n_spots_all"].sum()),
            }

    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "package": {
            "name": "wspr-station-census",
            "version": __version__,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "polar_psws_commit": _git_commit(Path(__file__).resolve().parents[3]),
        },
        "query": {
            "endpoint": config.endpoint,
            "period_start": config.start,
            "period_end_exclusive": config.end,
            "period_days": period_days,
            "tables": ["wspr.rx", "wsprdaemon.spots"],
            "stats_chunk_days": config.stats_chunk_days,
            "detail_chunk_days": config.detail_chunk_days,
            "families": [
                {"name": fam.name, "windows": len(fam.windows), "description": fam.description}
                for fam in families
            ],
            "cache": cache.to_dict(orient="records") if not cache.empty else [],
            "extraction": extraction or {},
        },
        "filters": {
            "tx_server_side_pregate": {
                "min_spots_per_window": extract.TX_PREGATE_MIN_SPOTS,
                "min_distinct_receivers_per_window": extract.TX_PREGATE_MIN_PARTNERS,
                "rationale": (
                    "The unfiltered transmitter population is ~167,000 callsigns per "
                    "month, dominated by balloon-telemetry callsigns with fabricated "
                    "grids and by single false decodes. Callsigns not heard at least "
                    "twice by at least two receivers within a window cannot pass any "
                    "downstream persistence gate."
                ),
            },
            "tx_detail_min_spots_per_window": extract.TX_DETAIL_MIN_SPOTS,
            "wd_membership_min_spots_per_window": extract.WD_MEMBERSHIP_MIN_SPOTS,
            "noise_validity": (
                "rms_noise BETWEEN -200 AND -50 dBm/Hz; removes the 0 and -999 "
                "sentinels and grossly miscalibrated sites"
            ),
            "geometry": (
                "positions derived from the Maidenhead grid, not from the lat/lon "
                "columns, which carry -999 sentinels (1.4% of wsprdaemon.spots rows) "
                "and out-of-range latitudes in both tables"
            ),
        },
        "results": {
            "rows": {
                name: (0 if frame is None or frame.empty else int(len(frame)))
                for name, frame in tables.as_dict().items()
            },
            "rx_quality_tiers": _tier_counts(tables.rx_stations),
            "tx_quality_tiers": _tier_counts(tables.tx_stations),
            "tx_reject_reasons": _reason_counts(tables.tx_stations),
            "unfiltered_population": pop_summary,
            "reconciliation": reconcile(tables),
        },
        "geomagnetic": {
            "aacgmv2_available": geo.aacgm_available(),
            "epoch": geomag_epoch,
            "note": (
                "cgm_lat is NaN when aacgmv2 is not installed; install with "
                "pip install 'wspr-station-census[geomag]'"
            ),
        },
        "caveats": [
            "n_partners_* are per-window bounds, not period-exact distinct counts: "
            "distinct partner counts are not additive across windows and an "
            "unbounded uniqExact over the period exceeds the proxy timeout.",
            "Columns ending _p50_approx / _p90_approx are spot-count-weighted means "
            "of per-window quantileTDigest results. Means, minima and maxima are exact.",
            "wspr.rx keeps the strongest decode per (time, tx, rx, band) while "
            "wsprdaemon.spots keeps every receiver instance separately, so "
            "n_spots_wsprnet and n_spots_wd_raw are not comparable without reduction.",
            "Transmitter activity is only observable through receptions, so "
            "transmitter counts are convolved with propagation and with receiver "
            "density. A transmitter map is partly a map of who was listening.",
            "band is coded as integer MHz in wspr.rx and as wavelength in meters in "
            "wsprdaemon.spots; the codes collide (band=40 is 8 m in one and 40 m in "
            "the other). Both are decoded to canonical labels such as '20m'.",
            "The extended-spot record begins 2020-03-29, so noise-based methods "
            "cannot reach earlier than that for any station.",
        ],
        "outputs": written or {},
    }


def write_manifest(manifest: dict[str, Any], config: CensusConfig) -> Path:
    """Write the manifest as JSON next to the CSVs and return its path."""
    config.output_dir.mkdir(parents=True, exist_ok=True)
    path = config.output_dir / "extraction_manifest.json"
    path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    return path
