"""Player selectors.

``list_players`` ranks by trigram name similarity when ``q`` is given and falls
back to a plain ordered queryset otherwise (cursor-ready for the API).
"""

from __future__ import annotations

from uuid import UUID

from django.contrib.postgres.search import TrigramSimilarity
from django.db.models import QuerySet

from apps.core.errors import NotFound
from apps.players.models import Player, PlayerDuplicateCandidate, PlayerMerge

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
        # The `%` operator (__trigram_similar) is the index-backed form: a GIN
        # scan on ix_player_name_trgm (similarity() >= x alone cannot use it).
        # The recall threshold is the pg_trgm session default (0.3); ranking
        # still uses the annotated similarity.
        qs = qs.annotate(similarity=TrigramSimilarity("full_name", q)).filter(
            full_name__trigram_similar=q
        )
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


def resolve_player_redirect(pk: UUID) -> UUID | None:
    """The canonical player ``pk`` reads as after merges, or ``None``.

    Follows ``PlayerMerge`` rows (a canonical may itself be merged into a
    further winner, though the merge service re-points chains forward) and
    refuses a corrupt cycle by returning ``None`` (treat as not-merged). The
    public API turns a non-``None`` result into a 301 ``Location`` header.
    """
    seen: set[UUID] = set()
    current = pk
    while True:
        if current in seen:
            return None
        seen.add(current)
        canonical = (
            PlayerMerge.objects.filter(merged_player_id=current)
            .values_list("canonical_player_id", flat=True)
            .first()
        )
        if canonical is None:
            return current if current != pk else None
        current = canonical


__all__ = [
    "PLAYER_ORDERINGS",
    "get_player",
    "list_duplicate_candidates",
    "list_players",
    "resolve_player_redirect",
]
