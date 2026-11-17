"""Admin for players: trigram search, merge history, duplicate-review queue.

The duplicate-review actions (confirm_merge / reject_candidate / verify_mapping)
render an intermediate confirmation page showing the feature breakdown side by
side, then apply the decision. ``confirm_merge`` is gated on the
``players.merge_players`` permission so operators can see candidates without
being able to merge them.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.contrib import admin, messages
from django.contrib.postgres.search import TrigramSimilarity
from django.db.models import QuerySet
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from apps.core.events import TOPIC_DUPLICATE_REVIEWED, publish
from apps.players.models import Player, PlayerDuplicateCandidate, PlayerMerge
from apps.players.services import merge_players
from apps.players.services import verify_mapping as verify_map_service
from apps.providers.admin import ProviderEntityMapInline
from apps.providers.models import ProviderEntityMap

CONFIRM_TEMPLATE = "admin/players/duplicate_review_confirm.html"


def _admin_actor(user: Any) -> str:
    """Audit actor for an admin request: user uuid if present, else label."""
    uuid_value = getattr(user, "uuid", None)
    if uuid_value is not None:
        return str(uuid_value)
    return str(user)


def _actor_uuid(actor: str) -> UUID | None:
    try:
        return UUID(actor)
    except ValueError:
        return None


def _confirmation_rows(
    queryset: QuerySet[PlayerDuplicateCandidate],
) -> list[dict[str, Any]]:
    rows = []
    for candidate in queryset.select_related("candidate_player", "canonical_player"):
        rows.append(
            {
                "candidate": candidate.candidate_player,
                "canonical": candidate.canonical_player,
                "score": candidate.score,
                "breakdown": candidate.feature_breakdown,
            }
        )
    return rows


@admin.action(
    permissions=["merge_players"],
    description="Merge selected candidates into their canonical players",
)
def confirm_merge(
    modeladmin: Any,
    request: Any,
    queryset: QuerySet[PlayerDuplicateCandidate],
) -> HttpResponse | None:
    """Steward-confirmed merge: candidate -> canonical, then flag merged."""
    if "confirm" in request.POST:
        actor = _admin_actor(request.user)
        merged = 0
        for candidate in queryset.select_related("candidate_player", "canonical_player"):
            merge_players(
                candidate.canonical_player,
                candidate.candidate_player,
                actor=actor,
                reason="steward review",
            )
            candidate.status = "merged"
            candidate.reviewed_by = _actor_uuid(actor)
            candidate.reviewed_at = timezone.now()
            candidate.save(update_fields=["status", "reviewed_by", "reviewed_at"])
            merged += 1
        modeladmin.message_user(request, f"Merged {merged} candidate(s).")
        return None
    return render(
        request,
        CONFIRM_TEMPLATE,
        {
            "title": "Confirm merges",
            "description": (
                "permanently merge each candidate into its canonical player "
                "(no unmerge) and audit the decision"
            ),
            "action": "confirm_merge",
            "rows": _confirmation_rows(queryset),
            "queryset": queryset,
        },
    )


@admin.action(
    description="Reject selected candidates (mark resolved)",
)
def reject_candidate(
    modeladmin: Any,
    request: Any,
    queryset: QuerySet[PlayerDuplicateCandidate],
) -> HttpResponse | None:
    """Reject a duplicate flag: resolved + audited via player_duplicate.reviewed."""
    if "confirm" in request.POST:
        actor = _admin_actor(request.user)
        now = timezone.now()
        rejected = 0
        for candidate in queryset.filter(status="open"):
            candidate.status = "resolved"
            candidate.reviewed_by = _actor_uuid(actor)
            candidate.reviewed_at = now
            candidate.save(update_fields=["status", "reviewed_by", "reviewed_at"])
            publish(
                TOPIC_DUPLICATE_REVIEWED,
                {
                    "duplicate_candidate_id": str(candidate.pk),
                    "canonical_player_id": str(candidate.canonical_player_id),
                    "decision": "rejected",
                    "actor": actor,
                },
                on_commit=False,
            )
            rejected += 1
        modeladmin.message_user(request, f"Rejected {rejected} candidate(s).")
        return None
    return render(
        request,
        CONFIRM_TEMPLATE,
        {
            "title": "Reject duplicate candidates",
            "description": "mark the selected candidates as resolved (not duplicates)",
            "action": "reject_candidate",
            "rows": _confirmation_rows(queryset),
            "queryset": queryset,
        },
    )


@admin.action(
    description="Verify provider mappings for selected candidates",
)
def verify_mapping(
    modeladmin: Any,
    request: Any,
    queryset: QuerySet[PlayerDuplicateCandidate],
) -> HttpResponse | None:
    """Verify the candidate's provider-map rows (audited, Group 5)."""
    if "confirm" in request.POST:
        actor = _admin_actor(request.user)
        verified = 0
        for candidate in queryset:
            for map_row in ProviderEntityMap.objects.filter(
                internal_id=candidate.candidate_player_id
            ):
                verify_map_service(map_row, actor=actor)
                verified += 1
        modeladmin.message_user(
            request,
            f"Verified {verified} provider mapping(s).",
            messages.SUCCESS,
        )
        return None
    return render(
        request,
        CONFIRM_TEMPLATE,
        {
            "title": "Verify provider mappings",
            "description": "mark the selected candidates' provider maps as verified",
            "action": "verify_mapping",
            "rows": _confirmation_rows(queryset),
            "queryset": queryset,
        },
    )


@admin.register(Player)
class PlayerAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("full_name", "known_as", "gender", "primary_role", "is_active")
    search_fields = ("full_name", "known_as", "unique_name")
    list_filter = ("gender", "birth_country", "is_active")
    inlines = (ProviderEntityMapInline,)
    # Provenance / derived columns are read-only.
    readonly_fields = ("source_provider_id", "name_key")

    def get_search_results(
        self,
        request: Any,
        queryset: QuerySet[Player],
        search_term: str,
    ) -> tuple[QuerySet[Player], bool]:
        """Rank results by trigram similarity instead of ``icontains``."""
        if search_term:
            queryset = (
                queryset.annotate(similarity=TrigramSimilarity("full_name", search_term))
                .filter(similarity__gte=0.2)
                .order_by("-similarity")
            )
            return queryset, True  # may_have_duplicates (annotated ordering)
        return super().get_search_results(request, queryset, search_term)


@admin.register(PlayerMerge)
class PlayerMergeAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("merged_player", "canonical_player", "merged_at", "merged_by")
    search_fields = ("merged_player__full_name", "canonical_player__full_name")
    readonly_fields = (
        "merged_player",
        "canonical_player",
        "reason",
        "merged_by",
        "merged_at",
    )


@admin.register(PlayerDuplicateCandidate)
class PlayerDuplicateCandidateAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("candidate_player", "canonical_player", "score", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("candidate_player__full_name", "canonical_player__full_name")
    readonly_fields = (
        "candidate_player",
        "canonical_player",
        "score",
        "feature_breakdown",
        "created_at",
    )
    actions = (confirm_merge, reject_candidate, verify_mapping)

    def has_merge_players_permission(self, request: Any, obj: Any = None) -> bool:
        """Gate the confirm_merge action on the players.merge_players perm."""
        return bool(request.user.has_perm("players.merge_players"))


__all__ = [
    "PlayerAdmin",
    "PlayerDuplicateCandidateAdmin",
    "PlayerMergeAdmin",
    "confirm_merge",
    "reject_candidate",
    "verify_mapping",
]
