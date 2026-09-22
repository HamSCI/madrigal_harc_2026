"""Command-line interface: ``wspr-census``.

Subcommands
-----------
``extract``
    Query the database and cache the raw responses. Resumable; safe to interrupt.
``summarize``
    Build the CSVs and the manifest from the cache. No network access.
``run``
    ``extract`` then ``summarize``.
``status``
    Report cache completeness per query family without querying anything.
``plan``
    Print the query plan, and optionally the SQL, without running it.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from .config import DEFAULT_END, DEFAULT_START, CensusConfig
from .extract import build_families, cache_report, extract
from .manifest import build_manifest, write_manifest
from .summarize import mid_period_epoch, summarize, write_tables


def _config_from_args(args: argparse.Namespace) -> CensusConfig:
    """Build a :class:`CensusConfig` from parsed arguments."""
    return CensusConfig(
        start=args.start,
        end=args.end,
        data_dir=Path(args.data_dir).expanduser() if args.data_dir else None,
        endpoint=args.endpoint,
        stats_chunk_days=args.stats_chunk_days,
        detail_chunk_days=args.detail_chunk_days,
        pace_s=args.pace,
    )


def _add_common(parser: argparse.ArgumentParser) -> None:
    """Add the arguments shared by every subcommand."""
    parser.add_argument("--start", default=DEFAULT_START, help="period start, YYYY-MM-DD")
    parser.add_argument(
        "--end", default=DEFAULT_END, help="period end (exclusive), YYYY-MM-DD"
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="cache/output root (default: $POLAR_PSWS_DATA_DIR or ~/polar_psws_data)",
    )
    parser.add_argument(
        "--endpoint",
        default=CensusConfig.endpoint,
        help="ClickHouse HTTP endpoint (default: %(default)s)",
    )
    parser.add_argument("--stats-chunk-days", type=int, default=10, help="stats window cap")
    parser.add_argument("--detail-chunk-days", type=int, default=15, help="detail window cap")
    parser.add_argument(
        "--pace", type=float, default=0.5, help="minimum seconds between requests"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser."""
    parser = argparse.ArgumentParser(
        prog="wspr-census",
        description=(
            "Build station-level summaries of WSPR transmitters and receivers from "
            "the WsprDaemon ClickHouse mirrors."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_extract = sub.add_parser("extract", help="query the database into the local cache")
    _add_common(p_extract)
    p_extract.add_argument(
        "--only", nargs="+", default=None, help="restrict to these query families"
    )
    p_extract.add_argument(
        "--refresh", action="store_true", help="re-query windows already cached"
    )

    p_sum = sub.add_parser("summarize", help="build the CSVs from the cache (no network)")
    _add_common(p_sum)
    p_sum.add_argument(
        "--geomag-epoch",
        default=None,
        help="YYYY-MM-DD epoch for AACGM-v2 (default: middle of the period)",
    )

    p_run = sub.add_parser("run", help="extract then summarize")
    _add_common(p_run)
    p_run.add_argument("--geomag-epoch", default=None)
    p_run.add_argument("--refresh", action="store_true")

    p_status = sub.add_parser("status", help="report cache completeness")
    _add_common(p_status)

    p_plan = sub.add_parser("plan", help="print the query plan")
    _add_common(p_plan)
    p_plan.add_argument(
        "--sql", action="store_true", help="also print the first SQL of each family"
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns a process exit status."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    config = _config_from_args(args)

    if args.command == "status":
        report = cache_report(config)
        print(report.to_string(index=False))
        total = int(report["bytes"].sum())
        print(
            f"\ncache root: {config.cache_dir}\n"
            f"{report['cached'].sum()}/{report['expected'].sum()} windows, "
            f"{total / 1e6:.1f} MB"
        )
        return 0

    if args.command == "plan":
        families = build_families(config)
        print(f"period {config.start} .. {config.end} (exclusive)")
        print(f"cache  {config.cache_dir}")
        print(f"output {config.output_dir}\n")
        total = 0
        for fam in families:
            total += len(fam.windows)
            print(f"{fam.name:<32} {len(fam.windows):>4} windows   {fam.description}")
            if args.sql:
                lo, hi = fam.windows[0]
                print("\n" + fam.build(lo, hi) + "\n")
        print(f"\n{total} queries total")
        return 0

    if args.command in {"extract", "run"}:
        extraction = extract(
            config,
            only=getattr(args, "only", None),
            refresh=getattr(args, "refresh", False),
        )
    else:
        extraction = None

    if args.command == "extract":
        return 0

    # Resolve the epoch here rather than inside summarize(), so the manifest
    # records the value actually used. cgm_lat depends on it, so a manifest
    # saying "None" would leave those columns unreproducible.
    epoch = getattr(args, "geomag_epoch", None) or mid_period_epoch(config)
    tables = summarize(config, geomag_epoch=epoch)
    written = write_tables(tables, config)
    manifest = build_manifest(
        config,
        tables,
        extraction=extraction,
        written=written,
        geomag_epoch=epoch,
    )
    path = write_manifest(manifest, config)
    print(f"\nwrote {len(written)} tables and the manifest to {config.output_dir}")
    print(f"manifest: {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
