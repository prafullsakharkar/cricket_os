"""Phase I Task 4 (4c): the reversible match merge/split service.

* every child table's rows re-point into the canonical where they fit, and
  conflicting rows (toss/result/innings/teams already present) stay on the
  merged row — invisible via ``merged_into_id``;
* ``player_match_stat`` rows are captured (verbatim) and removed so the
  Tier-2 aggregates can never count the same match twice, and split restores
  them;
* merge is idempotent (a retry collapses onto the existing ``match_merge``
  row), self/cycle merges raise ``Conflict``, and a concurrent retry produces
  exactly one merge row (advisory locks);
* split reverses the ``before`` inventory exactly and splitting twice is a
  ``Conflict``;
* ``recompute_affected_series`` is idempotent and never double-counts;
* ``match.merged`` / ``match.split`` write audit rows.
"""

from __future__ import annotations

import threading
import uuid as uuidlib
from datetime import UTC, datetime

import pytest
from django.utils import timezone
from tests.phase_c.factories import (
    make_innings,
    make_match,
    make_match_teams,
    make_player,
    make_provider,
    make_team,
)

from apps.administration.models import AuditLog
from apps.core import events
from apps.core.enums import MatchStatus
from apps.core.errors import Conflict
from apps.matches.models import (
    Match,
    MatchAward,
    MatchMerge,
    MatchOfficial,
    MatchPlayer,
    MatchResult,
    MatchTeam,
    Toss,
)
from apps.matches.services import merge
from apps.providers.models import ProviderEntityMap
from apps.statistics.models import PlayerCareerStat, PlayerMatchStat, StatScope
from apps.statistics.services import recompute

pytestmark = pytest.mark.django_db

DAY = datetime(2026, 10, 25, 9, 0, tzinfo=UTC)


def _pair(*, status: str = MatchStatus.COMPLETED) -> tuple[Match, Match, object, object]:
    """Canonical + duplicate matches sharing the same two teams."""
    india = make_team("India")
    australia = make_team("Australia")
    kwargs: dict[str, object] = {
        "start_time_utc": DAY,
        "match_number": 1,
        "status": status,
    }
    if status in MatchStatus.values and status not in (
        MatchStatus.SCHEDULED,
        MatchStatus.POSTPONED,
    ):
        kwargs["end_time_utc"] = timezone.now()
    canonical = make_match(**kwargs)
    merged = make_match(**kwargs)
    make_match_teams(canonical, india, australia)
    make_match_teams(merged, india, australia)
    return canonical, merged, india, australia


def _attach_children(
    match: Match,
    india: object,
    australia: object,
    players: tuple[object, object] | None = None,
) -> tuple[object, object]:
    """Every non-conflicting child type on ``match``.

    ``players`` reuses the same player rows (so ``match_player`` conflicts when
    the canonical already lists them); otherwise fresh players are created.
    """
    if players is not None:
        p1, p2 = players
    else:
        p1 = make_player("Virat Kohli")
        p2 = make_player("Mitchell Starc")
    MatchPlayer.objects.create(match=match, player=p1, team=india, batting_position=1)
    MatchPlayer.objects.create(match=match, player=p2, team=australia, batting_position=1)
    Toss.objects.create(match=match, winner_team=india, decision="bat")
    MatchOfficial.objects.create(match=match, official_name="Ump One", role="umpire1")
    MatchResult.objects.create(match=match, result_type="WIN", winner_team=india)
    MatchAward.objects.create(match=match, player=p1, award_type="player_of_match")
    make_innings(match, india, australia, innings_number=1)
    return p1, p2


def _map_row(match: Match, provider: object, fixture_id: str = "live-1") -> ProviderEntityMap:
    return ProviderEntityMap.objects.create(
        provider=provider,
        entity_type="match",
        internal_id=match.pk,
        provider_entity_id=fixture_id,
        is_primary=False,
        verified=False,
    )


