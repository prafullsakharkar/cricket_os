"""Venue-domain selectors (read-only; never import services).

Querysets default-exclude soft-deleted / inactive rows wherever the reference
DDL carries those flags (``country.is_active``; venue has neither, so all
rows are visible).
"""

from __future__ import annotations

from typing import Any

from django.db.models import QuerySet

from apps.core.errors import NotFound
from apps.venues.models import Country, GoverningBody, Venue


def list_countries() -> QuerySet[Country]:
    """All active countries, ordered by name."""
    return Country.objects.filter(is_active=True).order_by("name")


def get_country(pk: str) -> Country:
    """A single active country or :class:`~apps.core.errors.NotFound`."""
    try:
        return list_countries().get(pk=pk)
    except Country.DoesNotExist as exc:
        raise NotFound(f"Country {pk} not found") from exc


def list_venues(**filters: Any) -> QuerySet[Venue]:
    """Venues filtered by the given lookup kwargs (``country_id``, city...)."""
    return Venue.objects.filter(**filters).select_related("country").order_by("name", "city")


def get_venue(pk: str) -> Venue:
    try:
        return Venue.objects.select_related("country").get(pk=pk)
    except Venue.DoesNotExist as exc:
        raise NotFound(f"Venue {pk} not found") from exc


def list_governing_bodies() -> QuerySet[GoverningBody]:
    """All governing bodies with their country loaded."""
    return GoverningBody.objects.select_related("country").order_by("name")


def get_governing_body(pk: str) -> GoverningBody:
    try:
        return GoverningBody.objects.select_related("country").get(pk=pk)
    except GoverningBody.DoesNotExist as exc:
        raise NotFound(f"Governing body {pk} not found") from exc


__all__ = [
    "get_country",
    "get_governing_body",
    "get_venue",
    "list_countries",
    "list_governing_bodies",
    "list_venues",
]
