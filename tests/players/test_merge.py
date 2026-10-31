"""merge_players: re-pointing, idempotency, cycle/self refusal, no unmerge."""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction

from apps.core.enums import TeamType
from apps.core.errors import Conflict
from apps.players.models import Player, PlayerMerge
from apps.players.services.merge import merge_players
from apps.providers.models import DataProvider, ProviderEntityMap
from apps.teams.models import Squad, SquadMember, Team, TeamMembership
from tests.dummy_app.models import DummyPlayerRef

pytestmark = pytest.mark.django_db


@pytest.fixture
def provider() -> DataProvider:
    return DataProvider.objects.create(name="Test Provider", code="test-provider")


def make_player(**overrides: object) -> Player:
    defaults: dict[str, object] = {"full_name": "Player One"}
    defaults.update(overrides)
    return Player.objects.create(**defaults)  # type: ignore[arg-type]


def make_team(**overrides: object) -> Team:
    defaults: dict[str, object] = {"name": "Atlantis XI", "team_type": TeamType.INTERNATIONAL}
    defaults.update(overrides)
    return Team.objects.create(**defaults)  # type: ignore[arg-type]


def make_map(provider: DataProvider, player: Player, *, ext_id: str) -> ProviderEntityMap:
    return ProviderEntityMap.objects.create(
        provider=provider,
        entity_type="player",
        internal_id=player.pk,
        provider_entity_id=ext_id,
        verified=False,
    )


class TestMergeHappyPath:
    def test_merge_repoints_every_reference(self, provider: DataProvider) -> None:
        winner = make_player(full_name="Virat Kohli")
        loser = make_player(full_name="V Kohli")
        team = make_team()
        squad = Squad.objects.create(team=team, name="2026 squad")

        map_row = make_map(provider, loser, ext_id="opt-vk-1")
        TeamMembership.objects.create(team=team, player=loser)
        SquadMember.objects.create(squad=squad, player=loser)
        DummyPlayerRef.objects.create(player=loser, label="dummy-1")

        merge = merge_players(winner, loser, actor="steward-1", reason="duplicate")

        # The merge record is written.
        assert merge.canonical_player_id == winner.pk
        assert merge.merged_player_id == loser.pk
        assert merge.reason == "duplicate"
        assert PlayerMerge.objects.count() == 1

        # Every reference re-points loser -> winner.
        map_row.refresh_from_db()
        assert map_row.internal_id == winner.pk
        assert TeamMembership.objects.get().player_id == winner.pk
        assert SquadMember.objects.get().player_id == winner.pk
        assert DummyPlayerRef.objects.get().player_id == winner.pk

        # Loser is soft-deleted; winner untouched.
        loser.refresh_from_db()
        assert loser.is_active is False
        assert loser.deleted_at is not None
        winner.refresh_from_db()
        assert winner.is_active is True
        assert winner.deleted_at is None

    def test_merge_repoints_canonical_rows_pointing_at_loser(self, provider: DataProvider) -> None:
        # A previous merge made `loser` the canonical of `third`; merging the
        # loser away must carry that redirect forward.
        loser = make_player(full_name="Middle")
        third = make_player(full_name="Third")
        winner = make_player(full_name="Final")
        PlayerMerge.objects.create(canonical_player=loser, merged_player=third, reason="earlier")

        merge_players(winner, loser, actor="s", reason="dup")

        row = PlayerMerge.objects.get(merged_player=third)
        assert row.canonical_player_id == winner.pk

    def test_merge_repoints_provider_map_rows_of_redirect_chain(
        self, provider: DataProvider
    ) -> None:
        # A's map row -> merge A into B (row follows to B) -> merge B into C
        # (row follows to C): A's row ends on the final canonical.
        a = make_player(full_name="A")
        b = make_player(full_name="B")
        c = make_player(full_name="C")
        map_row = make_map(provider, a, ext_id="opt-a-1")

        merge_players(b, a, actor="s", reason="a->b")
        map_row.refresh_from_db()
        assert map_row.internal_id == b.pk

        merge_players(c, b, actor="s", reason="b->c")

        map_row.refresh_from_db()
        assert map_row.internal_id == c.pk
        assert PlayerMerge.objects.count() == 2


