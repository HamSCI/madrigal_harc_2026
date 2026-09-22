"""Station-level census of WSPR transmitters and receivers.

This package builds tractable, station-level summary tables from the two large
WSPR spot tables mirrored on the WsprDaemon ClickHouse servers:

``wspr.rx``
    A clone of the wsprnet.org database — broad receiver coverage (thousands of
    receivers) but known to silently drop reported spots. ``band`` is coded as
    **integer MHz**.
``wsprdaemon.spots``
    "Extended" spots reported by official WsprDaemon client sites only — a much
    smaller receiver population, but loss-resistant by design and carrying a
    per-spot background-noise measurement. ``band`` is coded as **wavelength in
    meters**.

Both tables are reachable from the single endpoint ``http://wd10.wsprdaemon.org/``.

The census answers, per station and for a whole study period: where is it, when
was it active, on which bands, is it a transmitter or a receiver (or both), does
it report extended WsprDaemon noise data, what software does it run, and how
much should you trust the record.

See ``docs/COLUMNS.md`` for the output column dictionary and ``README.md`` for a
quickstart. The upstream database reference lives in the parent repository at
``docs/wsprdaemon_extended_spots_access.md``.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
