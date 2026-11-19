"""Competition + series write services and their entity resolvers (Group 3.4)."""

from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction

from apps.competitions.models import Competition, Series
from apps.core.enums import CompetitionType
from apps.core.events import (
    TOPIC_COMPETITION_UPDATED,
    TOPIC_SERIES_UPDATED,
    publish,
)
from apps.core.uuid7 import uuid7
from apps.players.identity_scoring import normalize_name
from apps.providers.models import DataProvider, ProviderEntityMap
from apps.providers.services import Candidate, Resolver, is_pem_race

_COMPETITION_ENTITY_TYPE = "competition"
_SERIES_ENTITY_TYPE = "series"


def _apply(competition: Competition, fields: dict[str, Any]) -> Competition:
    for name, value in fields.items():
        setattr(competition, name, value)
    return competition


def upsert_competition(
    provider: DataProvider,
    provider_entity_id: str,
    *,
    name: str,
    competition_type: str,
    short_name: str | None = None,
    governing_body_id: UUID | None = None,
    format_type: str | None = None,
    is_international: bool = False,
    gender: str = "M",
) -> Competition:
    """Create or update the competition for ``(provider, provider_entity_id)``.

    Keyed via the provider map, never by name alone (see
    :func:`~apps.venues.services.venues.upsert_venue` for the pattern).
    """
    fields: dict[str, Any] = {
        "name": name,
        "competition_type": competition_type,
        "short_name": short_name,
        "governing_body_id": governing_body_id,
        "format_type": format_type,
        "is_international": is_international,
        "gender": gender,
    }
    with transaction.atomic():
        map_row = (
            ProviderEntityMap.objects.select_for_update()
            .filter(
                provider=provider,
                entity_type=_COMPETITION_ENTITY_TYPE,
                provider_entity_id=provider_entity_id,
            )
            .first()
        )
        if map_row is not None:
            competition = Competition.objects.select_for_update().get(pk=map_row.internal_id)
            _apply(competition, fields)
            competition.save()
            publish(TOPIC_COMPETITION_UPDATED, {"competition_id": str(competition.pk)})
            return competition

        try:
            with transaction.atomic():
                competition = Competition.objects.create(
                    source_provider_id=provider.pk,
                    provider_competition_id=provider_entity_id,
                    **fields,
                )
                ProviderEntityMap.objects.create(
                    provider=provider,
                    entity_type=_COMPETITION_ENTITY_TYPE,
                    internal_id=competition.pk,
                    provider_entity_id=provider_entity_id,
                    provider_unique_name=name,
                    verified=False,
                )
        except IntegrityError as exc:
            if not is_pem_race(exc):
                raise
            map_row = ProviderEntityMap.objects.get(
                provider=provider,
                entity_type=_COMPETITION_ENTITY_TYPE,
                provider_entity_id=provider_entity_id,
            )
            competition = Competition.objects.get(pk=map_row.internal_id)
            _apply(competition, fields)
            competition.save()
        publish(TOPIC_COMPETITION_UPDATED, {"competition_id": str(competition.pk)})
        return competition