def _make_stat(match: Match, player: object, team: object) -> PlayerMatchStat:
    from apps.scoring.config import preset_for_format
    from apps.scoring.types import Format

    config = preset_for_format(Format(match.format_type))
    batting = [
        {
            "rule_version": config.version,
            "player_id": str(player.id),
            "did_bat": True,
            "runs": 10,
            "balls": 8,
            "out": False,
        }
    ]
    bowling = [
        {
            "rule_version": config.version,
            "player_id": str(player.id),
            "legal_balls": 12,
            "runs_conceded": 9,
            "wickets": 1,
        }
    ]
    return PlayerMatchStat.objects.create(
        match=match,
        player=player,
        team=team,
        did_bat=True,
        did_bowl=True,
        batting=batting,
        bowling=bowling,
    )


def _child_match_ids() -> dict[str, set[uuidlib.UUID | None]]:
    return {
        "match_team": set(MatchTeam.objects.values_list("match_id", flat=True)),
        "toss": set(Toss.objects.values_list("match_id", flat=True)),
        "match_player": set(MatchPlayer.objects.values_list("match_id", flat=True)),
        "match_official": set(MatchOfficial.objects.values_list("match_id", flat=True)),
        "match_result": set(MatchResult.objects.values_list("match_id", flat=True)),
        "match_award": set(MatchAward.objects.values_list("match_id", flat=True)),
    }


# --------------------------------------------------------------------------- #
# Merge
# --------------------------------------------------------------------------- #


