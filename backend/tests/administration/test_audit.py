"""Audit wiring: merge/verify/review events write AuditLog rows atomically."""

from __future__ import annotations

import pytest
from django.apps import apps as django_apps
from django.db import transaction

from apps.administration.models import AuditLog
from apps.core import events
from apps.players.models import Player
from apps.players.services import merge_players, verify_mapping
from apps.providers.models import DataProvider, ProviderEntityMap

pytestmark = pytest.mark.django_db


def make_player(**overrides: object) -> Player:
    defaults: dict[str, object] = {"full_name": "Player One"}
    defaults.update(overrides)
    return Player.objects.create(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def provider() -> DataProvider:
    return DataProvider.objects.create(name="Test Provider", code="test-provider")


class TestPlayerMergedAudit:
    def test_merge_writes_one_audit_row(self) -> None:
        winner = make_player(full_name="Virat Kohli")
        loser = make_player(full_name="V Kohli")
        merge_players(winner, loser, actor="steward-1", reason="duplicate")

        rows = AuditLog.objects.filter(action=events.TOPIC_PLAYER_MERGED)
        assert rows.count() == 1
        row = rows.get()
        assert row.entity_type == "player"
        assert row.entity_id == winner.pk
        assert row.actor_label == "steward-1"
        assert row.after == {
            "canonical_player_id": str(winner.pk),
            "merged_player_id": str(loser.pk),
        }

    def test_audit_row_visible_inside_transaction(self) -> None:
        winner = make_player(full_name="Virat Kohli")
        loser = make_player(full_name="V Kohli")
        with transaction.atomic():
            merge_players(winner, loser, actor="s", reason="dup")
            # on_commit=False: the audit row exists before the commit.
            assert AuditLog.objects.filter(action=events.TOPIC_PLAYER_MERGED).count() == 1
        # ... and survives it.
        assert AuditLog.objects.filter(action=events.TOPIC_PLAYER_MERGED).count() == 1

    def test_failed_merge_writes_no_audit_row(self) -> None:
        winner = make_player(full_name="Virat Kohli")
        loser = make_player(full_name="V Kohli")
        with pytest.raises(RuntimeError), transaction.atomic():
            merge_players(winner, loser, actor="s", reason="dup")
            raise RuntimeError("force rollback")
        assert AuditLog.objects.filter(action=events.TOPIC_PLAYER_MERGED).count() == 0
        assert AuditLog.objects.count() == 0


class TestProviderMapVerifiedAudit:
    def test_verify_mapping_writes_audit_row(self, provider: DataProvider) -> None:
        player = make_player(full_name="Virat Kohli")
        map_row = ProviderEntityMap.objects.create(
            provider=provider,
            entity_type="player",
            internal_id=player.pk,
            provider_entity_id="opt-vk-1",
            verified=False,
        )
        verify_mapping(map_row, actor="steward-1")

        rows = AuditLog.objects.filter(action=events.TOPIC_PROVIDER_MAP_VERIFIED)
        assert rows.count() == 1
        row = rows.get()
        assert row.entity_type == "provider_entity_map"
        assert row.entity_id == map_row.pk
        assert row.actor_label == "steward-1"


class TestDuplicateReviewedAudit:
    def test_duplicate_reviewed_writes_audit_row(self) -> None:
        candidate = make_player(full_name="Candidate")
        canonical = make_player(full_name="Canonical")
        events.publish(
            events.TOPIC_DUPLICATE_REVIEWED,
            {
                "duplicate_candidate_id": str(candidate.pk),
                "canonical_player_id": str(canonical.pk),
                "decision": "merged",
                "actor": "steward-1",
            },
            on_commit=False,
        )
        rows = AuditLog.objects.filter(action=events.TOPIC_DUPLICATE_REVIEWED)
        assert rows.count() == 1
        row = rows.get()
        assert row.entity_type == "player_duplicate_candidate"
        assert row.entity_id == candidate.pk
        assert row.after == {
            "canonical_player_id": str(canonical.pk),
            "decision": "merged",
        }


class TestReadyIdempotency:
    def test_multiple_ready_calls_do_not_double_subscribe(self) -> None:
        cfg = django_apps.get_app_config("administration")
        cfg.ready()
        cfg.ready()

        winner = make_player(full_name="Virat Kohli")
        loser = make_player(full_name="V Kohli")
        merge_players(winner, loser, actor="s", reason="dup")
        assert AuditLog.objects.filter(action=events.TOPIC_PLAYER_MERGED).count() == 1
