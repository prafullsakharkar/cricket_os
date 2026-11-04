"""Admin for matches: lifecycle state + audited steward actions.

The two actions are the **steward correction entry point** (SECURITY §11,
DATA_INGESTION §4.5): they are gated by the model's ``change`` permission (so
only ``admin``/``data_steward``/``operator`` see them), call the same service
functions the provider pipeline uses, and publish topics that the Phase B
``AuditLog`` subscriber records — an admin correction is as audited as a
provider one.

* :func:`reopen_for_correction` — the explicit, audited terminal → IN_PROGRESS
  path required before re-deriving a completed match;
* :func:`request_manual_correction` — a steward-supplied metadata correction
  through ``synchronization.services.corrections.apply_manual_correction``.
"""

from __future__ import annotations

from typing import Any

from django import forms
from django.contrib import admin, messages
from django.http import HttpRequest
from django.shortcuts import redirect, render

from apps.core import events as core_events
from apps.matches.models import Match
from apps.matches.services import state as lifecycle

CORRECTION_TEMPLATE = "admin/matches/correction_confirm.html"


class ReopenForm(forms.Form):
    reason = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3, "cols": 60}),
        initial="provider correction",
        help_text="Why is this completed match being reopened?",
    )


class ManualCorrectionForm(forms.Form):
    entity_type = forms.CharField(initial="match")
    entity_id = forms.UUIDField(label="Entity id (uuid)")
    field_path = forms.CharField(help_text="e.g. status_detail, start_time_utc")
    old_value = forms.CharField(required=False)
    new_value = forms.CharField()
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3, "cols": 60}))


def _selected(request: HttpRequest) -> list[Match]:
    pks = request.POST.getlist("_selected_action")
    return list(Match.objects.filter(pk__in=pks))


@admin.action(description="Reopen for provider correction…", permissions=("change",))
def reopen_for_correction(modeladmin: Any, request: HttpRequest, queryset: Any) -> Any:
    """Audited terminal → IN_PROGRESS reopen used before a correction."""
    if "confirm" in request.POST:
        form = ReopenForm(request.POST)
        if form.is_valid():
            reason = form.cleaned_data["reason"]
            count = 0
            for match in _selected(request):
                if match.status in lifecycle.TERMINAL:
                    lifecycle.reopen(match, reason=reason, source=None)
                    core_events.publish(
                        core_events.TOPIC_MATCH_REOPENED,
                        {
                            "match_id": str(match.id),
                            "reason": reason,
                            "source": "admin",
                            "actor": str(request.user),
                        },
                    )
                    count += 1
            modeladmin.message_user(
                request, f"Reopened {count} match(es) for correction.", messages.SUCCESS
            )
            return redirect(request.get_full_path())
    else:
        form = ReopenForm()
    return render(
        request,
        CORRECTION_TEMPLATE,
        {
            "title": "Reopen matches for provider correction",
            "description": "reopen the selected completed matches (audited)",
            "queryset": queryset,
            "form": form,
            "action": "reopen_for_correction",
            "opts": Match._meta,
        },
    )


@admin.action(description="Request a manual correction…", permissions=("change",))
def request_manual_correction(modeladmin: Any, request: HttpRequest, queryset: Any) -> Any:
    """Steward-supplied metadata correction via the shared service entry point."""
    from apps.synchronization.services.corrections import apply_manual_correction

    if "confirm" in request.POST:
        form = ManualCorrectionForm(request.POST)
        if form.is_valid():
            match = queryset.first()
            entity_id = form.cleaned_data["entity_id"]
            row = apply_manual_correction(
                match=match,
                entity_type=form.cleaned_data["entity_type"],
                entity_id=entity_id,
                field_path=form.cleaned_data["field_path"],
                old_value=form.cleaned_data["old_value"] or None,
                new_value=form.cleaned_data["new_value"],
                reason=form.cleaned_data["reason"],
                actor=request.user,
            )
            core_events.publish(
                core_events.TOPIC_CORRECTION_REQUESTED,
                {
                    "match_id": str(match.id) if match is not None else None,
                    "entity_type": form.cleaned_data["entity_type"],
                    "entity_id": str(entity_id),
                    "old_value": form.cleaned_data["old_value"] or None,
                    "new_value": form.cleaned_data["new_value"],
                    "actor": str(request.user),
                },
            )
            modeladmin.message_user(request, f"Correction recorded ({row.pk}).", messages.SUCCESS)
            return redirect(request.get_full_path())
    else:
        form = ManualCorrectionForm()
    return render(
        request,
        CORRECTION_TEMPLATE,
        {
            "title": "Request a manual correction",
            "description": "record an append-only data correction (audited)",
            "queryset": queryset,
            "form": form,
            "action": "request_manual_correction",
            "opts": Match._meta,
        },
    )


@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("id", "status", "state_title", "start_time_utc", "format_type")
    list_filter = ("status", "format_type")
    search_fields = ("provider_match_id",)
    readonly_fields = ("id", "ingested_at", "last_ingested_at", "revision")
    actions = (reopen_for_correction, request_manual_correction)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


__all__ = ["MatchAdmin", "reopen_for_correction", "request_manual_correction"]
