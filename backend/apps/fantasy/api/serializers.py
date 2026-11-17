"""Serializers for the fantasy read API (Task 7).

These describe the ``data`` content of the standard ``{"data", "meta"}``
envelope. They are used both at runtime (building the response body) and by
drf-spectacular for the OpenAPI components that the contract tests validate.

``?fields=`` sparse fields are supported on the ruleset catalogue serializers
via :class:`apps.core.api.SparseFieldsMixin` (CRICKET_API_SPECIFICATION §1).
"""

from __future__ import annotations

from rest_framework import serializers

from apps.core.api import SparseFieldsMixin


class RulesetBriefSerializer(serializers.Serializer):  # type: ignore[type-arg]
    """A ruleset's identity fields (no versions)."""

    id = serializers.UUIDField()
    name = serializers.CharField()
    provider_scope = serializers.CharField(allow_null=True, required=False)
    description = serializers.CharField(allow_null=True, required=False)


class RulesetVersionSerializer(serializers.Serializer):  # type: ignore[type-arg]
    """One ruleset version as shown in the catalogue (no point rules)."""

    id = serializers.UUIDField()
    version = serializers.IntegerField()
    format_scope = serializers.CharField(allow_null=True, required=False)
    effective_from = serializers.DateTimeField()
    effective_to = serializers.DateTimeField(allow_null=True, required=False)
    status = serializers.CharField()
    is_default = serializers.BooleanField()
    auto_recompute = serializers.BooleanField()
    content_hash = serializers.CharField(allow_null=True, required=False)
    #: The *effective* engine policy flags (stored overrides merged with the
    #: engine defaults), so the public catalogue shows what actually applies.
    policies = serializers.JSONField()


class RulesetListSerializer(SparseFieldsMixin, RulesetBriefSerializer):
    """One ruleset in the public catalogue, with its public versions."""

    allowed_fields = ("id", "name", "provider_scope", "description", "versions")
    versions = RulesetVersionSerializer(many=True)


class PointRuleSerializer(serializers.Serializer):  # type: ignore[type-arg]
    """One declarative point rule (version detail only)."""

    event_type = serializers.CharField()
    role_scope = serializers.CharField(allow_null=True, required=False)
    points = serializers.DecimalField(max_digits=6, decimal_places=2)
    condition = serializers.JSONField(allow_null=True, required=False)


class RulesetVersionDetailSerializer(SparseFieldsMixin, RulesetVersionSerializer):
    """Version detail: catalogue fields + the declarative point rules."""

    allowed_fields = (
        "id",
        "version",
        "format_scope",
        "effective_from",
        "effective_to",
        "status",
        "is_default",
        "auto_recompute",
        "content_hash",
        "policies",
        "rules",
    )
    rules = PointRuleSerializer(many=True)


class PointsRowSerializer(serializers.Serializer):  # type: ignore[type-arg]
    """One per-player fantasy points row (/fantasy/points)."""

    match_id = serializers.UUIDField()
    player_id = serializers.UUIDField()
    player_name = serializers.CharField()
    ruleset_version_id = serializers.UUIDField()
    version = serializers.IntegerField()
    format_scope = serializers.CharField(allow_null=True, required=False)
    total_points = serializers.DecimalField(max_digits=8, decimal_places=2)
    breakdown = serializers.JSONField()
    #: The calc run that produced this row (null before any run exists).
    calc_run_id = serializers.UUIDField(allow_null=True, required=False)
    #: "final" | "provisional" (null when no calc run backs the row).
    status = serializers.CharField(allow_null=True, required=False)
    is_partial = serializers.BooleanField()
    unsupported_rules = serializers.ListField(child=serializers.CharField())
    computed_at = serializers.DateTimeField(allow_null=True, required=False)


class MatchVersionSerializer(serializers.Serializer):  # type: ignore[type-arg]
    """The primary version a match's points are presented under."""

    id = serializers.UUIDField()
    version = serializers.IntegerField()
    format_scope = serializers.CharField(allow_null=True, required=False)
    content_hash = serializers.CharField(allow_null=True, required=False)
    is_default = serializers.BooleanField()


class MatchPlayerPointsSerializer(serializers.Serializer):  # type: ignore[type-arg]
    """One player's points within a match (component breakdown)."""

    player_id = serializers.UUIDField()
    player_name = serializers.CharField()
    total_points = serializers.DecimalField(max_digits=8, decimal_places=2)
    breakdown = serializers.JSONField()


class MatchPointsSerializer(serializers.Serializer):  # type: ignore[type-arg]
    """GET /fantasy/matches/{id}: the match + all players, ordered."""

    match_id = serializers.UUIDField()
    format = serializers.CharField()
    status = serializers.CharField()
    version = MatchVersionSerializer(allow_null=True, required=False)
    players = MatchPlayerPointsSerializer(many=True)


__all__ = [
    "MatchPlayerPointsSerializer",
    "MatchPointsSerializer",
    "MatchVersionSerializer",
    "PointRuleSerializer",
    "PointsRowSerializer",
    "RulesetBriefSerializer",
    "RulesetListSerializer",
    "RulesetVersionDetailSerializer",
    "RulesetVersionSerializer",
]
