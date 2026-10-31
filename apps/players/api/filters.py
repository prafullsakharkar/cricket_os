"""Query-parameter parsing/validation for the players endpoints.

``country`` accepts a UUID or ISO code (see apps.teams.api.filters); ``team``
must be a UUID. ``sort`` is validated against the selector allow-list.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from apps.core.api import validate_ordering
from apps.core.errors import InvalidQuery
from apps.players.selectors import PLAYER_ORDERINGS


def _uuid_param(request: Any, name: str) -> UUID | None:
    raw = request.query_params.get(name)
    if not raw:
        return None
    try:
        return UUID(raw.strip())
    except ValueError as exc:
        raise InvalidQuery(f"{name} must be a UUID", details={name: raw}) from exc


def player_filters(request: Any) -> dict[str, Any]:
    """Validated ``list_players`` kwargs from the request query string."""
    from apps.teams.api.filters import resolve_country_param

    raw_country = request.query_params.get("country")
    return {
        "q": request.query_params.get("q") or None,
        "team": _uuid_param(request, "team"),
        "country": resolve_country_param(raw_country) if raw_country else None,
        "role": request.query_params.get("role") or None,
        "ordering": validate_ordering(request, PLAYER_ORDERINGS, default="full_name"),
    }


__all__ = ["player_filters"]
