"""Fantasy public read endpoints (Task 7).

Three read-only endpoints behind ``FANTASY_PUBLIC_API_ENABLED`` (OFF by
default — the product/legal gate, docs/FANTASY_PRODUCT_DECISION.md, is
ABSENT):

* ``GET /fantasy/rulesets`` (+ ``/fantasy/rulesets/{id}/versions/{version}``)
  — the public (non-draft) ruleset catalogue, ``max-age=3600``;
* ``GET /fantasy/points`` — page-number paginated per-player points with
  ``match`` / ``player`` / ``ruleset_version`` filters, ETag/304;
* ``GET /fantasy/matches/{id}`` — all players for the match's primary
  version, ordered, component breakdown, ``max-age=120``.

Every response carries ``meta`` with ``label: "derived"`` and the third-party
notice; points responses identify the calc run that produced them
(``calc_run_id``, ``status`` final|provisional, ``is_partial``,
``unsupported_rules``, ``as_of``, ``ruleset`` id/version/hash). Draft versions
are never public. Merged players answer 301 to the canonical query. Reads go
through the Phase E read cache and never touch ``delivery``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from django.conf import settings
from django.http import Http404, HttpResponse
from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.cache.policy import CachePlan
from apps.core.cache_views import CachedReadMixin
from apps.core.errors import InvalidQuery, NotFound
from apps.core.pagination import PageNumberPagination
from apps.fantasy.api.serializers import (
    MatchPointsSerializer,
    PointsRowSerializer,
    RulesetListSerializer,
    RulesetVersionDetailSerializer,
)
from apps.fantasy.engine import RulesetPolicies
from apps.fantasy.models import (
    FantasyCalcAudit,
    FantasyRuleset,
    FantasyRulesetVersion,
    PlayerMatchPerformance,
    RulesetVersionStatus,
)
from apps.matches.models import Match
from apps.players.selectors.players import resolve_player_redirect

#: Response ``meta.label`` — every fantasy payload is a derived computation.
FANTASY_LABEL = "derived"

#: Third-party notice required on every fantasy response (plan Task 7).
FANTASY_NOTICE = (
    "Points are computed under the stated ruleset and are not any third "
    "party's official fantasy points."
)

#: Engine policy flag names (mirrors authoring/recompute so the catalogue can
#: show the *effective* policies with the engine defaults resolved).
_POLICY_FIELDS = ("super_over", "abandoned", "reduced_overs", "retirement_hurt", "substitutes")


def _effective_policies(version: FantasyRulesetVersion) -> dict[str, Any]:
    """The effective engine policies: stored overrides over the defaults."""
    defaults = RulesetPolicies()
    values = {name: getattr(defaults, name) for name in _POLICY_FIELDS}
    values.update(version.policies or {})
    return values


def _calc_status(run: FantasyCalcAudit | None) -> str | None:
    """The API's ``status`` label for a calc run (final|provisional)."""
    if run is None:
        return None
    return "provisional" if run.is_provisional else "final"


def _as_uuid(raw: Any, name: str) -> UUID:
    """Parse a query-param uuid, else 400 ``invalid_query``."""
    try:
        return UUID(str(raw))
    except ValueError:
        raise InvalidQuery(f"{name} must be a uuid", details={name: str(raw)}) from None


def _redirect_if_merged(request: Any) -> HttpResponse | None:
    """301 to the canonical player when ``?player=`` is a merged-away id."""
    player_id = request.query_params.get("player")
    if not player_id:
        return None
    try:
        canonical = resolve_player_redirect(UUID(str(player_id)))
    except ValueError:
        return None  # malformed: the normal validation answers 400
    if canonical is None:
        return None
    params = {key: value for key, value in request.query_params.items() if key != "player"}
    params["player"] = str(canonical)
    response = HttpResponse(status=301)
    response["Location"] = "/api/v1/fantasy/points?" + urlencode(params)
    return response


