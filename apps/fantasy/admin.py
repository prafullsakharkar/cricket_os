"""Django admin for the fantasy module.

Task 1: read-only registration so the models are visible and the Phase B role
Groups receive their permissions. The authoring surface (validate, dry-run,
freeze, set-default, retire, adjustments) is Task 6 and extends this file.
"""

from __future__ import annotations

from django.contrib import admin

from apps.fantasy.models import (
    FantasyCalcAudit,
    FantasyPointAdjustment,
    FantasyPointRule,
    FantasyPointsLedger,
    FantasyRuleset,
    FantasyRulesetVersion,
    PlayerMatchPerformance,
)

# Ledger/adjustment/calc-audit are append-only: the DB triggers already block
# UPDATE/DELETE; the admin must not offer add/edit/delete either.
APPEND_ONLY_READONLY = (
    "id",
    "match_id",
    "player_id",
    "ruleset_version_id",
    "delta",
    "event_type",
    "reason",
    "created_at",
    "created_by",
    "is_current",
    "superseded_by",
)


@admin.register(FantasyPointsLedger)
class FantasyPointsLedgerAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = (
        "created_at",
        "match_id",
        "player_id",
        "ruleset_version_id",
        "delta",
        "is_current",
    )
    list_filter = ("event_type", "is_current")
    readonly_fields = APPEND_ONLY_READONLY

    def has_add_permission(self, request: object) -> bool:
        return False

    def has_delete_permission(self, request: object, obj: object = None) -> bool:
        return False


@admin.register(FantasyPointAdjustment)
class FantasyPointAdjustmentAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("created_at", "match_id", "player_id", "delta", "adjusted_by")
    readonly_fields = (
        "id",
        "match_id",
        "player_id",
        "delta",
        "reason",
        "adjusted_by",
        "created_at",
    )

    def has_add_permission(self, request: object) -> bool:
        return False

    def has_delete_permission(self, request: object, obj: object = None) -> bool:
        return False


@admin.register(FantasyCalcAudit)
class FantasyCalcAuditAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("computed_at", "match_id", "ruleset_version_id", "is_provisional")
    list_filter = ("trigger", "is_provisional")
    readonly_fields = (
        "id",
        "match_id",
        "ruleset_version_id",
        "input_digest",
        "output_digest",
        "engine_version",
        "notes",
        "computed_at",
        "snapshot_digest",
        "trigger",
        "is_provisional",
        "timings",
    )

    def has_add_permission(self, request: object) -> bool:
        return False

    def has_delete_permission(self, request: object, obj: object = None) -> bool:
        return False


@admin.register(FantasyRuleset)
class FantasyRulesetAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("name", "provider_scope", "is_active")
    list_filter = ("is_active",)


@admin.register(FantasyRulesetVersion)
class FantasyRulesetVersionAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("ruleset", "version", "status", "format_scope", "is_default", "auto_recompute")
    list_filter = ("status", "format_scope", "is_default")
    readonly_fields = ("content_hash", "frozen_at", "frozen_by")


@admin.register(FantasyPointRule)
class FantasyPointRuleAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("ruleset_version", "event_type", "role_scope", "points", "cap_kind")
    list_filter = ("event_type", "role_scope")


@admin.register(PlayerMatchPerformance)
class PlayerMatchPerformanceAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("match", "player", "ruleset_version", "total_points", "is_partial")
    list_filter = ("is_partial",)
    readonly_fields = ("calc_run", "revision", "computed_at")


__all__ = [
    "FantasyCalcAuditAdmin",
    "FantasyPointAdjustmentAdmin",
    "FantasyPointRuleAdmin",
    "FantasyPointsLedgerAdmin",
    "FantasyRulesetAdmin",
    "FantasyRulesetVersionAdmin",
    "PlayerMatchPerformanceAdmin",
]
