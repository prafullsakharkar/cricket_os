"""Player selectors.

``list_players`` ranks by trigram name similarity when ``q`` is given and falls
back to a plain ordered queryset otherwise (cursor-ready for the API).
"""

from __future__ import annotations

from uuid import UUID

from django.contrib.postgres.search import TrigramSimilarity
from django.db.models import QuerySet

from apps.core.errors import NotFound
from apps.players.models import Player, PlayerDuplicateCandidate

#: Allow-listed orderings for the non-ranked path.
PLAYER_ORDERINGS = frozenset({"full_name", "-full_name", "date_of_birth", "-date_of_birth"})


def list_players(
    *,
    q: str | None = None,
    team: UUID | None = None,
    country: UUID | None = None,
    role: str | None = None,
    ordering: str = "full_name",
) -> QuerySet[Player]:
    """Active players, optionally filtered and ranked by name similarity."""
    qs: QuerySet[Player] = Player.objects.filter(is_active=True, deleted_at__isnull=True)
    if country:
        qs = qs.filter(birth_country_id=country)
    if role:
        qs = qs.filter(primary_role=role)
    if team:
        qs = qs.filter(memberships__team_id=team, memberships__left_at__isnull=True).distinct()

    if q:
        qs = qs.annotate(similarity=TrigramSimilarity("full_name", q)).filter(similarity__gte=0.2)
    # The `similarity` annotation is dynamic; django-stubs cannot resolve it.
    qs = qs.order_by("-similarity", "full_name") if q else qs.order_by(ordering)  # type: ignore[misc]
    return qs.select_related("birth_country", "international_team")


def get_player(pk: str) -> Player:
    """A single active player or :class:`NotFound`."""
    try:
        return (
            Player.objects.filter(is_active=True, deleted_at__isnull=True)
            .select_related("birth_country", "international_team")
            .get(pk=pk)
        )
    except Player.DoesNotExist as exc:
        raise NotFound(f"Player {pk} not found") from exc


def list_duplicate_candidates(*, status: str = "open") -> QuerySet[PlayerDuplicateCandidate]:
    """Duplicate-review queue, highest score first."""
    return (
        PlayerDuplicateCandidate.objects.filter(status=status)
        .select_related("candidate_player", "canonical_player")
        .order_by("-score", "-created_at")
    )


__all__ = [
    "PLAYER_ORDERINGS",
    "get_player",
    "list_duplicate_candidates",
    "list_players",
]
