"""Player upsert service + mark_inactive + verify_mapping."""

from __future__ import annotations

import datetime

import pytest

from apps.core.events import TOPIC_PROVIDER_MAP_VERIFIED, clear_subscriptions, subscribe
from apps.players.models import Player
from apps.players.services import mark_inactive, upsert_player, verify_mapping
from apps.providers.models import DataProvider, ProviderEntityMap

pytestmark = pytest.mark.django_db


@pytest.fixture
def provider() -> DataProvider:
    return DataProvider.objects.create(name="Test Provider", code="test-provider")


def test_creates_player_and_map_row(provider: DataProvider) -> None:
    player = upsert_player(
        provider,
        "opt-p-1",
        full_name="Virat Kohli",
        date_of_birth=datetime.date(1988, 11, 5),
    )
    assert player.full_name == "Virat Kohli"
    assert player.is_active is True
    assert player.source_provider_id == provider.pk
    assert player.provider_player_id == "opt-p-1"

    map_row = ProviderEntityMap.objects.get(
        provider=provider, entity_type="player", provider_entity_id="opt-p-1"
    )
    assert map_row.internal_id == player.pk
    assert map_row.verified is False


def test_run_twice_is_idempotent(provider: DataProvider) -> None:
    first = upsert_player(provider, "opt-p-1", full_name="Virat Kohli")
    second = upsert_player(provider, "opt-p-1", full_name="Virat Kohli")
    assert first.pk == second.pk
    assert Player.objects.count() == 1
    assert ProviderEntityMap.objects.filter(entity_type="player").count() == 1


def test_update_path_updates_fields(provider: DataProvider) -> None:
    player = upsert_player(provider, "opt-p-1", full_name="Virat Kohli")
    updated = upsert_player(
        provider,
        "opt-p-1",
        full_name="Virat Kohli",
        date_of_birth=datetime.date(1988, 11, 5),
        known_as="Cheeku",
    )
    assert updated.pk == player.pk
    updated.refresh_from_db()
    assert updated.date_of_birth == datetime.date(1988, 11, 5)
    assert updated.known_as == "Cheeku"
    assert Player.objects.count() == 1


def test_name_based_upsert_never_matches_an_existing_player(provider: DataProvider) -> None:
    existing = Player.objects.create(full_name="Virat Kohli")
    player = upsert_player(provider, "opt-p-2", full_name="Virat Kohli")
    assert player.pk != existing.pk
    assert Player.objects.count() == 2


def test_mark_inactive_sets_flag_and_deleted_at(provider: DataProvider) -> None:
    player = upsert_player(provider, "opt-p-1", full_name="Virat Kohli")
    mark_inactive(player)
    player.refresh_from_db()
    assert player.is_active is False
    assert player.deleted_at is not None


def test_verify_mapping_sets_verified_and_publishes(provider: DataProvider) -> None:
    upsert_player(provider, "opt-p-1", full_name="Virat Kohli")
    map_row = ProviderEntityMap.objects.get(
        provider=provider, entity_type="player", provider_entity_id="opt-p-1"
    )

    payloads: list[dict[str, object]] = []

    def handler(payload: object) -> None:
        payloads.append(payload)  # type: ignore[arg-type]

    subscribe(TOPIC_PROVIDER_MAP_VERIFIED, handler)
    try:
        verify_mapping(map_row, actor="steward-1")
    finally:
        clear_subscriptions()

    map_row.refresh_from_db()
    assert map_row.verified is True
    assert payloads == [{"provider_entity_map_id": str(map_row.pk), "actor": "steward-1"}]