def _version_dict(version: FantasyRulesetVersion) -> dict[str, Any]:
    """The catalogue representation of a version."""
    return {
        "id": version.id,
        "version": version.version,
        "format_scope": version.format_scope,
        "effective_from": version.effective_from,
        "effective_to": version.effective_to,
        "status": version.status,
        "is_default": version.is_default,
        "auto_recompute": version.auto_recompute,
        "content_hash": version.content_hash,
        "policies": _effective_policies(version),
    }


def _rule_dict(rule: Any) -> dict[str, Any]:
    """The version-detail representation of one point rule."""
    return {
        "event_type": rule.event_type,
        "role_scope": rule.role_scope,
        "points": rule.points,
        "condition": rule.condition or {},
    }


class _FantasyBase(CachedReadMixin, APIView):
    """Read-only fantasy endpoint base: flag-gated, cached under ``fantasy``.

    The flag check happens before the cache lookup so a disabled API is a 404
    even when stale cache entries exist (the routes are also only registered
    when the flag is on).
    """

    cache_namespace = "fantasy"

    def dispatch(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        if not getattr(settings, "FANTASY_PUBLIC_API_ENABLED", False):
            raise Http404("fantasy API is disabled")
        return super().dispatch(request, *args, **kwargs)

    def _base_meta(self, **extra: Any) -> dict[str, Any]:
        meta: dict[str, Any] = {"label": FANTASY_LABEL, "notice": FANTASY_NOTICE}
        meta.update(extra)
        return meta


class FantasyRulesetListView(_FantasyBase):
    """GET /api/v1/fantasy/rulesets (max-age=3600)."""

    cache_namespace = "fantasy:rulesets"

    def cache_plan(self) -> Any:
        return CachePlan(redis_ttl=3600, max_age=3600)

    @extend_schema(responses={200: RulesetListSerializer})
    def get(self, request: Any) -> Any:
        rulesets = FantasyRuleset.objects.filter(is_active=True).prefetch_related("versions")
        data: list[dict[str, Any]] = []
        for ruleset in rulesets:
            public_versions = [
                _version_dict(version)
                for version in ruleset.versions.all()
                if version.status != RulesetVersionStatus.DRAFT
            ]
            if not public_versions:
                continue  # a ruleset with nothing public is not in the catalogue
            data.append(
                {
                    "id": ruleset.id,
                    "name": ruleset.name,
                    "provider_scope": ruleset.provider_scope,
                    "description": ruleset.description,
                    "versions": public_versions,
                }
            )
        serializer = RulesetListSerializer(data, many=True, context={"request": request})
        meta = self._base_meta(count=len(data))
        return Response({"data": serializer.data, "meta": meta})


class FantasyRulesetVersionDetailView(_FantasyBase):
    """GET /api/v1/fantasy/rulesets/{ruleset_id}/versions/{version} (3600)."""

    cache_namespace = "fantasy:rulesets"

    def cache_parts(self, request: Any, *args: Any, **kwargs: Any) -> tuple[str, ...]:
        return (kwargs["ruleset_id"], kwargs["version"])

    def cache_plan(self) -> Any:
        return CachePlan(redis_ttl=3600, max_age=3600)

    @extend_schema(responses={200: RulesetVersionDetailSerializer})
    def get(self, request: Any, ruleset_id: Any, version: Any) -> Any:
        try:
            ruleset = FantasyRuleset.objects.get(pk=ruleset_id, is_active=True)
        except FantasyRuleset.DoesNotExist:
            raise NotFound(f"ruleset {ruleset_id} not found") from None
        try:
            version_obj = FantasyRulesetVersion.objects.get(ruleset_id=ruleset.id, version=version)
        except FantasyRulesetVersion.DoesNotExist:
            raise NotFound(f"version {version} not found") from None
        if version_obj.status == RulesetVersionStatus.DRAFT:
            raise NotFound("draft versions are not public")
        data: dict[str, Any] = {
            **_version_dict(version_obj),
            "rules": [_rule_dict(rule) for rule in version_obj.point_rules.order_by("id")],
        }
        serializer = RulesetVersionDetailSerializer(data, context={"request": request})
        meta = self._base_meta(ruleset={"id": str(ruleset.id), "name": ruleset.name})
        return Response({"data": serializer.data, "meta": meta})


class FantasyPointsView(_FantasyBase):
    """GET /api/v1/fantasy/points (match/player/ruleset_version; page-number).

    At least one of the three filters is required (else 400 ``invalid_query``).
    Each row identifies the calc run that produced it; ``meta`` summarises the
    latest run among the returned rows.
    """

    cache_namespace = "fantasy:points"

    def cache_version_ns(self, request: Any, *args: Any, **kwargs: Any) -> str:
        match_id = request.query_params.get("match")
        if match_id:
            return f"fantasy:match:{match_id}"
        return self.cache_namespace

    def cache_plan(self) -> Any:
        return CachePlan(redis_ttl=60, max_age=60)

    @extend_schema(responses={200: PointsRowSerializer})
    def get(self, request: Any) -> Any:
        redirect = _redirect_if_merged(request)
        if redirect is not None:
            return redirect
        params = request.query_params
        match_id = params.get("match")
        player_id = params.get("player")
        version_id = params.get("ruleset_version")
        if not (match_id or player_id or version_id):
            raise InvalidQuery("at least one of match, player, ruleset_version is required")

        filters: dict[str, Any] = {}
        if match_id:
            filters["match_id"] = _as_uuid(match_id, "match")
        if player_id:
            filters["player_id"] = _as_uuid(player_id, "player")
        if version_id:
            version = _public_version_or_404(version_id)
            filters["ruleset_version_id"] = version.id

        queryset = (
            PlayerMatchPerformance.objects.filter(**filters)
            .exclude(ruleset_version__status=RulesetVersionStatus.DRAFT)
            .select_related("player", "ruleset_version", "ruleset_version__ruleset", "calc_run")
            .order_by("-computed_at", "match_id", "player__full_name")
        )
        paginator = PageNumberPagination()
        page = paginator.paginate_queryset(queryset, request, view=self) or []
        rows = [_row_dict(perf) for perf in page]
        serializer = PointsRowSerializer(rows, many=True, context={"request": request})
        payload = paginator.get_paginated_response(serializer.data)  # type: ignore[arg-type]
        payload.data["meta"].update(self._points_meta(page))
        return payload

    def _points_meta(self, perfs: list[PlayerMatchPerformance]) -> dict[str, Any]:
        """The response meta: label/notice + the latest run among the rows."""
        meta = self._base_meta()
        meta["as_of"] = None
        meta["calc_run_id"] = None
        meta["status"] = None
        meta["is_partial"] = None
        meta["unsupported_rules"] = []
        meta["ruleset"] = None
        if not perfs:
            return meta
        latest = max(perfs, key=lambda perf: perf.computed_at or datetime.min)
        run = latest.calc_run
        meta["as_of"] = run.computed_at if run else None
        meta["calc_run_id"] = str(run.id) if run else None
        meta["status"] = _calc_status(run)
        meta["is_partial"] = latest.is_partial
        meta["unsupported_rules"] = list(latest.unsupported_rules or [])
        version = latest.ruleset_version
        meta["ruleset"] = {
            "id": str(version.ruleset_id),
            "version": version.version,
            "hash": version.content_hash or (run.version_hash if run else None),
        }
        return meta


def _public_version_or_404(raw: Any) -> FantasyRulesetVersion:
    """A public (non-draft) version for ``?ruleset_version=``, else 404."""
    try:
        version = FantasyRulesetVersion.objects.get(pk=UUID(str(raw)))
    except ValueError, FantasyRulesetVersion.DoesNotExist:
        raise NotFound("ruleset_version not found") from None
    if version.status == RulesetVersionStatus.DRAFT:
        raise NotFound("draft versions are not public")
    return version


def _row_dict(perf: PlayerMatchPerformance) -> dict[str, Any]:
    """One /fantasy/points row (computed + calc-run identity)."""
    run = perf.calc_run
    return {
        "match_id": perf.match_id,
        "player_id": perf.player_id,
        "player_name": perf.player.full_name,
        "ruleset_version_id": perf.ruleset_version_id,
        "version": perf.ruleset_version.version,
        "format_scope": perf.ruleset_version.format_scope,
        "total_points": perf.total_points,
        "breakdown": perf.breakdown or {},
        "calc_run_id": str(run.id) if run else None,
        "status": _calc_status(run),
        "is_partial": perf.is_partial,
        "unsupported_rules": list(perf.unsupported_rules or []),
        "computed_at": run.computed_at if run else None,
    }


def _primary_version(match: Match) -> FantasyRulesetVersion | None:
    """The version a match's points are presented under.

    Prefers the active default frozen version for the match's format (a
    generic default with no format scope is the fallback), then the latest
    non-provisional calc run's version.
    """
    base = FantasyRulesetVersion.objects.filter(
        ruleset__is_active=True, is_frozen=True, is_default=True
    )
    version = base.filter(format_scope=match.format_type).first()
    if version is None:
        version = base.filter(format_scope__isnull=True).first()
    if version is None:
        audit = (
            FantasyCalcAudit.objects.filter(match_id=match.id, is_provisional=False)
            .order_by("-computed_at", "-id")
            .first()
        )
        if audit is not None:
            version = FantasyRulesetVersion.objects.filter(pk=audit.ruleset_version_id).first()
    return version


class FantasyMatchPointsView(_FantasyBase):
    """GET /api/v1/fantasy/matches/{id} (max-age=120)."""

    cache_namespace = "fantasy:match"

    def cache_parts(self, request: Any, *args: Any, **kwargs: Any) -> tuple[str, ...]:
        return (kwargs["pk"],)

    def cache_version_ns(self, request: Any, *args: Any, **kwargs: Any) -> str:
        return f"fantasy:match:{kwargs['pk']}"

    def cache_plan(self) -> Any:
        return CachePlan(redis_ttl=120, max_age=120)

    @extend_schema(responses={200: MatchPointsSerializer})
    def get(self, request: Any, pk: Any) -> Any:
        try:
            match = Match.objects.get(pk=pk)
        except Match.DoesNotExist:
            raise NotFound(f"match {pk} not found") from None
        version = _primary_version(match)

        if version is None:
            data: dict[str, Any] = {
                "match_id": match.id,
                "format": match.format_type,
                "status": match.status,
                "version": None,
                "players": [],
            }
            meta = self._base_meta(
                as_of=None,
                calc_run_id=None,
                status=None,
                is_partial=None,
                unsupported_rules=[],
                ruleset=None,
            )
        else:
            perfs = list(
                PlayerMatchPerformance.objects.filter(
                    match_id=match.id, ruleset_version_id=version.id
                )
                .select_related("player", "calc_run")
                .order_by("-total_points", "player__full_name")
            )
            run = perfs[0].calc_run if perfs else None
            data = {
                "match_id": match.id,
                "format": match.format_type,
                "status": match.status,
                "version": {
                    "id": version.id,
                    "version": version.version,
                    "format_scope": version.format_scope,
                    "content_hash": version.content_hash or (run.version_hash if run else None),
                    "is_default": version.is_default,
                },
                "players": [
                    {
                        "player_id": perf.player_id,
                        "player_name": perf.player.full_name,
                        "total_points": perf.total_points,
                        "breakdown": perf.breakdown or {},
                    }
                    for perf in perfs
                ],
            }
            latest = perfs[0] if perfs else None
            meta = self._base_meta(
                as_of=run.computed_at if run else None,
                calc_run_id=str(run.id) if run else None,
                status=_calc_status(run),
                is_partial=latest.is_partial if latest else None,
                unsupported_rules=list(latest.unsupported_rules or []) if latest else [],
                ruleset={
                    "id": str(version.ruleset_id),
                    "version": version.version,
                    "hash": version.content_hash or (run.version_hash if run else None),
                }
                if version
                else None,
            )
        serializer = MatchPointsSerializer(data, context={"request": request})
        return Response({"data": serializer.data, "meta": meta})


__all__ = [
    "FANTASY_LABEL",
    "FANTASY_NOTICE",
    "FantasyMatchPointsView",
    "FantasyPointsView",
    "FantasyRulesetListView",
    "FantasyRulesetVersionDetailView",
]