class TestMerge:
    def test_moves_every_non_conflicting_child_and_flags_merged_row(self) -> None:
        canonical, merged, india, australia = _pair()
        _attach_children(merged, india, australia)
        provider = make_provider("live")
        map_row = _map_row(merged, provider)

        merge_row = merge.merge_matches(merged, canonical, actor="steward-1")

        # Every moveable child now lives under the canonical. The match_team
        # rows conflict on (team_number, team_id) — the two providers resolve
        # the same two teams — so they stay on the merged row (invisible).
        for table in ("toss", "match_player", "match_official", "match_result", "match_award"):
            assert _child_match_ids()[table] == {canonical.id}, f"{table} not re-pointed"
        # Both matches keep their (identical) team rows: the merged ones stay.
        assert set(_child_match_ids()["match_team"]) == {canonical.id, merged.id}
        assert set(merged.innings.values_list("match_id", flat=True)) == set()
        assert canonical.innings.filter(innings_number=1).exists()

        # The merged row is flagged and the map row re-pointed.
        merged.refresh_from_db()
        assert merged.merged_into_id == canonical.pk
        map_row.refresh_from_db()
        assert map_row.internal_id == canonical.pk
        assert map_row.is_primary is False  # canonical is COMPLETED -> not live-authoritative

        # The ledger recorded the before/after inventories per table.
        assert set(merge_row.before) == {
            "toss",
            "match_player",
            "match_official",
            "match_result",
            "match_award",
            "innings",
            "provider_entity_map",
        }
        assert merge_row.after["toss"][0]["match_id"] == str(canonical.id)
        assert merge_row.after["provider_entity_map"][0]["internal_id"] == str(canonical.id)

        # Both matches' revisions bumped.
        canonical.refresh_from_db()
        merged.refresh_from_db()
        assert canonical.revision == 2
        assert merged.revision == 2

    def test_conflicting_children_stay_on_merged_row(self) -> None:
        canonical, merged, india, australia = _pair()
        players = _attach_children(merged, india, australia)
        # The canonical already has every child type (same players/teams) ->
        # all merged rows conflict and stay.
        _attach_children(canonical, india, australia, players=players)

        merge.merge_matches(merged, canonical, actor="steward-1")

        # toss / result / innings / teams / players stay on the merged row
        # (invisible via merged_into).
        assert Toss.objects.get(match_id=merged.id) is not None
        assert MatchResult.objects.get(match_id=merged.id) is not None
        assert merged.innings.filter(innings_number=1).exists()
        assert MatchTeam.objects.filter(match_id=merged.id).count() == 2
        assert MatchPlayer.objects.filter(match_id=merged.id).count() == 2
        # Nothing moved, so the before inventory is empty (no map row either).
        merge_row = MatchMerge.objects.get()
        assert merge_row.before == {}

    def test_super_over_moves_when_no_conflict_and_normal_stays(self) -> None:
        canonical, merged, india, australia = _pair()
        make_innings(canonical, india, australia, innings_number=1)
        make_innings(merged, india, australia, innings_number=1)
        super_over = make_innings(
            merged, india, australia, innings_number=1, is_super_over=True, super_over_number=1
        )

        merge.merge_matches(merged, canonical, actor="steward-1")

        # The super-over innings had no conflict -> moves to the canonical; the
        # normal innings (1) conflicts with the canonical's -> stays.
        assert canonical.innings.count() == 2  # own normal + moved super over
        assert canonical.innings.filter(is_super_over=True).count() == 1
        assert super_over.id in canonical.innings.values_list("id", flat=True)
        assert merged.innings.filter(is_super_over=False, innings_number=1).exists()
        assert not merged.innings.filter(is_super_over=True).exists()

    def test_super_over_conflict_stays_under_merged(self) -> None:
        canonical, merged, india, australia = _pair()
        make_innings(canonical, india, australia, innings_number=1)
        make_innings(
            canonical, india, australia, innings_number=1, is_super_over=True, super_over_number=1
        )
        make_innings(merged, india, australia, innings_number=1)
        make_innings(
            merged, india, australia, innings_number=1, is_super_over=True, super_over_number=1
        )

        merge.merge_matches(merged, canonical, actor="steward-1")

        # Both the normal and the super-over innings conflict and stay under
        # the merged row (excluded from every selector via merged_into_id).
        assert canonical.innings.filter(is_super_over=True).count() == 1
        assert merged.innings.filter(is_super_over=True).count() == 1
        assert merged.innings.filter(is_super_over=False).count() == 1

    def test_merge_is_idempotent_retry_collapses_onto_one_row(self) -> None:
        canonical, merged, _, _ = _pair()
        first = merge.merge_matches(merged, canonical, actor="steward-1")
        second = merge.merge_matches(merged, canonical, actor="steward-1")
        assert first.id == second.id
        assert MatchMerge.objects.count() == 1

    def test_merge_self_and_merge_target_raise_conflict(self) -> None:
        canonical, merged, _, _ = _pair()
        with pytest.raises(Conflict, match="itself or its merge target"):
            merge.merge_matches(canonical, canonical, actor="steward-1")
        # Merging the canonical INTO an already-merged-away match is also refused.
        merge.merge_matches(merged, canonical, actor="steward-1")
        merged.refresh_from_db()
        with pytest.raises(Conflict, match="itself or its merge target"):
            merge.merge_matches(canonical, merged, actor="steward-1")

    def test_merge_chain_resolves_to_canonical_before_guards(self) -> None:
        a, b, _, _ = _pair()
        c = make_match(start_time_utc=DAY, match_number=2, status=MatchStatus.COMPLETED)
        # b -> a already merged; merging c into b resolves to a.
        merge.merge_matches(b, a, actor="steward-1")
        merge.merge_matches(c, b, actor="steward-1")
        c.refresh_from_db()
        assert c.merged_into_id == a.pk


# --------------------------------------------------------------------------- #
# Stats (no double counting)
# --------------------------------------------------------------------------- #


