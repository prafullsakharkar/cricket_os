"""Django admin for the fantasy module.

Task 1: read-only registration so the models are visible and the Phase B role
Groups receive their permissions. Task 5 adds the role-gated manual-adjustment
action. The authoring surface (validate, dry-run, freeze, set-default, retire)
is Task 6 and extends this file.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from django.contrib import admin, messages
from django.contrib.admin.options import ActionLocation
from django.http import HttpResponse
from django.shortcuts import render

from apps.fantasy.models import (
    FantasyCalcAudit,
    FantasyPointAdjustment,
    FantasyPointRule,
    FantasyPointsLedger,
    FantasyRuleset,
    FantasyRulesetVersion,
    PlayerMatchPerformance,
)
from apps.fantasy.services.adjustments import ADJUSTMENT_ROLES, apply_adjustment

ADJUSTMENT_TEMPLATE = "admin/fantasy/adjustment_confirm.html"

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
    list_display = (
        "created_at",
        "match_id",
        "player_id",
        "delta",
        "reason",
        "reference",
        "adjusted_by",
    )
    readonly_fields = (
        "id",
        "match_id",
        "player_id",
        "delta",
        "reason",
        "reference",
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


def _adjustment_confirm(
    modeladmin: Any, request: Any, queryset: Any, *, delta: str = ""
) -> HttpResponse:
    """Render the intermediate page for the manual-adjustment action."""
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for perf in queryset:
        key = (str(perf.match_id), str(perf.player_id))
        if key in seen:
            continue  # one entry per (match, player); applies to all versions
        seen.add(key)
        rows.append(
            {
                "match_id": perf.match_id,
                "player_id": perf.player_id,
                "total": perf.total_points,
            }
        )
    return render(
        request,
        ADJUSTMENT_TEMPLATE,
        {
            "title": "Apply a manual adjustment",
            "rows": rows,
            "queryset": queryset,
            "action": "apply_adjustment_action",
            "delta": delta,
        },
    )


@admin.action(description="Apply a manual adjustment to selected performances")
def apply_adjustment_action(
    modeladmin: Any,
    request: Any,
    queryset: Any,
) -> HttpResponse | None:
    """Role-gated (data_steward/admin) manual adjustment, applied per (match,
    player) pair. The service enforces the role, bounds and mandatory fields;
    the action just collects delta/reason/reference and calls it."""
    if "confirm" not in request.POST:
        return _adjustment_confirm(modeladmin, request, queryset)
    delta_raw = request.POST.get("delta", "")
    reason = request.POST.get("reason", "").strip()
    reference = request.POST.get("reference", "").strip()
    try:
        delta = Decimal(delta_raw)
    except InvalidOperation:
        modeladmin.message_user(request, "delta must be a decimal number", level=messages.ERROR)
        return _adjustment_confirm(modeladmin, request, queryset, delta=delta_raw)
    applied = 0
    seen: set[tuple[str, str]] = set()
    try:
        for perf in queryset:
            key = (str(perf.match_id), str(perf.player_id))
            if key in seen:
                continue
            seen.add(key)
            apply_adjustment(
                match_id=perf.match_id,
                player_id=perf.player_id,
                delta=delta,
                reason=reason,
                reference=reference,
                actor=request.user,
            )
            applied += 1
    except PermissionError:
        modeladmin.message_user(
            request,
            "adjustments require the data_steward or admin role",
            level=messages.ERROR,
        )
        return None
    except ValueError as exc:
        modeladmin.message_user(request, f"adjustment rejected: {exc}", level=messages.ERROR)
        return None
    modeladmin.message_user(request, f"Applied {applied} adjustment(s).")
    return None


@admin.register(PlayerMatchPerformance)
class PlayerMatchPerformanceAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("match", "player", "ruleset_version", "total_points", "is_partial")
    list_filter = ("is_partial",)
    readonly_fields = ("calc_run", "revision", "computed_at")
    actions = (apply_adjustment_action,)

    def get_actions(
        self, request: Any, action_location: Any = ActionLocation.CHANGE_LIST
    ) -> dict[str, Any]:
        actions = super().get_actions(request, action_location=action_location)
        allowed = (
            request.user.is_superuser
            or request.user.groups.filter(name__in=ADJUSTMENT_ROLES).exists()
        )
        if not allowed:
            actions.pop("apply_adjustment_action", None)
        return actions


__all__ = [
    "FantasyCalcAuditAdmin",
    "FantasyPointAdjustmentAdmin",
    "FantasyPointRuleAdmin",
    "FantasyPointsLedgerAdmin",
    "FantasyRulesetAdmin",
    "FantasyRulesetVersionAdmin",
    "PlayerMatchPerformanceAdmin",
    "apply_adjustment_action",
]