class TestMergeIdempotency:
    def test_merge_retry_returns_existing_row(self, provider: DataProvider) -> None:
        winner = make_player(full_name="Virat Kohli")
        loser = make_player(full_name="V Kohli")
        map_row = make_map(provider, loser, ext_id="opt-vk-1")

        first = merge_players(winner, loser, actor="s", reason="dup")
        second = merge_players(winner, loser, actor="s", reason="dup")

        assert second.pk == first.pk
        assert PlayerMerge.objects.count() == 1
        # No double re-point: the map still points once at the winner.
        map_row.refresh_from_db()
        assert map_row.internal_id == winner.pk
        assert ProviderEntityMap.objects.count() == 1

    def test_merge_already_merged_loser_is_noop(self) -> None:
        winner = make_player(full_name="Virat Kohli")
        loser = make_player(full_name="V Kohli")
        first = merge_players(winner, loser, actor="s", reason="dup")

        # Merging a *different* winner into the already-merged loser follows
        # the redirect and is a no-op against the existing merge.
        other = make_player(full_name="Someone Else")
        result = merge_players(other, loser, actor="s", reason="dup")
        assert result.pk == first.pk
        assert PlayerMerge.objects.count() == 1
        # The redirect target stays the original winner.
        assert result.canonical_player_id == winner.pk


class TestMergeRefusals:
    def test_merge_winner_equals_loser_refused(self) -> None:
        player = make_player(full_name="Virat Kohli")
        with pytest.raises(Conflict):
            merge_players(player, player, actor="s", reason="self-merge")
        assert PlayerMerge.objects.count() == 0

    def test_merge_cycle_refused(self) -> None:
        winner = make_player(full_name="Virat Kohli")
        loser = make_player(full_name="V Kohli")
        merge_players(winner, loser, actor="s", reason="dup")

        # loser is already merged into winner; making winner the loser of
        # loser would create a cycle, so it must be refused.
        with pytest.raises(Conflict):
            merge_players(loser, winner, actor="s", reason="cycle")
        assert PlayerMerge.objects.count() == 1

    def test_merge_redirect_cycle_refused(self) -> None:
        a = make_player(full_name="A")
        b = make_player(full_name="B")
        c = make_player(full_name="C")
        # Corrupt redirect data: A -> B and B -> A form a genuine cycle.
        PlayerMerge.objects.create(canonical_player=b, merged_player=a, reason="a->b")
        PlayerMerge.objects.create(canonical_player=a, merged_player=b, reason="b->a")
        # Resolving the winner through the loop must be refused, not looped.
        with pytest.raises(Conflict):
            merge_players(a, c, actor="s", reason="corrupt")
        assert PlayerMerge.objects.count() == 2  # nothing new written


class TestMergeFailureAtomicity:
    def test_failed_reference_repaint_rolls_back(self, provider: DataProvider) -> None:
        # winner and loser are already in the SAME squad, so re-pointing the
        # loser's SquadMember violates ux_squad_member -> IntegrityError.
        winner = make_player(full_name="Virat Kohli")
        loser = make_player(full_name="V Kohli")
        team = make_team()
        squad = Squad.objects.create(team=team, name="2026 squad")
        SquadMember.objects.create(squad=squad, player=winner)
        SquadMember.objects.create(squad=squad, player=loser)
        map_row = make_map(provider, loser, ext_id="opt-vk-1")

        with pytest.raises(IntegrityError):
            merge_players(winner, loser, actor="s", reason="dup")

        # Nothing was committed: no merge row, map still on loser, loser alive.
        assert PlayerMerge.objects.count() == 0
        map_row.refresh_from_db()
        assert map_row.internal_id == loser.pk
        loser.refresh_from_db()
        assert loser.is_active is True
        assert loser.deleted_at is None

    def test_audit_written_inside_transaction_rolls_back_with_it(
        self, provider: DataProvider
    ) -> None:
        from apps.administration.models import AuditLog

        winner = make_player(full_name="Virat Kohli")
        loser = make_player(full_name="V Kohli")

        with pytest.raises(RuntimeError), transaction.atomic():
            merge_players(winner, loser, actor="s", reason="dup")
            raise RuntimeError("force rollback")

        assert PlayerMerge.objects.count() == 0
        assert AuditLog.objects.filter(action="player.merged").count() == 0