def upsert_series(
    provider: DataProvider,
    provider_entity_id: str,
    *,
    name: str,
    short_name: str | None = None,
    competition_id: UUID | None = None,
    season_id: UUID | None = None,
    series_type: str = "bilateral",
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
    host_country_id: UUID | None = None,
    is_tournament: bool = False,
    points_system: dict[str, Any] | None = None,
) -> Series:
    """Create or update the series for ``(provider, provider_entity_id)``."""
    fields: dict[str, Any] = {
        "name": name,
        "short_name": short_name,
        "competition_id": competition_id,
        "season_id": season_id,
        "series_type": series_type,
        "start_date": start_date,
        "end_date": end_date,
        "host_country_id": host_country_id,
        "is_tournament": is_tournament,
        "points_system": points_system,
    }
    with transaction.atomic():
        map_row = (
            ProviderEntityMap.objects.select_for_update()
            .filter(
                provider=provider,
                entity_type=_SERIES_ENTITY_TYPE,
                provider_entity_id=provider_entity_id,
            )
            .first()
        )
        if map_row is not None:
            series = Series.objects.select_for_update().get(pk=map_row.internal_id)
            for name_, value in fields.items():
                setattr(series, name_, value)
            series.save()
            publish(TOPIC_SERIES_UPDATED, {"series_id": str(series.pk)})
            return series

        try:
            with transaction.atomic():
                series = Series.objects.create(
                    source_provider_id=provider.pk,
                    provider_series_id=provider_entity_id,
                    **fields,
                )
                ProviderEntityMap.objects.create(
                    provider=provider,
                    entity_type=_SERIES_ENTITY_TYPE,
                    internal_id=series.pk,
                    provider_entity_id=provider_entity_id,
                    provider_unique_name=name,
                    verified=False,
                )
        except IntegrityError as exc:
            if not is_pem_race(exc):
                raise
            map_row = ProviderEntityMap.objects.get(
                provider=provider,
                entity_type=_SERIES_ENTITY_TYPE,
                provider_entity_id=provider_entity_id,
            )
            series = Series.objects.get(pk=map_row.internal_id)
            for name_, value in fields.items():
                setattr(series, name_, value)
            series.save()
        publish(TOPIC_SERIES_UPDATED, {"series_id": str(series.pk)})
        return series


class CompetitionResolver(Resolver):
    """Entity resolver for ``entity_type="competition"`` (normalised name)."""

    def match(self, attributes: Mapping[str, Any]) -> list[Candidate]:
        name = attributes.get("name")
        if not name:
            return []
        norm = normalize_name(name)
        matches: list[Candidate] = []
        for competition in Competition.objects.all():
            if normalize_name(competition.name) == norm:
                matches.append(
                    Candidate(
                        internal_id=competition.pk,
                        score=1.0,
                        breakdown={"match": "normalized_name", "normalized_name": norm},
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
            entity_type=_COMPETITION_ENTITY_TYPE,
            internal_id=new_id,
            provider_entity_id=provider_entity_id,
            provider_unique_name=attributes.get("name"),
            confidence=1.0,
            verified=False,
        )
        return Competition.objects.create(
            id=new_id,
            name=attributes["name"],
            competition_type=attributes.get("competition_type", CompetitionType.LEAGUE),
            short_name=attributes.get("short_name"),
            is_international=attributes.get("is_international", False),
            source_provider_id=provider.pk,
            provider_competition_id=provider_entity_id,
        ).pk


class SeriesResolver(Resolver):
    """Entity resolver for ``entity_type="series"`` (normalised name + start year)."""

    def match(self, attributes: Mapping[str, Any]) -> list[Candidate]:
        name = attributes.get("name")
        if not name:
            return []
        norm = normalize_name(name)
        qs = Series.objects.all()
        start_date = attributes.get("start_date")
        if start_date:
            qs = qs.filter(start_date__year=start_date.year)
        matches: list[Candidate] = []
        for series in qs:
            if normalize_name(series.name) == norm:
                matches.append(
                    Candidate(
                        internal_id=series.pk,
                        score=1.0,
                        breakdown={
                            "match": "normalized_name",
                            "normalized_name": norm,
                            "qualifier": "start_year" if start_date is not None else None,
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
            entity_type=_SERIES_ENTITY_TYPE,
            internal_id=new_id,
            provider_entity_id=provider_entity_id,
            provider_unique_name=attributes.get("name"),
            confidence=1.0,
            verified=False,
        )
        return Series.objects.create(
            id=new_id,
            name=attributes["name"],
            short_name=attributes.get("short_name"),
            competition_id=attributes.get("competition_id"),
            season_id=attributes.get("season_id"),
            series_type=attributes.get("series_type", "bilateral"),
            start_date=attributes.get("start_date"),
            end_date=attributes.get("end_date"),
            host_country_id=attributes.get("host_country_id"),
            is_tournament=attributes.get("is_tournament", False),
            source_provider_id=provider.pk,
            provider_series_id=provider_entity_id,
        ).pk


__all__ = [
    "CompetitionResolver",
    "SeriesResolver",
    "upsert_competition",
    "upsert_series",
]
