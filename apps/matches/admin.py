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
from django.urls import reverse
from django.utils import timezone

from apps.core import events as core_events
from apps.core.errors import Conflict
from apps.matches.models import Match, MatchDuplicateCandidate, MatchMerge
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


# --------------------------------------------------------------------------- #
# Phase I Task 4: match de-duplication review actions (reversible)
# --------------------------------------------------------------------------- #

DEDUP_TEMPLATE = "admin/matches/dedup_review_confirm.html"


def _admin_actor(user: Any) -> str:
    """Audit actor for an admin request: user uuid if present, else label."""
    uuid_value = getattr(user, "uuid", None)
    if uuid_value is not None:
        return str(uuid_value)
    return str(user)


def _dup_rows(queryset: Any) -> list[dict[str, Any]]:
    rows = []
    for candidate in queryset.select_related("candidate_match", "canonical_match"):
        rows.append(
            {
                "candidate": candidate.candidate_match,
                "canonical": candidate.canonical_match,
                "detail": f"score {candidate.score}",
            }
        )
    return rows


def _merge_rows(queryset: Any) -> list[dict[str, Any]]:
    rows = []
    for merge in queryset.select_related("merged_match", "canonical_match"):
        rows.append(
            {
                "candidate": merge.merged_match,
                "canonical": merge.canonical_match,
                "detail": merge.status,
            }
        )
    return rows


def _dedup_confirm(
    request: HttpRequest,
    queryset: Any,
    *,
    action: str,
    title: str,
    description: str,
    rows: list[dict[str, Any]],
    cancel_url: str,
) -> Any:
    """Render the shared confirmation page for the de-dup actions."""
    return render(
        request,
        DEDUP_TEMPLATE,
        {
            "title": title,
            "description": description,
            "queryset": queryset,
            "action": action,
            "rows": rows,
            "cancel": reverse(cancel_url),
        },
    )


@admin.action(
    permissions=("change",),
    description="Confirm merge into canonical match (reversible)",
)
def confirm_match_merge(modeladmin: Any, request: HttpRequest, queryset: Any) -> Any:
    """Steward-confirmed merge: candidate -> canonical (reversible), audited."""
    from apps.matches.services.merge import merge_matches

    if "confirm" in request.POST:
        actor = _admin_actor(request.user)
        merged = 0
        for candidate in queryset.filter(status="open").select_related(
            "candidate_match", "canonical_match"
        ):
            merge_matches(
                candidate.candidate_match,
                candidate.canonical_match,
                actor=actor,
                reason="steward review",
            )
            candidate.status = "resolved"
            candidate.resolved_at = timezone.now()
            candidate.save(update_fields=["status", "resolved_at"])
            core_events.publish(
                core_events.TOPIC_MATCH_DUP_REVIEWED,
                {
                    "duplicate_candidate_id": str(candidate.pk),
                    "candidate_match_id": str(candidate.candidate_match_id),
                    "canonical_match_id": str(candidate.canonical_match_id),
                    "decision": "merged",
                    "actor": actor,
                },
                on_commit=False,
            )
            merged += 1
        modeladmin.message_user(
            request, f"Merged {merged} candidate(s) (reversible).", messages.SUCCESS
        )
        return None
    return _dedup_confirm(
        request,
        queryset,
        action="confirm_match_merge",
        title="Confirm match merges",
        description=(
            "merge each candidate match into its canonical match (reversible, "
            "audited); the merged rows stay recoverable via match_merge"
        ),
        rows=_dup_rows(queryset),
        cancel_url="admin:matches_matchduplicatecandidate_changelist",
    )


