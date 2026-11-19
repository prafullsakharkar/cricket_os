"""Ranking read selectors (Phase F Group 5).

``list_rankings`` serves the **latest dated snapshot** for a (gender, format,
category) view ordered by rank — the API_SPEC §3.7 contract. ``player_history``
returns a player's dated snapshots newest-first. Both read only the imported
``player_ranking`` rows (provider data, never computed here).
"""

from __future__ import annotations

from typing import Any

from django.db.models import Max
from django.db.models.query import QuerySet

from apps.rankings.models import PlayerRanking

#: Default view + cap for ``GET /rankings`` (API_SPEC §3.7).
DEFAULT_GENDER = "M"
DEFAULT_FORMAT = "TEST"
DEFAULT_CATEGORY = "BATTING"
DEFAULT_LIMIT = 50


def list_rankings(
    *,
    gender: str = DEFAULT_GENDER,
    format_type: str = DEFAULT_FORMAT,
    category: str = DEFAULT_CATEGORY,
    limit: int = DEFAULT_LIMIT,
) -> tuple[QuerySet[PlayerRanking], str | None]:
    """Latest-snapshot rankings for one view, plus the snapshot date.

    Returns ``(rows, as_of_date)`` where ``as_of_date`` is the newest snapshot
    date (``None`` when no rows exist). Rows are ordered by rank with the
    stable (rank, player id) tie-break.
    """
    qs = PlayerRanking.objects.filter(gender=gender, format_type=format_type, category=category)
    latest = qs.aggregate(Max("as_of_date"))["as_of_date__max"]
    if latest is None:
        return PlayerRanking.objects.none(), None
    rows = qs.filter(as_of_date=latest).order_by("rank", "player_id")[:limit]
    return rows, str(latest)


def player_history(player_id: Any) -> QuerySet[PlayerRanking]:
    """A player's ranking snapshots, newest date first (then rank)."""
    return PlayerRanking.objects.filter(player_id=player_id).order_by(
        "-as_of_date", "format_type", "category", "rank"
    )


__all__ = [
    "DEFAULT_CATEGORY",
    "DEFAULT_FORMAT",
    "DEFAULT_GENDER",
    "DEFAULT_LIMIT",
    "list_rankings",
    "player_history",
]
