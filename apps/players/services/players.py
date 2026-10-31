"""Player write services: upsert, deactivate, map verification."""

from __future__ import annotations

import datetime
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.events import TOPIC_PROVIDER_MAP_VERIFIED, publish
from apps.players.models import Player
from apps.providers.models import DataProvider, ProviderEntityMap
from apps.providers.services import is_pem_race

_ENTITY_TYPE = "player"


def _apply(player: Player, fields: dict[str, Any]) -> Player:
    for name, value in fields.items():
        setattr(player, name, value)
    return player


def upsert_player(
    provider: DataProvider,
    provider_entity_id: str,
    *,
    full_name: str,
    known_as: str | None = None,
    unique_name: str | None = None,
    date_of_birth: datetime.date | None = None,
    date_of_death: datetime.date | None = None,
    birth_country_id: UUID | None = None,
    birth_place: str | None = None,
    gender: str = "M",
    height_cm: int | None = None,
    batting_style: str | None = None,
    bowling_style: str | None = None,
    primary_role: str | None = None,
    is_wicketkeeper: bool = False,
    international_team_id: UUID | None = None,
    face_image_url: str | None = None,
    bio: str | None = None,
) -> Player:
    """Create or update the player for ``(provider, provider_entity_id)``.

    Keyed via the provider map, never by name alone (see
    :func:`~apps.venues.services.venues.upsert_venue` for the pattern).
    """
    fields: dict[str, Any] = {
        "full_name": full_name,
        "known_as": known_as,
        "unique_name": unique_name,
        "date_of_birth": date_of_birth,
        "date_of_death": date_of_death,
        "birth_country_id": birth_country_id,
        "birth_place": birth_place,
        "gender": gender,
        "height_cm": height_cm,
        "batting_style": batting_style,
        "bowling_style": bowling_style,
        "primary_role": primary_role,
        "is_wicketkeeper": is_wicketkeeper,
        "international_team_id": international_team_id,
        "face_image_url": face_image_url,
        "bio": bio,
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
            player = Player.objects.select_for_update().get(pk=map_row.internal_id)
            _apply(player, fields)
            player.save()
            return player

        try:
            with transaction.atomic():
                player = Player.objects.create(
                    source_provider_id=provider.pk,
                    provider_player_id=provider_entity_id,
                    **fields,
                )
                ProviderEntityMap.objects.create(
                    provider=provider,
                    entity_type=_ENTITY_TYPE,
                    internal_id=player.pk,
                    provider_entity_id=provider_entity_id,
                    provider_unique_name=full_name,
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
            player = Player.objects.get(pk=map_row.internal_id)
            _apply(player, fields)
            player.save()
        return player


def mark_inactive(player: Player) -> Player:
    """Soft-delete a player (the merge path also uses this; no unmerge)."""
    player.is_active = False
    player.deleted_at = timezone.now()
    player.save(update_fields=["is_active", "deleted_at"])
    return player


def verify_mapping(map_row: ProviderEntityMap, *, actor: str) -> ProviderEntityMap:
    """Mark a provider-entity mapping as steward-verified and audit it.

    The audit event is published with ``on_commit=False`` so the AuditLog row
    commits or rolls back with ``verified`` (Group 4 subscribes the handler).
    """
    map_row.verified = True
    map_row.save(update_fields=["verified"])
    publish(
        TOPIC_PROVIDER_MAP_VERIFIED,
        {"provider_entity_map_id": str(map_row.pk), "actor": actor},
        on_commit=False,
    )
    return map_row


__all__ = ["mark_inactive", "upsert_player", "verify_mapping"]
