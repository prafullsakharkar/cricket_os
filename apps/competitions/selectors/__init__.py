"""Read-optimised competition-domain queries (no service imports)."""

from __future__ import annotations

from apps.competitions.selectors.competitions import (
    get_competition,
    get_format,
    get_series,
    list_competitions,
    list_series,
)

__all__ = [
    "get_competition",
    "get_format",
    "get_series",
    "list_competitions",
    "list_series",
]
