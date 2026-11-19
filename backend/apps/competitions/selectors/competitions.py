"""Competition/season/series/format selectors."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.contrib.postgres.search import TrigramSimilarity
from django.db.models import QuerySet

from apps.competitions.models import Competition, FormatCatalogue, Series
from apps.core.errors import NotFound

#: Allow-listed orderings for series listing.
SERIES_ORDERINGS = frozenset({"start_date", "-start_date", "name", "-name"})
#: Allow-listed orderings for the competition listing.
COMPETITION_ORDERINGS = frozenset({"name", "-name"})


def list_competitions(
    *,
    q: str | None = None,
    competition_type: str | None = None,
    ordering: str = "name",
    **filters: Any,
) -> QuerySet[Competition]:
    """Competitions, optionally ranked by name similarity (``q``).

    ``filters`` carries plain lookup kwargs (``is_international``...) for the
    non-API callers; the public API uses ``q`` / ``competition_type``.
    """
    qs: QuerySet[Competition] = Competition.objects.filter(**filters)
    if competition_type:
        qs = qs.filter(competition_type=competition_type)
    if q:
        # The `%` operator (__trigram_similar) is the index-backed form (GIN
        # gin_trgm_ops); similarity() >= x alone cannot use the index. Recall
        # threshold is the pg_trgm session default (0.3).
        qs = qs.annotate(similarity=TrigramSimilarity("name", q)).filter(name__trigram_similar=q)
    # The `similarity` annotation is dynamic; django-stubs cannot resolve it.
    qs = qs.order_by("-similarity", "name") if q else qs.order_by(ordering)  # type: ignore[misc]
    return qs.select_related("governing_body")


def get_competition(pk: str) -> Competition:
    try:
        return (
            Competition.objects.select_related("governing_body")
            .prefetch_related("seasons")
            .get(pk=pk)
        )
    except Competition.DoesNotExist as exc:
        raise NotFound(f"Competition {pk} not found") from exc


def list_series(
    *,
    year: int | None = None,
    category: UUID | None = None,
    series_type: str | None = None,
    ordering: str = "start_date",
) -> QuerySet[Series]:
    """Series, optionally filtered by year / competition / type."""
    qs: QuerySet[Series] = Series.objects.all()
    if year:
        qs = qs.filter(start_date__year=year)
    if category:
        qs = qs.filter(competition_id=category)
    if series_type:
        qs = qs.filter(series_type=series_type)
    return qs.select_related("competition", "season", "host_country").order_by(ordering)


def get_series(pk: str) -> Series:
    try:
        return Series.objects.select_related("competition", "season", "host_country").get(pk=pk)
    except Series.DoesNotExist as exc:
        raise NotFound(f"Series {pk} not found") from exc


def get_format(fmt: str) -> FormatCatalogue:
    try:
        return FormatCatalogue.objects.get(pk=fmt)
    except FormatCatalogue.DoesNotExist as exc:
        raise NotFound(f"Format {fmt!r} not found") from exc


__all__ = [
    "COMPETITION_ORDERINGS",
    "SERIES_ORDERINGS",
    "get_competition",
    "get_format",
    "get_series",
    "list_competitions",
    "list_series",
]
