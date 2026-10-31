"""Competition-domain write services (six-layer rule: selectors never import these)."""

from __future__ import annotations

from apps.competitions.services.competitions import (
    CompetitionResolver,
    SeriesResolver,
    upsert_competition,
    upsert_series,
)

__all__ = [
    "CompetitionResolver",
    "SeriesResolver",
    "upsert_competition",
    "upsert_series",
]
