"""Read-optimised competition-domain queries (no service imports)."""

from __future__ import annotations

from apps.competitions.selectors.competitions import (
    COMPETITION_ORDERINGS,
    SERIES_ORDERINGS,
    get_competition,
    get_format,
    get_series,
    list_competitions,
    list_series,
)

__all__ = [
    "COMPETITION_ORDERINGS",
    "SERIES_ORDERINGS",
    "get_competition",
    "get_format",
    "get_series",
    "list_competitions",
    "list_series",
]