@admin.action(
    permissions=("change",),
    description="Reject selected duplicate candidates",
)
def reject_match_candidate(modeladmin: Any, request: HttpRequest, queryset: Any) -> Any:
    """Reject a duplicate flag: status='rejected' + audited decision."""
    if "confirm" in request.POST:
        actor = _admin_actor(request.user)
        now = timezone.now()
        rejected = 0
        for candidate in queryset.filter(status="open"):
            candidate.status = "rejected"
            candidate.resolved_at = now
            candidate.save(update_fields=["status", "resolved_at"])
            core_events.publish(
                core_events.TOPIC_MATCH_DUP_REVIEWED,
                {
                    "duplicate_candidate_id": str(candidate.pk),
                    "candidate_match_id": str(candidate.candidate_match_id),
                    "canonical_match_id": str(candidate.canonical_match_id),
                    "decision": "rejected",
                    "actor": actor,
                },
                on_commit=False,
            )
            rejected += 1
        modeladmin.message_user(request, f"Rejected {rejected} candidate(s).", messages.SUCCESS)
        return None
    return _dedup_confirm(
        request,
        queryset,
        action="reject_match_candidate",
        title="Reject duplicate candidates",
        description="mark the selected candidates as resolved (not duplicates)",
        rows=_dup_rows(queryset),
        cancel_url="admin:matches_matchduplicatecandidate_changelist",
    )


@admin.action(
    permissions=("change",),
    description="Split selected merges (reverse)",
)
def split_match(modeladmin: Any, request: HttpRequest, queryset: Any) -> Any:
    """Reverse a merge exactly: only status='merged' rows split (audited)."""
    from apps.matches.services.merge import split_match as split_service

    if "confirm" in request.POST:
        actor = _admin_actor(request.user)
        split = 0
        errors = 0
        for merge_row in queryset.filter(status="merged"):
            try:
                split_service(merge_row, actor=actor)
                split += 1
            except Conflict:
                errors += 1
        modeladmin.message_user(
            request,
            f"Split {split} merge(s).{f' {errors} skipped (not merged).' if errors else ''}",
            messages.SUCCESS,
        )
        return None
    return _dedup_confirm(
        request,
        queryset,
        action="split_match",
        title="Split match merges",
        description=(
            "reverse each selected merge exactly (restores the child rows and "
            "the merged match); only 'merged' rows split"
        ),
        rows=_merge_rows(queryset),
        cancel_url="admin:matches_matchmerge_changelist",
    )


@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("id", "status", "state_title", "start_time_utc", "format_type")
    list_filter = ("status", "format_type")
    search_fields = ("provider_match_id",)
    readonly_fields = ("id", "ingested_at", "last_ingested_at", "revision", "merged_into")
    actions = (reopen_for_correction, request_manual_correction)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(MatchDuplicateCandidate)
class MatchDuplicateCandidateAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """Steward review queue for plausible match duplicates (Phase I Task 4).

    ``confirm_match_merge`` performs the reversible merge via
    ``apps.matches.services.merge``; ``reject_match_candidate`` closes the
    pair. Both are idempotent and audited (``match.dup.reviewed``).
    """

    list_display = ("id", "candidate_match", "canonical_match", "score", "status", "created_at")
    list_filter = ("status",)
    readonly_fields = ("id", "created_at")
    search_fields = ("candidate_match_id", "canonical_match_id")
    actions = (confirm_match_merge, reject_match_candidate)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(MatchMerge)
class MatchMergeAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """Reversible merge ledger (Phase I Task 4). ``split_match`` reverses a
    merge exactly (only ``status='merged'`` rows split; audited)."""

    list_display = (
        "id",
        "merged_match",
        "canonical_match",
        "status",
        "created_at",
        "split_at",
    )
    list_filter = ("status",)
    readonly_fields = ("id", "before", "after", "created_at", "split_at")
    actions = (split_match,)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


__all__ = [
    "MatchAdmin",
    "MatchDuplicateCandidateAdmin",
    "MatchMergeAdmin",
    "confirm_match_merge",
    "reject_match_candidate",
    "reopen_for_correction",
    "request_manual_correction",
    "split_match",
]
