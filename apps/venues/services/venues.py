"""Venue-domain write services (six-layer rule: selectors never import these)."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction

from apps.core.events import TOPIC_VENUE_UPDATED, publish
from apps.core.uuid7 import uuid7
from apps.players.identity_scoring import normalize_name
from apps.providers.models import DataProvider, ProviderEntityMap
from apps.providers.services import Candidate, Resolver, is_pem_race
from apps.venues.models import Venue

_ENTITY_TYPE = "venue"


def _apply(venue: Venue, fields: dict[str, Any]) -> Venue:
    for name, value in fields.items():
        setattr(venue, name, value)
    return venue


def upsert_venue(
    provider: DataProvider,
    provider_entity_id: str,
    *,
    name: str,
    city: str | None = None,
    country_id: UUID | None = None,
    timezone: str = "UTC",
    latitude: Decimal | None = None,
    longitude: Decimal | None = None,
    capacity: int | None = None,
    is_neutral: bool = False,
) -> Venue:
    """Create or update the venue for ``(provider, provider_entity_id)``.

    Keyed **via the provider map**, never by name alone: an exact map hit
    updates the mapped venue's fields; otherwise a venue is created with a map
    row (``verified=False``). Atomic and idempotent on re-run; a lost race
    against ``ux_pem`` rolls back to a savepoint and re-reads the winner.
    """
    fields: dict[str, Any] = {
        "name": name,
        "city": city,
        "country_id": country_id,
        "timezone": timezone,
        "latitude": latitude,
        "longitude": longitude,
        "capacity": capacity,
        "is_neutral": is_neutral,
    }
    with transaction.atomic():
        map_row = (
            ProviderEntityMap.objects.select_for_update()
            .filter(
                provider=provider,
                entity_type=_ENTITY_TYPE,
                provider_entity_id=provider_entity_id,
            )
            .first()
        )
        if map_row is not None:
            venue = Venue.objects.select_for_update().get(pk=map_row.internal_id)
            _apply(venue, fields)
            venue.save()
            publish(TOPIC_VENUE_UPDATED, {"venue_id": str(venue.pk)})
            return venue

        try:
            with transaction.atomic():
                venue = Venue.objects.create(
                    source_provider_id=provider.pk,
                    provider_venue_id=provider_entity_id,
                    **fields,
                )
                ProviderEntityMap.objects.create(
                    provider=provider,
                    entity_type=_ENTITY_TYPE,
                    internal_id=venue.pk,
                    provider_entity_id=provider_entity_id,
                    provider_unique_name=name,
                    verified=False,
                )
        except IntegrityError as exc:
            if not is_pem_race(exc):
                raise
            # Lost the ux_pem race: the winner's venue+map committed; re-read.
            map_row = ProviderEntityMap.objects.get(
                provider=provider,
                entity_type=_ENTITY_TYPE,
                provider_entity_id=provider_entity_id,
            )
            venue = Venue.objects.get(pk=map_row.internal_id)
            _apply(venue, fields)
            venue.save()
        publish(TOPIC_VENUE_UPDATED, {"venue_id": str(venue.pk)})
        return venue


class VenueResolver(Resolver):
    """Entity resolver for ``entity_type="venue"`` (normalised name + city).

    Registered from ``venues.apps.ready()``; used by the generic
    :func:`apps.providers.services.resolve`.
    """

    def match(self, attributes: Mapping[str, Any]) -> list[Candidate]:
        name = attributes.get("name")
        if not name:
            return []
        norm = normalize_name(name)
        qs = Venue.objects.all()
        city = attributes.get("city")
        if city is not None:
            qs = qs.filter(city=city)
        matches: list[Candidate] = []
        for venue in qs:
            if normalize_name(venue.name) == norm:
                matches.append(
                    Candidate(
                        internal_id=venue.pk,
                        score=1.0,
                        breakdown={
                            "match": "normalized_name",
                            "normalized_name": norm,
                            "qualifier": "city" if city is not None else None,
                        },
                    )
                )
        matches.sort(key=lambda c: c.score, reverse=True)
        return matches

    def create_stub(
        self, provider: DataProvider, provider_entity_id: str, attributes: Mapping[str, Any]
    ) -> Any:
        new_id = uuid7()
        ProviderEntityMap.objects.create(
            provider=provider,
            entity_type=_ENTITY_TYPE,
            internal_id=new_id,
            provider_entity_id=provider_entity_id,
            provider_unique_name=attributes.get("name"),
            confidence=1.0,
            verified=False,
        )
        return Venue.objects.create(
            id=new_id,
            name=attributes["name"],
            city=attributes.get("city"),
            timezone=attributes.get("timezone", "UTC"),
            source_provider_id=provider.pk,
            provider_venue_id=provider_entity_id,
        ).pk


__all__ = ["VenueResolver", "upsert_venue"]
