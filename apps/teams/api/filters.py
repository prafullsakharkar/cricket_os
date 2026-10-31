"""Query-parameter parsing/validation for the teams endpoints.

``country`` accepts either a UUID or a two-letter ISO-3166 alpha-2 code; an
unknown code (or anything else) is a 400 ``invalid_query``.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from apps.core.api import validate_ordering
from apps.core.errors import InvalidQuery
from apps.teams.selectors import TEAM_ORDERINGS
from apps.venues.models import Country


def resolve_country_param(value: str) -> UUID:
    """``value`` as a Country UUID: either the UUID itself or an ISO code."""
    try:
        return UUID(value)
    except ValueError:
        pass
    country = (
        Country.objects.filter(iso_code=value.strip().upper()).values_list("pk", flat=True).first()
    )
    if country is not None:
        return country
    raise InvalidQuery("Unknown country", details={"country": value})


def team_filters(request: Any) -> dict[str, Any]:
    """Validated ``list_teams`` kwargs from the request query string."""
    raw_country = request.query_params.get("country")
    return {
        "q": request.query_params.get("q") or None,
        "category": request.query_params.get("category") or None,
        "country": resolve_country_param(raw_country) if raw_country else None,
        "ordering": validate_ordering(request, TEAM_ORDERINGS, default="name"),
    }


__all__ = ["resolve_country_param", "team_filters"]