class TestStatsNoDoubleCount:
    def test_merge_captures_and_removes_merged_stats(self) -> None:
        canonical, merged, india, _ = _pair()
        player = make_player("Player One")
        _make_stat(canonical, player, india)
        merged_stat = _make_stat(merged, player, india)
        merged_stat_id = merged_stat.id

        merge_row = merge.merge_matches(merged, canonical, actor="steward-1")

        # Merged rows captured verbatim + removed from the live table.
        assert not PlayerMatchStat.objects.filter(match_id=merged.id).exists()
        assert PlayerMatchStat.objects.filter(match_id=canonical.id, player=player).count() == 1
        captured = merge_row.before["player_match_stat"]
        assert captured[0]["id"] == str(merged_stat_id)
        assert captured[0]["player_id"] == str(player.id)
        assert captured[0]["batting"][0]["runs"] == 10

    def test_career_aggregate_counts_each_match_once(self) -> None:
        canonical, merged, india, _ = _pair()
        player = make_player("Player One")
        _make_stat(canonical, player, india)
        _make_stat(merged, player, india)

        merge.merge_matches(merged, canonical, actor="steward-1")
        recompute.recompute_match(canonical, correction=True)

        # CAREER scope spans all formats (format_type is NULL on the row).
        career = PlayerCareerStat.objects.get(
            player=player, scope_type=StatScope.CAREER, format_type__isnull=True, gender="M"
        )
        assert career.matches == 1
        assert career.runs == 10

    def test_split_restores_merged_stats_rows_exactly(self) -> None:
        canonical, merged, india, _ = _pair()
        player = make_player("Player One")
        _make_stat(canonical, player, india)
        merged_stat = _make_stat(merged, player, india)

        merge_row = merge.merge_matches(merged, canonical, actor="steward-1")
        merge.split_match(merge_row, actor="steward-1")

        restored = PlayerMatchStat.objects.get(match_id=merged.id, player=player)
        assert restored.id == merged_stat.id
        assert restored.batting == merged_stat.batting
        assert restored.bowling == merged_stat.bowling
        assert restored.revision == merged_stat.revision
        # Canonical keeps its own rows.
        assert PlayerMatchStat.objects.filter(match_id=canonical.id, player=player).count() == 1

    def test_merge_of_live_matches_writes_no_stats(self) -> None:
        canonical, merged, india, australia = _pair(status=MatchStatus.IN_PROGRESS)
        _attach_children(merged, india, australia)
        merge.merge_matches(merged, canonical, actor="steward-1")
        assert PlayerMatchStat.objects.count() == 0


# --------------------------------------------------------------------------- #
# Split
# --------------------------------------------------------------------------- #


class TestSplit:
    def test_split_reverses_merge_exactly(self) -> None:
        canonical, merged, india, australia = _pair()
        _attach_children(merged, india, australia)
        provider = make_provider("live")
        _map_row(merged, provider)

        merge_row = merge.merge_matches(merged, canonical, actor="steward-1")
        # Teams conflict (same teams) so they never moved; the rest did.
        assert set(_child_match_ids()["toss"]) == {canonical.id}
        assert set(_child_match_ids()["match_result"]) == {canonical.id}
        assert set(_child_match_ids()["match_player"]) == {canonical.id}
        assert set(_child_match_ids()["match_award"]) == {canonical.id}

        split = merge.split_match(merge_row, actor="steward-1")
        assert split.status == "split"

        # Every moved child row is back under the merged match.
        for table in ("toss", "match_player", "match_official", "match_result", "match_award"):
            assert _child_match_ids()[table] == {merged.id}, f"{table} rows not restored"
        # Teams were never moved (they conflict); both matches keep theirs.
        assert set(_child_match_ids()["match_team"]) == {canonical.id, merged.id}
        assert set(merged.innings.values_list("match_id", flat=True)) == {merged.id}
        assert not canonical.innings.exists()
        merged.refresh_from_db()
        assert merged.merged_into_id is None
        # Map row restored (internal_id + is_primary).
        map_row = ProviderEntityMap.objects.get(provider=provider, provider_entity_id="live-1")
        assert map_row.internal_id == merged.pk
        assert map_row.is_primary is False
        # Revisions bumped again.
        canonical.refresh_from_db()
        assert canonical.revision == 3
        assert merged.revision == 3

    def test_split_restores_map_is_primary_after_terminal_merge(self) -> None:
        canonical, merged, _, _ = _pair()
        provider = make_provider("live")
        _map_row(merged, provider)
        merge_row = merge.merge_matches(merged, canonical, actor="steward-1")
        # Post-merge the map row is non-primary (canonical terminal); split restores.
        assert ProviderEntityMap.objects.get(internal_id=canonical.pk).is_primary is False
        merge.split_match(merge_row, actor="steward-1")
        assert ProviderEntityMap.objects.get(internal_id=merged.pk).is_primary is False

    def test_split_of_conflicting_innings_restores_both(self) -> None:
        canonical, merged, india, australia = _pair()
        make_innings(canonical, india, australia, innings_number=1)
        make_innings(
            canonical, india, australia, innings_number=1, is_super_over=True, super_over_number=1
        )
        merged_innings = make_innings(merged, india, australia, innings_number=1)
        merged_super = make_innings(
            merged, india, australia, innings_number=1, is_super_over=True, super_over_number=1
        )

        merge_row = merge.merge_matches(merged, canonical, actor="steward-1")
        # Normal innings moved; super over stayed behind (conflict).
        assert merged.innings.filter(is_super_over=True).count() == 1

        merge.split_match(merge_row, actor="steward-1")
        assert list(merged.innings.values_list("id", flat=True)) == [
            merged_innings.id,
            merged_super.id,
        ]
        assert canonical.innings.filter(is_super_over=True).count() == 1

    def test_split_twice_is_conflict(self) -> None:
        canonical, merged, _, _ = _pair()
        merge_row = merge.merge_matches(merged, canonical, actor="steward-1")
        merge.split_match(merge_row, actor="steward-1")
        with pytest.raises(Conflict, match="already split"):
            merge.split_match(merge_row, actor="steward-1")
        with pytest.raises(Conflict, match="already split"):
            merge.split_match(merge_row, actor="steward-1")


