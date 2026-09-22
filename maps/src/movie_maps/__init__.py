"""Polar-PSWS study analysis code.

This package holds analysis specific to the polar-PSWS investigation. Reusable,
collaborator-facing tooling lives in the ``wspr-station-census/`` sub-project at
the repository root instead; this package consumes its output CSVs.

Modules
-------
maps
    Polar stereographic station maps built from a ``wspr-station-census`` run.
"""

from __future__ import annotations

__all__ = ["maps"]
