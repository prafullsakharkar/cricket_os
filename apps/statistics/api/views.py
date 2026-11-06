"""Phase F statistics/analytics read endpoints (CRICKET_API_SPECIFICATION §2).

Every endpoint is read-only, cache-invalidated by the ``statistics`` /
``standings`` version namespaces (bumped by the Group 2/3 event invalidators)
or the player-scoped ``statistics:player:{id}`` namespace, and honours the
standard envelope. Player-scoped endpoints answer ``301`` for merged ids,
mirroring the player detail route. Bad query params map to ``400
invalid_query``.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpResponse
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.cache.policy import CachePlan, for_player
from apps.core.cache_views import CachedReadMixin
from apps.core.errors import InvalidQuery
from apps.players.selectors import resolve_player_redirect
from apps.statistics.services import reads
from apps.statistics.services.compare import SUPPORTED_METRICS
from apps.statistics.services.leaderboard import (
    PLAYER_BATTING_METRICS,
    PLAYER_BOWLING_METRICS,
    PLAYER_FIELDING_METRICS,
)


def _redirect_if_merged(pk: Any) -> HttpResponse | None:
    """301 to the canonical player when ``pk`` is a merged-away id."""
    canonical = resolve_player_redirect(pk)
    if canonical is None:
        return None
    response = HttpResponse(status=301)
    response["Location"] = f"/api/v1/players/{canonical}/"
    return response


class _StatsBase(CachedReadMixin, APIView):
    """Shared caching: any statistics change bumps the ``statistics`` ns."""

    cache_namespace = "statistics"

    def cache_plan(self) -> Any:
        return CachePlan(redis_ttl=60, max_age=60)


# --------------------------------------------------------------------------- #
# Players
# --------------------------------------------------------------------------- #


class PlayerStatisticsView(_StatsBase):
    """``GET /players/{id}/statistics`` (API_SPEC §4.3, max-age=300)."""

    cache_namespace = "statistics:player"

    def cache_parts(self, request: Any, *args: Any, **kwargs: Any) -> tuple[str, ...]:
        return (kwargs["pk"],)

    def cache_version_ns(self, request: Any, *args: Any, **kwargs: Any) -> str:
        return f"statistics:player:{kwargs['pk']}"

    def cache_surrogates(self, request: Any, *args: Any, **kwargs: Any) -> tuple[str, ...]:
        return (f"player-{kwargs['pk']}",)

    def cache_plan(self) -> Any:
        return for_player()

    def get(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        redirect = _redirect_if_merged(kwargs["pk"])
        if redirect is not None:
            return redirect
        params = request.query_params
        try:
            data = reads.player_statistics(
                kwargs["pk"],
                scope=params.get("scope", "CAREER"),
                format_type=params.get("format") or None,
                gender=params.get("gender", "M"),
            )
        except ValueError as exc:
            raise InvalidQuery(str(exc)) from exc
        return Response(
            {
                "data": data,
                "meta": {
                    "official_source": data["source_kind"],
                    "scope": data["scope"],
                },
            }
        )


class PlayerFormView(_StatsBase):
    """``GET /players/{id}/form`` (max-age=120)."""

    cache_namespace = "statistics:player"

    def cache_parts(self, request: Any, *args: Any, **kwargs: Any) -> tuple[str, ...]:
        return (kwargs["pk"], "form")

    def cache_version_ns(self, request: Any, *args: Any, **kwargs: Any) -> str:
        return f"statistics:player:{kwargs['pk']}"

    def cache_surrogates(self, request: Any, *args: Any, **kwargs: Any) -> tuple[str, ...]:
        return (f"player-{kwargs['pk']}",)

    def cache_plan(self) -> Any:
        return CachePlan(redis_ttl=120, max_age=120)

    def get(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        redirect = _redirect_if_merged(kwargs["pk"])
        if redirect is not None:
            return redirect
        return Response({"data": reads.player_form(kwargs["pk"]), "meta": {}})


class PlayerCompareView(_StatsBase):
    """``GET /players/compare`` — 2–4 players, deltas vs the first (300)."""

    cache_namespace = "statistics"

    def cache_plan(self) -> Any:
        return CachePlan(redis_ttl=300, max_age=300)

    def get(self, request: Any) -> Any:
        params = request.query_params
        ids = params.getlist("ids")
        metric = params.get("metric")
        if metric is not None and metric not in SUPPORTED_METRICS:
            raise InvalidQuery("Unknown metric", details={"metric": metric})
        try:
            rows = reads.compare_players(
                ids,
                format_type=params.get("format") or None,
                metric=metric,
            )
        except ValueError as exc:
            raise InvalidQuery(str(exc)) from exc
        return Response({"data": rows, "meta": {"count": len(rows)}})


# --------------------------------------------------------------------------- #
# Teams / matches / series
# --------------------------------------------------------------------------- #


class TeamStatisticsView(_StatsBase):
    """``GET /teams/{id}/statistics`` (max-age=300)."""

    cache_namespace = "statistics"

    def cache_parts(self, request: Any, *args: Any, **kwargs: Any) -> tuple[str, ...]:
        return ("team", kwargs["pk"])

    def cache_plan(self) -> Any:
        return CachePlan(redis_ttl=300, max_age=300)

    def get(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        params = request.query_params
        data = reads.team_statistics(
            kwargs["pk"],
            scope=params.get("scope", "CAREER"),
            format_type=params.get("format") or None,
        )
        return Response({"data": data, "meta": {"official_source": data["source_kind"]}})


class MatchStatisticsView(_StatsBase):
    """``GET /matches/{id}/statistics`` — match-level leaderboards (15)."""

    cache_namespace = "statistics:match"

    def cache_parts(self, request: Any, *args: Any, **kwargs: Any) -> tuple[str, ...]:
        return (kwargs["pk"],)

    def cache_version_ns(self, request: Any, *args: Any, **kwargs: Any) -> str:
        return f"statistics:match:{kwargs['pk']}"

    def cache_plan(self) -> Any:
        return CachePlan(redis_ttl=15, max_age=15)

    def get(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        return Response({"data": reads.match_statistics(kwargs["pk"]), "meta": {}})


class SeriesPointsTableView(_StatsBase):
    """``GET /series/{id}/points-table`` (max-age=20, swr=20)."""

    cache_namespace = "standings"

    def cache_parts(self, request: Any, *args: Any, **kwargs: Any) -> tuple[str, ...]:
        return (kwargs["pk"],)

    def cache_version_ns(self, request: Any, *args: Any, **kwargs: Any) -> str:
        return f"standings:{kwargs['pk']}"

    def cache_plan(self) -> Any:
        return CachePlan(redis_ttl=20, max_age=20, swr=20)

    def get(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        params = request.query_params
        data = reads.series_points_table(kwargs["pk"], stage_id=params.get("stage") or None)
        return Response({"data": data, "meta": {"count": len(data["standings"])}})


class SeriesStatsView(_StatsBase):
    """``GET /series/{id}/stats`` — series leaderboards (max-age=20)."""

    cache_namespace = "statistics"

    def cache_parts(self, request: Any, *args: Any, **kwargs: Any) -> tuple[str, ...]:
        return ("series", kwargs["pk"])

    def cache_plan(self) -> Any:
        return CachePlan(redis_ttl=20, max_age=20)

    def get(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        params = request.query_params
        rows = reads.series_leaderboard(
            kwargs["pk"],
            format_type=params.get("format") or None,
            metric=params.get("metric"),
        )
        return Response({"data": rows, "meta": {"count": len(rows)}})


# --------------------------------------------------------------------------- #
# Leaderboards / records / H2H
# --------------------------------------------------------------------------- #


def _leaderboard_response(request: Any, metrics: frozenset[str]) -> Response:
    params = request.query_params
    metric = params.get("metric")
    if metric is not None and metric not in metrics:
        raise InvalidQuery("Unknown metric", details={"metric": metric})
    rows = reads.leaderboard_rows(
        scope=params.get("scope", "CAREER"),
        scope_ref_id=params.get("scope_ref") or None,
        format_type=params.get("format") or None,
        metric=metric,
    )
    # The endpoint serves one metric family (batting/bowling/fielding); rows
    # outside the family are never surfaced (e.g. fielding stays an honest
    # empty list until a per-match fielding source exists).
    rows = [r for r in rows if r["metric"] in metrics]
    return Response({"data": rows, "meta": {"count": len(rows)}})


class BattingLeaderboardView(_StatsBase):
    """``GET /statistics/batting`` (max-age=60)."""

    def get(self, request: Any) -> Any:
        return _leaderboard_response(request, PLAYER_BATTING_METRICS)


class BowlingLeaderboardView(_StatsBase):
    """``GET /statistics/bowling`` (max-age=60)."""

    def get(self, request: Any) -> Any:
        return _leaderboard_response(request, PLAYER_BOWLING_METRICS)


class FieldingLeaderboardView(_StatsBase):
    """``GET /statistics/fielding`` (max-age=60; honest empty when no source)."""

    def get(self, request: Any) -> Any:
        return _leaderboard_response(request, PLAYER_FIELDING_METRICS)


class TeamRecordsView(_StatsBase):
    """``GET /statistics/teams`` — team records catalogue (max-age=60)."""

    def get(self, request: Any) -> Any:
        data = reads.team_records(request.query_params.get("format") or None)
        return Response({"data": data, "meta": {"count": len(data)}})


class PlayerRecordsView(_StatsBase):
    """``GET /records/players`` (max-age=300)."""

    def cache_plan(self) -> Any:
        return CachePlan(redis_ttl=300, max_age=300)

    def get(self, request: Any) -> Any:
        data = reads.player_records(request.query_params.get("format") or None)
        return Response({"data": data, "meta": {"count": len(data)}})


class TeamRecordsCatalogueView(_StatsBase):
    """``GET /records/teams`` (max-age=300)."""

    def cache_plan(self) -> Any:
        return CachePlan(redis_ttl=300, max_age=300)

    def get(self, request: Any) -> Any:
        data = reads.team_records(request.query_params.get("format") or None)
        return Response({"data": data, "meta": {"count": len(data)}})


class HeadToHeadView(_StatsBase):
    """``GET /head-to-head?team1=&team2=&format=`` (max-age=300)."""

    def cache_plan(self) -> Any:
        return CachePlan(redis_ttl=300, max_age=300)

    def get(self, request: Any) -> Any:
        params = request.query_params
        team1, team2 = params.get("team1"), params.get("team2")
        if not team1 or not team2:
            raise InvalidQuery("team1 and team2 are required")
        data = reads.head_to_head(team1, team2, format_type=params.get("format") or None)
        return Response({"data": data, "meta": {}})


__all__ = [
    "BattingLeaderboardView",
    "BowlingLeaderboardView",
    "FieldingLeaderboardView",
    "HeadToHeadView",
    "MatchStatisticsView",
    "PlayerCompareView",
    "PlayerFormView",
    "PlayerRecordsView",
    "PlayerStatisticsView",
    "SeriesPointsTableView",
    "SeriesStatsView",
    "TeamRecordsCatalogueView",
    "TeamRecordsView",
    "TeamStatisticsView",
]
