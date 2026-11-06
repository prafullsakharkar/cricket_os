"""Rankings read endpoints (CRICKET_API_SPECIFICATION §3.7).

* ``GET /rankings`` — the latest dated snapshot for one view
  (``?gender=&format=&category=&limit=``), rows ordered by rank. Served from
  provider-supplied data (``meta.source="provider"``), cached ``max-age=60``.
* ``GET /players/{id}/rankings`` — a player's ranking history, newest
  snapshot first (``max-age=300``).

Both are read-only and cache-invalidated by the ``rankings`` version namespace
(re-import bumps it); the payloads are plain dicts — no ORM objects cached.
"""

from __future__ import annotations

from typing import Any

from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.cache.policy import CachePlan, for_player
from apps.core.cache_views import CachedReadMixin
from apps.rankings.selectors import (
    DEFAULT_CATEGORY,
    DEFAULT_FORMAT,
    DEFAULT_GENDER,
    DEFAULT_LIMIT,
    list_rankings,
    player_history,
)


def _row_dict(row: Any) -> dict[str, Any]:
    return {
        "rank": row.rank,
        "player": {"id": str(row.player_id), "name": row.player.full_name},
        "team": {"name": row.team.name} if row.team_id else None,
        "rating": row.rating,
        "points": str(row.points) if row.points is not None else None,
        "trend": row.trend,
        "best": {"rank": row.best_rank, "rating": row.best_rating},
    }


class RankingsListView(CachedReadMixin, APIView):
    """``GET /rankings`` — latest snapshot for a gender/format/category view."""

    cache_namespace = "rankings"

    def cache_plan(self) -> Any:
        # API_SPEC §3.7: max-age=60 (provider data changes weekly at most).
        return CachePlan(redis_ttl=60, max_age=60)

    def get(self, request: Any) -> Response:
        params = request.query_params
        rows, as_of = list_rankings(
            gender=params.get("gender", DEFAULT_GENDER),
            format_type=params.get("format", DEFAULT_FORMAT),
            category=params.get("category", DEFAULT_CATEGORY),
            limit=int(params.get("limit", DEFAULT_LIMIT)),
        )
        return Response(
            {
                "data": [_row_dict(r) for r in rows],
                "meta": {
                    "source": "provider",
                    "as_of_date": as_of,
                    "count": len(rows),
                },
            }
        )


class PlayerRankingsView(CachedReadMixin, APIView):
    """``GET /players/{id}/rankings`` — one player's history, newest first."""

    cache_namespace = "player-rankings"

    def cache_parts(self, request: Any, *args: Any, **kwargs: Any) -> tuple[str, ...]:
        return (kwargs["pk"],)

    def cache_version_ns(self, request: Any, *args: Any, **kwargs: Any) -> str:
        return f"rankings:{kwargs['pk']}"

    def cache_surrogates(self, request: Any, *args: Any, **kwargs: Any) -> tuple[str, ...]:
        return (f"player-{kwargs['pk']}",)

    def cache_plan(self) -> Any:
        # API_SPEC §2.2: max-age=300 (matches the player profile plan).
        return for_player()

    def get(self, request: Any, *args: Any, **kwargs: Any) -> Response:
        rows = player_history(kwargs["pk"])
        return Response(
            {
                "data": [
                    {
                        "as_of_date": str(r.as_of_date),
                        "format": r.format_type,
                        "category": r.category,
                        "rank": r.rank,
                        "rating": r.rating,
                        "trend": r.trend,
                        "best_rank": r.best_rank,
                        "best_rating": r.best_rating,
                    }
                    for r in rows
                ],
                "meta": {"source": "provider", "count": len(rows)},
            }
        )


__all__ = ["PlayerRankingsView", "RankingsListView"]
