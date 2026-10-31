"""Competition/season/series/format selectors."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.db.models import QuerySet

from apps.competitions.models import Competition, FormatCatalogue, Series
from apps.core.errors import NotFound

#: Allow-listed orderings for series listing.
SERIES_ORDERINGS = frozenset({"start_date", "-start_date", "name", "-name"})


def list_competitions(**filters: Any) -> QuerySet[Competition]:
    """Competitions filtered by the given lookup kwargs (is_international...)."""
    return Competition.objects.filter(**filters).select_related("governing_body").order_by("name")


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
    "SERIES_ORDERINGS",
    "get_competition",
    "get_format",
    "get_series",
    "list_competitions",
    "list_series",
]
