"""Team-domain write services + the team entity resolver (Group 3.4)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction

from apps.core.enums import TeamType
from apps.core.events import TOPIC_TEAM_UPDATED, publish
from apps.core.uuid7 import uuid7
from apps.players.identity_scoring import normalize_name
from apps.providers.models import DataProvider, ProviderEntityMap
from apps.providers.services import Candidate, Resolver, is_pem_race
from apps.teams.models import Team

_ENTITY_TYPE = "team"


def _apply(team: Team, fields: dict[str, Any]) -> Team:
    for name, value in fields.items():
        setattr(team, name, value)
    return team


def upsert_team(
    provider: DataProvider,
    provider_entity_id: str,
    *,
    name: str,
    team_type: str,
    short_name: str | None = None,
    abbreviation: str | None = None,
    country_id: UUID | None = None,
    governing_body_id: UUID | None = None,
    home_venue_id: UUID | None = None,
    gender: str = "M",
    is_international: bool = False,
    logo_url: str | None = None,
) -> Team:
    """Create or update the team for ``(provider, provider_entity_id)``.

    Keyed via the provider map, never by name alone (see
    :func:`~apps.venues.services.venues.upsert_venue` for the pattern).
    """
    fields: dict[str, Any] = {
        "name": name,
        "team_type": team_type,
        "short_name": short_name,
        "abbreviation": abbreviation,
        "country_id": country_id,
        "governing_body_id": governing_body_id,
        "home_venue_id": home_venue_id,
        "gender": gender,
        "is_international": is_international,
        "logo_url": logo_url,
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
            team = Team.objects.select_for_update().get(pk=map_row.internal_id)
            _apply(team, fields)
            team.save()
            publish(TOPIC_TEAM_UPDATED, {"team_id": str(team.pk)})
            return team

        try:
            with transaction.atomic():
                team = Team.objects.create(
                    source_provider_id=provider.pk,
                    provider_team_id=provider_entity_id,
                    **fields,
                )
                ProviderEntityMap.objects.create(
                    provider=provider,
                    entity_type=_ENTITY_TYPE,
                    internal_id=team.pk,
                    provider_entity_id=provider_entity_id,
                    provider_unique_name=name,
                    verified=False,
                )
        except IntegrityError as exc:
            if not is_pem_race(exc):
                raise
            map_row = ProviderEntityMap.objects.get(
                provider=provider,
                entity_type=_ENTITY_TYPE,
                provider_entity_id=provider_entity_id,
            )
            team = Team.objects.get(pk=map_row.internal_id)
            _apply(team, fields)
            team.save()
        publish(TOPIC_TEAM_UPDATED, {"team_id": str(team.pk)})
        return team


class TeamResolver(Resolver):
    """Entity resolver for ``entity_type="team"`` (normalised name + country).

    Registered from ``teams.apps.ready()``; used by the generic
    :func:`apps.providers.services.resolve`.
    """

    def match(self, attributes: Mapping[str, Any]) -> list[Candidate]:
        name = attributes.get("name")
        if not name:
            return []
        norm = normalize_name(name)
        qs = Team.objects.filter(is_active=True, deleted_at__isnull=True)
        country_id = attributes.get("country_id")
        if country_id:
            qs = qs.filter(country_id=country_id)
        matches: list[Candidate] = []
        for team in qs:
            if normalize_name(team.name) == norm:
                matches.append(
                    Candidate(
                        internal_id=team.pk,
                        score=1.0,
                        breakdown={
                            "match": "normalized_name",
                            "normalized_name": norm,
                            "qualifier": "country_id" if country_id else None,
                        },
                    )
                )
        matches.sort(key=lambda c: c.score, reverse=True)
        return matches

    def create_stub(
        self, provider: DataProvider, provider_entity_id: str, attributes: Mapping[str, Any]
    ) -> Any:
        new_id = uuid7()
        # Map row first (pre-generated id): a lost race aborts before the
        # entity row exists, so no orphan teams are left behind.
        ProviderEntityMap.objects.create(
            provider=provider,
            entity_type=_ENTITY_TYPE,
            internal_id=new_id,
            provider_entity_id=provider_entity_id,
            provider_unique_name=attributes.get("name"),
            confidence=1.0,
            verified=False,
        )
        return Team.objects.create(
            id=new_id,
            name=attributes["name"],
            team_type=attributes.get("team_type", TeamType.OTHER),
            short_name=attributes.get("short_name"),
            country_id=attributes.get("country_id"),
            is_active=True,
            source_provider_id=provider.pk,
            provider_team_id=provider_entity_id,
        ).pk


__all__ = ["TeamResolver", "upsert_team"]