# --------------------------------------------------------------------------- #
# Concurrency + recompute idempotence + audit
# --------------------------------------------------------------------------- #


class TestConcurrencyAndAudit:
    @pytest.mark.django_db(transaction=True)
    def test_concurrent_merge_collapses_onto_one_row(self) -> None:
        from django.db import connections

        canonical, merged, _, _ = _pair()
        barrier = threading.Barrier(2)
        results: list[object] = []
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                barrier.wait(timeout=10)
                results.append(merge.merge_matches(merged, canonical, actor="steward-1"))
            except BaseException as exc:  # pragma: no cover - failure path
                errors.append(exc)
            finally:
                connections.close_all()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        connections.close_all()

        assert not errors, errors
        assert len(results) == 2
        assert MatchMerge.objects.count() == 1
        merged.refresh_from_db()
        assert merged.merged_into_id == canonical.pk

    def test_recompute_affected_series_is_idempotent(self) -> None:
        canonical, merged, india, _ = _pair()
        player = make_player("Player One")
        _make_stat(canonical, player, india)
        _make_stat(merged, player, india)

        merge.merge_matches(merged, canonical, actor="steward-1")  # runs recompute once
        first = PlayerCareerStat.objects.get(
            player=player, scope_type=StatScope.CAREER, format_type__isnull=True, gender="M"
        )
        first_digest = first.inputs_digest

        # Run again explicitly: same aggregate, same digest, no revision bump.
        affected = merge.recompute_affected_series(canonical, merged)
        assert isinstance(affected, list)
        second = PlayerCareerStat.objects.get(
            player=player, scope_type=StatScope.CAREER, format_type__isnull=True, gender="M"
        )
        assert second.inputs_digest == first_digest
        assert second.revision == first.revision

    def test_merge_and_split_write_audit_rows(self) -> None:
        canonical, merged, _, _ = _pair()
        merge_row = merge.merge_matches(merged, canonical, actor="steward-1")
        assert AuditLog.objects.filter(action=events.TOPIC_MATCH_MERGED).count() == 1
        merge.split_match(merge_row, actor="steward-1")
        assert AuditLog.objects.filter(action=events.TOPIC_MATCH_SPLIT).count() == 1
        merged_audit = AuditLog.objects.get(action=events.TOPIC_MATCH_SPLIT)
        assert merged_audit.after["merged_match_id"] == str(merged.id)

    def test_abandoned_terminal_merge_keeps_no_result_row(self) -> None:
        canonical, merged, _india, _australia = _pair(status=MatchStatus.ABANDONED)
        # A NO_RESULT row on the merged match moves like any 1:1 result.
        MatchResult.objects.create(match=merged, result_type="NO_RESULT", winner_team=None)
        merge.merge_matches(merged, canonical, actor="steward-1")
        result = MatchResult.objects.get(match_id=canonical.id)
        assert result.result_type == "NO_RESULT"
        assert not MatchResult.objects.filter(match_id=merged.id).exists()
