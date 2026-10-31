"""Team selectors.

``list_teams`` supports a ranked trigram search on ``q`` and an allow-listed
``ordering``. Soft-deleted and inactive teams are excluded by default.
"""

from __future__ import annotations

from uuid import UUID

from django.contrib.postgres.search import TrigramSimilarity
from django.db.models import QuerySet

from apps.core.errors import NotFound
from apps.teams.models import Team

#: Allow-listed orderings; anything else is rejected by the caller's validator.
TEAM_ORDERINGS = frozenset({"name", "-name", "country", "-country", "team_type", "-team_type"})


def list_teams(
    *,
    q: str | None = None,
    category: str | None = None,
    country: UUID | None = None,
    ordering: str = "name",
) -> QuerySet[Team]:
    """Active teams, optionally filtered and ranked by name similarity."""
    qs: QuerySet[Team] = Team.objects.filter(is_active=True, deleted_at__isnull=True)
    if category:
        qs = qs.filter(team_type=category)
    if country:
        qs = qs.filter(country_id=country)

    if q:
        qs = qs.annotate(similarity=TrigramSimilarity("name", q)).filter(similarity__gte=0.2)
    # The `similarity` annotation is dynamic; django-stubs cannot resolve it.
    qs = qs.order_by("-similarity", "name") if q else qs.order_by(ordering)  # type: ignore[misc]
    return qs.select_related("country", "governing_body", "home_venue")


def get_team(pk: str) -> Team:
    """A single team with memberships prefetched, or :class:`NotFound`."""
    try:
        return (
            Team.objects.filter(is_active=True, deleted_at__isnull=True)
            .select_related("country", "governing_body", "home_venue")
            .prefetch_related("memberships")
            .get(pk=pk)
        )
    except Team.DoesNotExist as exc:
        raise NotFound(f"Team {pk} not found") from exc


__all__ = ["TEAM_ORDERINGS", "get_team", "list_teams"]
