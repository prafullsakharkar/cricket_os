"""Query-parameter parsing/validation for competitions and series endpoints.

``year`` must be an integer, ``category`` a UUID; ``sort`` is validated
against the selector allow-lists.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from apps.competitions.selectors import COMPETITION_ORDERINGS, SERIES_ORDERINGS
from apps.core.api import validate_ordering
from apps.core.errors import InvalidQuery


def competition_filters(request: Any) -> dict[str, Any]:
    """Validated ``list_competitions`` kwargs from the request query string."""
    return {
        "q": request.query_params.get("q") or None,
        "competition_type": request.query_params.get("type") or None,
        "ordering": validate_ordering(request, COMPETITION_ORDERINGS, default="name"),
    }


def series_filters(request: Any) -> dict[str, Any]:
    """Validated ``list_series`` kwargs from the request query string."""
    raw_year = request.query_params.get("year")
    raw_category = request.query_params.get("category")
    year: int | None = None
    if raw_year:
        try:
            year = int(raw_year)
        except ValueError as exc:
            raise InvalidQuery("year must be an integer", details={"year": raw_year}) from exc
    category: UUID | None = None
    if raw_category:
        try:
            category = UUID(raw_category.strip())
        except ValueError as exc:
            raise InvalidQuery(
                "category must be a UUID", details={"category": raw_category}
            ) from exc
    return {
        "year": year,
        "category": category,
        "series_type": request.query_params.get("type") or None,
        "ordering": validate_ordering(request, SERIES_ORDERINGS, default="start_date"),
    }


__all__ = ["competition_filters", "series_filters"]
