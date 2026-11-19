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

from apps.fantasy import authoring
from apps.fantasy.forms import DryRunVersionForm
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
VERSION_ACTION_TEMPLATE = "admin/fantasy/version_action_confirm.html"
DRY_RUN_TEMPLATE = "admin/fantasy/dry_run.html"

#: Lifecycle actions that mutate a version require the admin role.
ADMIN_ONLY_ACTIONS = frozenset(
    {"freeze_version_action", "set_default_version_action", "retire_version_action"}
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


def _version_confirm(
    modeladmin: Any,
    request: Any,
    queryset: Any,
    *,
    title: str,
    description: str,
    action: str,
    action_label: str,
) -> HttpResponse:
    """Render the intermediate confirm page for a single-version lifecycle action."""
    rows = [
        {
            "version": version,
            "status": version.status,
            "rules": version.point_rules.count(),
        }
        for version in queryset
    ]
    return render(
        request,
        VERSION_ACTION_TEMPLATE,
        {
            "title": title,
            "description": description,
            "rows": rows,
            "queryset": queryset,
            "action": action,
            "action_label": action_label,
        },
    )


@admin.action(description="Validate the selected versions against the engine")
def validate_version_action(modeladmin: Any, request: Any, queryset: Any) -> HttpResponse | None:
    """Rebuild each version's engine spec and validate it (read-only)."""
    for version in queryset:
        try:
            spec = authoring.validate_version(version)
        except ValueError as exc:
            modeladmin.message_user(request, f"{version} INVALID: {exc}", level=messages.ERROR)
            continue
        modeladmin.message_user(
            request, f"{version} OK ({len(spec.rules)} rule(s), {version.status})"
        )
    return None


@admin.action(description="Dry-run the selected versions against a match")
def dry_run_version_action(modeladmin: Any, request: Any, queryset: Any) -> HttpResponse | None:
    """Compute a version against a chosen completed match (writes nothing)."""
    if "confirm" not in request.POST:
        form = DryRunVersionForm()
        return render(
            request,
            DRY_RUN_TEMPLATE,
            {
                "title": "Dry-run a version",
                "form": form,
                "queryset": queryset,
                "action": "dry_run_version_action",
            },
        )
    form = DryRunVersionForm(request.POST)
    version = queryset.first()
    if not form.is_valid() or version is None:
        modeladmin.message_user(
            request, "pick a version and a completed match", level=messages.ERROR
        )
        return None
    match = form.cleaned_data["match"]
    against = form.cleaned_data.get("against")
    try:
        result = authoring.dry_run_version(version, match, against=against)
    except ValueError as exc:
        modeladmin.message_user(request, f"{version} dry-run failed: {exc}", level=messages.ERROR)
        return None
    return render(
        request,
        DRY_RUN_TEMPLATE,
        {
            "title": f"Dry-run of {version} on {match}",
            "result": result,
            "queryset": queryset,
            "action": "dry_run_version_action",
        },
    )


@admin.action(description="Freeze the selected versions (irreversible)")
def freeze_version_action(modeladmin: Any, request: Any, queryset: Any) -> HttpResponse | None:
    """Freeze a draft version: records content_hash, locks it forever."""
    if "confirm" not in request.POST:
        return _version_confirm(
            modeladmin,
            request,
            queryset,
            title="Freeze version(s)",
            description=(
                "irreversibly freeze the selected draft versions: the engine "
                "content hash is recorded and the version + its rules become immutable"
            ),
            action="freeze_version_action",
            action_label="Freeze",
        )
    frozen = 0
    for version in queryset:
        try:
            authoring.freeze_version(version, actor=request.user)
            frozen += 1
        except (PermissionError, ValueError) as exc:
            modeladmin.message_user(request, f"{version}: {exc}", level=messages.ERROR)
    modeladmin.message_user(request, f"Frozen {frozen} version(s).")
    return None


@admin.action(description="Set the selected versions as the default")
def set_default_version_action(modeladmin: Any, request: Any, queryset: Any) -> HttpResponse | None:
    """Mark a draft version as the default for its (ruleset, format)."""
    if "confirm" not in request.POST:
        return _version_confirm(
            modeladmin,
            request,
            queryset,
            title="Set default version",
            description=(
                "mark the selected version as the default for its ruleset and "
                "format. The default must be chosen before freezing (frozen "
                "versions are immutable); at most one default per (ruleset, format)"
            ),
            action="set_default_version_action",
            action_label="Set default",
        )
    set_default = 0
    for version in queryset:
        try:
            authoring.set_default_version(version, actor=request.user)
            set_default += 1
        except (PermissionError, ValueError) as exc:
            modeladmin.message_user(request, f"{version}: {exc}", level=messages.ERROR)
    modeladmin.message_user(request, f"Set {set_default} default version(s).")
    return None


@admin.action(description="Retire the selected versions (fork a new draft)")
def retire_version_action(modeladmin: Any, request: Any, queryset: Any) -> HttpResponse | None:
    """Fork a fresh draft from each selected version (never mutating it)."""
    if "confirm" not in request.POST:
        return _version_confirm(
            modeladmin,
            request,
            queryset,
            title="Retire version(s)",
            description=(
                "fork a new draft version from each selected version (same rules "
                "and policies, next version number). The original is never edited"
            ),
            action="retire_version_action",
            action_label="Retire / fork",
        )
    forked = 0
    for version in queryset:
        try:
            authoring.retire_version(version, actor=request.user)
            forked += 1
        except (PermissionError, ValueError) as exc:
            modeladmin.message_user(request, f"{version}: {exc}", level=messages.ERROR)
    modeladmin.message_user(request, f"Forked {forked} new draft version(s).")
    return None


@admin.register(FantasyRulesetVersion)
class FantasyRulesetVersionAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("ruleset", "version", "status", "format_scope", "is_default", "auto_recompute")
    list_filter = ("status", "format_scope", "is_default")
    readonly_fields = ("content_hash", "frozen_at", "frozen_by")
    actions = (
        validate_version_action,
        dry_run_version_action,
        freeze_version_action,
        set_default_version_action,
        retire_version_action,
    )

    def get_actions(
        self, request: Any, action_location: Any = ActionLocation.CHANGE_LIST
    ) -> dict[str, Any]:
        actions = super().get_actions(request, action_location=action_location)
        if not (
            request.user.is_superuser
            or request.user.groups.filter(name__in=authoring.AUTHORING_ADMIN_ROLES).exists()
        ):
            for name in ADMIN_ONLY_ACTIONS:
                actions.pop(name, None)
        return actions


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
    "dry_run_version_action",
    "freeze_version_action",
    "retire_version_action",
    "set_default_version_action",
    "validate_version_action",
]
