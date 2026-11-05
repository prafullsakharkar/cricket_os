"""Phase F Group 2: Tier-2 recompute pipeline + sweep.

Covers the brief's pipeline requirements: dependency sets, recompute-from-source
(not delta), idempotence, order independence, add-then-remove digest recovery,
correction recomputes union(previous,current) keys, crash-then-sweep healing,
merge integration (source re-pointed, loser rows removed, winner recomputed),
backfill digest == incremental digest, advisory-lock serialization, and
``stats.updated``/``standings.updated`` publishing. The oracle (plain Python
over raw fixture payloads) must agree with the pipeline's career totals.
"""

from __future__ import annotations

import dataclasses

import pytest
from django.db import connection, connections, transaction
from django.utils import timezone

from apps.core.enums import MatchStatus, StatScope
from apps.matches.models import Match, MatchResult
from apps.players.models import Player
from apps.scoring.config import preset_for_format
from apps.scoring.types import BattingLine, BowlingLine, Format
from apps.statistics.models import (
    HeadToHead,
    PlayerCareerStat,
    PlayerMatchStat,
    StatisticsWatermark,
    TeamStat,
)
from apps.statistics.services import recompute

from ..phase_c.factories import (
    make_match,
    make_match_teams,
    make_player,
    make_team,
)

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #


def _line_dict(line: BattingLine | BowlingLine) -> dict:
    """Serialise a line the same way ``stats._line_dict`` does (test-local)."""
    data = dataclasses.asdict(line)
    for key, value in data.items():
        if hasattr(value, "value"):  # StrEnum
            data[key] = value.value
    return data


def make_completed_match(
    *, format_type: str = "T20I", gender: str = "M", **overrides: object
) -> tuple[Match, object, object]:
    match = make_match(
        format_type=format_type,
        gender=gender,
        status=MatchStatus.COMPLETED,
        end_time_utc=timezone.now(),
        **overrides,
    )
    team1 = make_team("India")
    team2 = make_team("Australia")
    make_match_teams(match, team1, team2)
    MatchResult.objects.create(match=match, result_type="WIN", winner_team=team1)
    return match, team1, team2


def make_stat(
    match: Match,
    player: Player,
    team: object,
    *,
    runs: int = 0,
    balls: int = 0,
    out: bool = False,
    batting_lines: list[tuple[int, int, bool]] | None = None,
    wickets: int = 0,
    balls_bowled: int = 0,
    runs_conceded: int = 0,
    is_partial: bool = False,
    did_bat: bool | None = None,
) -> PlayerMatchStat:
    cfg = preset_for_format(Format(match.format_type))
    batting: list[dict] = []
    bowling: list[dict] = []
    if batting_lines:
        batting = [
            _line_dict(
                BattingLine(
                    rule_version=cfg.version,
                    player_id=str(player.id),
                    did_bat=True,
                    runs=r,
                    balls=b,
                    out=o,
                )
            )
            for r, b, o in batting_lines
        ]
    elif runs or balls or out:
        batting.append(
            _line_dict(
                BattingLine(
                    rule_version=cfg.version,
                    player_id=str(player.id),
                    did_bat=True,
                    runs=runs,
                    balls=balls,
                    out=out,
                )
            )
        )
    if wickets or balls_bowled:
        bowling.append(
            _line_dict(
                BowlingLine(
                    rule_version=cfg.version,
                    player_id=str(player.id),
                    legal_balls=balls_bowled,
                    runs_conceded=runs_conceded,
                    wickets=wickets,
                )
            )
        )
    return PlayerMatchStat.objects.create(
        match=match,
        player=player,
        team=team,
        did_bat=bool(batting) if did_bat is None else did_bat,
        did_bowl=bool(bowling),
        batting=batting,
        bowling=bowling,
        is_partial=is_partial,
    )


def _oracle_career(rows: list[PlayerMatchStat]) -> dict[str, object]:
    """Deliberately simple, obviously-correct reference implementation."""
    runs = innings = not_outs = balls_faced = 0
    high_score: int | None = None
    high_not_out = False
    fifties = hundreds = 0
    for row in rows:
        for bat in row.batting:
            if not bat.get("did_bat"):
                continue
            runs += bat["runs"]
            balls_faced += bat["balls"]
            innings += 1
            if not bat.get("out"):
                not_outs += 1
            if (
                high_score is None
                or bat["runs"] > high_score
                or (bat["runs"] == high_score and not bat.get("out") and not high_not_out)
            ):
                high_score = bat["runs"]
                high_not_out = not bat.get("out")
            if 50 <= bat["runs"] < 100:
                fifties += 1
            if bat["runs"] >= 100:
                hundreds += 1
    dismissals = innings - not_outs
    batting_avg = (runs / dismissals) if dismissals else None

    balls_bowled = runs_conc = wkts = 0
    for row in rows:
        for bowl in row.bowling:
            balls_bowled += bowl["legal_balls"]
            runs_conc += bowl["runs_conceded"]
            wkts += bowl["wickets"]
    economy = (runs_conc / (balls_bowled / 6)) if balls_bowled else None
    bowling_avg = (runs_conc / wkts) if wkts else None
    return {
        "matches": len({str(row.match_id) for row in rows}),
        "innings": innings,
        "not_outs": not_outs,
        "runs": runs,
        "balls_faced": balls_faced,
        "high_score": high_score,
        "high_score_not_out": high_not_out,
        "fifties": fifties,
        "hundreds": hundreds,
        "batting_avg": batting_avg,
        "balls_bowled": balls_bowled,
        "runs_conceded": runs_conc,
        "wickets": wkts,
        "economy": economy,
        "bowling_avg": bowling_avg,
    }


# --------------------------------------------------------------------------- #
# Dependency set
# --------------------------------------------------------------------------- #


def test_dependency_keys_for_match_cover_every_scope() -> None:
    from apps.competitions.models import MatchStage, Season, Series

    season = Season.objects.create(label="2026")
    series = Series.objects.create(name="Tri-Series 2026", is_tournament=True)
    stage = MatchStage.objects.create(
        series=series, name="Group A", stage_type="league", group_name="A"
    )
    match, team1, team2 = make_completed_match(season=season, series=series, stage=stage)
    player = make_player("Virat Kohli")
    make_stat(match, player, team1, runs=45, balls=30, out=True)

    keys = recompute.dependency_keys_for_match(match)

    gender = match.gender
    fmt = match.format_type
    expected_career = {
        f"career:{player.id}:{StatScope.CAREER}:-:-:{gender}",
        f"career:{player.id}:{StatScope.FORMAT}:-:{fmt}:{gender}",
        f"career:{player.id}:{StatScope.SEASON}:{season.id}:-:{gender}",
        f"career:{player.id}:{StatScope.SERIES}:{series.id}:-:{gender}",
        f"career:{player.id}:{StatScope.TEAM}:{team1.id}:-:{gender}",
        f"career:{player.id}:{StatScope.OPPOSITION}:{team2.id}:-:{gender}",
    }
    assert expected_career <= keys
    # Team stats: both teams, career + format (+ season/series/tournament/venue).
    assert f"team:{team1.id}:{StatScope.CAREER}:-:-" in keys
    assert f"team:{team2.id}:{StatScope.CAREER}:-:-" in keys
    assert f"team:{team1.id}:{StatScope.FORMAT}:-:{fmt}" in keys
    assert f"team:{team1.id}:{StatScope.SERIES}:{series.id}:-" in keys
    assert f"team:{team1.id}:{StatScope.TOURNAMENT}:{series.id}:-" in keys
    # H2H pair (canonical order low < high) + standings.
    low, high = sorted([str(team1.id), str(team2.id)])
    assert f"h2h:{low}:{high}:{fmt}" in keys
    assert f"h2h:{low}:{high}:-" in keys
    assert f"standings:{series.id}:{stage.id}" in keys


def test_dependency_keys_include_venue_scope() -> None:
    from apps.venues.models import Venue

    venue = Venue.objects.create(name="MCG")
    match, team1, _team2 = make_completed_match(venue=venue)
    player = make_player("Virat Kohli")
    make_stat(match, player, team1, runs=10)
    keys = recompute.dependency_keys_for_match(match)
    assert f"career:{player.id}:{StatScope.VENUE}:{venue.id}:-:{match.gender}" in keys
    assert f"team:{team1.id}:{StatScope.VENUE}:{venue.id}:-" in keys


# --------------------------------------------------------------------------- #
# Recompute-from-source + idempotence
# --------------------------------------------------------------------------- #


def test_recompute_career_stat_matches_independent_oracle() -> None:
    match, team1, _team2 = make_completed_match()
    p = make_player("Oracle Player")
    # Three batting lines in one match (Test-style two-innings shape).
    make_stat(match, p, team1, batting_lines=[(45, 30, True), (20, 10, False), (100, 50, False)])
    # A second match (different date) so `matches` is > 1.
    match2, team1b, _team2b = make_completed_match(format_type="ODI")
    make_stat(
        match2, p, team1b, runs=12, balls=15, out=True, wickets=3, balls_bowled=18, runs_conceded=30
    )

    for m in (match, match2):
        recompute.recompute_match(m)

    row = PlayerCareerStat.objects.get(player_id=p.id, scope_type=StatScope.CAREER)
    oracle = _oracle_career(list(PlayerMatchStat.objects.filter(player_id=p.id)))
    assert row.matches == oracle["matches"] == 2
    assert row.runs == oracle["runs"]
    assert row.innings == oracle["innings"]
    assert row.not_outs == oracle["not_outs"]
    assert row.high_score == oracle["high_score"]
    assert row.high_score_not_out is oracle["high_score_not_out"]
    assert row.fifties == oracle["fifties"]
    assert row.hundreds == oracle["hundreds"]
    assert float(row.batting_avg) == pytest.approx(oracle["batting_avg"])
    assert float(row.economy) == pytest.approx(oracle["economy"])
    assert float(row.bowling_avg) == pytest.approx(oracle["bowling_avg"])
    assert row.wickets == oracle["wickets"] == 3
    # Coverage labels.
    assert row.matches_counted == 2
    assert row.source_kind == "computed"
    assert row.rule_version  # set
    assert row.inputs_digest
    assert row.first_match_date is not None
    assert row.last_match_date is not None
    assert row.is_partial is False


def test_recompute_is_idempotent_digest_stable_revision_untouched() -> None:
    match, team1, _team2 = make_completed_match()
    p = make_player("Idem")
    make_stat(
        match, p, team1, runs=33, balls=20, out=True, wickets=1, balls_bowled=12, runs_conceded=9
    )

    recompute.recompute_match(match)
    row1 = PlayerCareerStat.objects.get(player_id=p.id, scope_type=StatScope.CAREER)
    digest1 = row1.inputs_digest
    revision1 = row1.revision

    recompute.recompute_match(match)
    row2 = PlayerCareerStat.objects.get(player_id=p.id, scope_type=StatScope.CAREER)
    assert row2.inputs_digest == digest1
    assert row2.revision == revision1
    assert row2.computed_at == row1.computed_at  # untouched


def test_recompute_order_independence() -> None:
    match, team1, _team2 = make_completed_match()
    p = make_player("Order")
    make_stat(match, p, team1, runs=40, balls=25, out=True)

    keys = recompute.dependency_keys_for_match(match)
    recompute.recompute_keys(sorted(keys))
    forward = {
        (r.scope_type, r.scope_ref_id, r.format_type): r.inputs_digest
        for r in PlayerCareerStat.objects.filter(player_id=p.id)
    }

    PlayerCareerStat.objects.filter(player_id=p.id).delete()
    recompute.recompute_keys(sorted(keys, reverse=True))
    backward = {
        (r.scope_type, r.scope_ref_id, r.format_type): r.inputs_digest
        for r in PlayerCareerStat.objects.filter(player_id=p.id)
    }
    assert forward == backward


def test_add_then_remove_match_returns_previous_digest() -> None:
    match1, t1a, _t2a = make_completed_match()
    match2, t1b, _t2b = make_completed_match(format_type="ODI")
    p = make_player("AddRemove")
    make_stat(match1, p, t1a, runs=50, balls=30, out=True)

    # CAREER scope is global, so the digest reflects every source row present
    # at recompute time: recompute match1 BEFORE match2's row exists.
    recompute.recompute_match(match1)
    digest_one = PlayerCareerStat.objects.get(
        player_id=p.id, scope_type=StatScope.CAREER
    ).inputs_digest

    make_stat(match2, p, t1b, runs=10, balls=8, out=False)
    recompute.recompute_match(match2)
    digest_two = PlayerCareerStat.objects.get(
        player_id=p.id, scope_type=StatScope.CAREER
    ).inputs_digest
    assert digest_two != digest_one

    # Remove match2 (as a correction that drops it from scope): recompute match1
    # again and the digest must return to the one-row state.
    PlayerMatchStat.objects.filter(match_id=match2.id).delete()
    recompute.recompute_match(match1)
    digest_again = PlayerCareerStat.objects.get(
        player_id=p.id, scope_type=StatScope.CAREER
    ).inputs_digest
    assert digest_again == digest_one


def test_recompute_team_stat_and_h2h() -> None:
    match, team1, team2 = make_completed_match()
    from apps.innings.models import Innings, InningsSummary

    inn1 = Innings.objects.create(
        match=match,
        innings_number=1,
        batting_team=team1,
        bowling_team=team2,
        all_out=False,
        status="COMPLETED",
    )
    inn2 = Innings.objects.create(
        match=match,
        innings_number=2,
        batting_team=team2,
        bowling_team=team1,
        all_out=True,
        status="COMPLETED",
    )
    InningsSummary.objects.create(innings=inn1, runs=160, wickets=6, legal_balls=120)
    InningsSummary.objects.create(innings=inn2, runs=120, wickets=10, legal_balls=110)

    recompute.recompute_match(match)

    t1 = TeamStat.objects.get(team_id=team1.id, scope_type=StatScope.CAREER)
    t2 = TeamStat.objects.get(team_id=team2.id, scope_type=StatScope.CAREER)
    assert t1.matches == 1
    assert t1.runs_scored == 160
    assert t1.runs_conceded == 120
    assert t1.won == 1
    assert t2.lost == 1

    low, high = sorted([str(team1.id), str(team2.id)])
    h2h = HeadToHead.objects.get(team_low_id=low, team_high_id=high, format_type=None)
    assert h2h.matches == 1
    winner = str(team1.id)
    if winner == low:
        assert h2h.low_wins == 1
        assert h2h.high_wins == 0
    else:
        assert h2h.high_wins == 1
        assert h2h.low_wins == 0
    assert h2h.last_match_id == match.id


# --------------------------------------------------------------------------- #
# Watermark + correction union + sweep
# --------------------------------------------------------------------------- #


def test_watermark_records_keys_and_digest() -> None:
    match, team1, _team2 = make_completed_match()
    p = make_player("WM")
    make_stat(match, p, team1, runs=10)
    recompute.recompute_match(match)
    wm = StatisticsWatermark.objects.get(match_id=match.id)
    assert wm.status == "done"
    assert set(wm.aggregate_keys) == recompute.dependency_keys_for_match(match)
    assert wm.source_digest


def test_correction_recomputes_union_of_previous_and_current_keys() -> None:
    match, team1, team2 = make_completed_match()
    p = make_player("Corr")
    make_stat(match, p, team1, runs=22)
    recompute.recompute_match(match)
    before = StatisticsWatermark.objects.get(match_id=match.id)
    prev_keys = set(before.aggregate_keys)

    # The correction changes the participant set: player is re-attributed to
    # team2 (so OPPOSITION flips). The affected set must be the union of the
    # previous and current dependency sets.
    PlayerMatchStat.objects.filter(player_id=p.id).update(team_id=team2)
    affected = recompute.recompute_match(match, correction=True)
    wm = StatisticsWatermark.objects.get(match_id=match.id)
    current_keys = set(wm.aggregate_keys)
    assert prev_keys != current_keys
    assert set(affected) == current_keys | prev_keys
    # The old TEAM key must have been recomputed and its row deleted (no rows
    # now in scope for team1), while the new TEAM key exists.
    assert not PlayerCareerStat.objects.filter(
        player_id=p.id, scope_type=StatScope.TEAM, scope_ref_id=team1.id
    ).exists()
    assert PlayerCareerStat.objects.filter(
        player_id=p.id, scope_type=StatScope.TEAM, scope_ref_id=team2.id
    ).exists()


def test_sweep_heals_crash_between_commit_and_recompute() -> None:
    match, team1, _team2 = make_completed_match()
    p = make_player("Crash")
    make_stat(match, p, team1, runs=77)
    # The worker died after commit of match.completed and before the recompute:
    # no watermark, no aggregate rows.
    assert not StatisticsWatermark.objects.filter(match_id=match.id).exists()
    assert not PlayerCareerStat.objects.filter(player_id=p.id).exists()

    healed = recompute.sweep_stale_statistics()
    assert healed >= 1
    assert PlayerCareerStat.objects.filter(player_id=p.id, scope_type=StatScope.CAREER).exists()
    wm = StatisticsWatermark.objects.get(match_id=match.id)
    assert wm.status == "done"

    # Idempotent: a second sweep heals nothing new.
    assert recompute.sweep_stale_statistics() == 0


def test_sweep_recomputes_when_source_changed_behind_its_back() -> None:
    match, team1, _team2 = make_completed_match()
    p = make_player("Stale")
    make_stat(match, p, team1, runs=30)
    recompute.recompute_match(match)

    # Someone rewrote source rows without the pipeline seeing an event.
    PlayerMatchStat.objects.filter(match_id=match.id).update(
        batting=[
            _line_dict(
                BattingLine(
                    rule_version="x", player_id=str(p.id), did_bat=True, runs=99, balls=40, out=True
                )
            )
        ]
    )
    assert recompute.sweep_stale_statistics() >= 1
    row = PlayerCareerStat.objects.get(player_id=p.id, scope_type=StatScope.CAREER)
    assert row.runs == 99


# --------------------------------------------------------------------------- #
# Merge integration
# --------------------------------------------------------------------------- #


def test_merge_repoints_source_deletes_loser_rows_recomputes_winner() -> None:
    from apps.players.services.merge import merge_players

    match, team1, _team2 = make_completed_match()
    winner = make_player("Virat Kohli")
    loser = make_player("V Kohli")
    make_stat(match, winner, team1, runs=60, balls=40, out=True)
    make_stat(match, loser, team1, runs=25, balls=20, out=False)

    recompute.recompute_match(match)
    winner_row_before = PlayerCareerStat.objects.get(
        player_id=winner.id, scope_type=StatScope.CAREER
    )
    assert winner_row_before.runs == 60
    assert PlayerCareerStat.objects.filter(player_id=loser.id).exists()

    # The source table is registered for re-pointing; the aggregate subscriber
    # deletes loser rows and recomputes the winner from source.
    recompute.register()  # idempotent; ensures the player.merged subscriber
    merge_players(winner, loser, actor="steward", reason="duplicate")

    assert not PlayerCareerStat.objects.filter(player_id=loser.id).exists()
    winner_row = PlayerCareerStat.objects.get(player_id=winner.id, scope_type=StatScope.CAREER)
    assert winner_row.runs == 85
    # player_match_stat rows re-pointed to the winner.
    assert not PlayerMatchStat.objects.filter(player_id=loser.id).exists()
    # A rebuild reproduces identical digests.
    PlayerCareerStat.objects.filter(player_id=winner.id).delete()
    for m in (match,):
        recompute.recompute_match(m)
    rebuilt = PlayerCareerStat.objects.get(player_id=winner.id, scope_type=StatScope.CAREER)
    assert rebuilt.inputs_digest == winner_row.inputs_digest
    assert rebuilt.runs == 85


# --------------------------------------------------------------------------- #
# Backfill command digest parity
# --------------------------------------------------------------------------- #


def test_full_rebuild_digest_equals_incremental_digest() -> None:
    match1, t1a, _t2a = make_completed_match()
    match2, t1b, _t2b = make_completed_match(format_type="ODI")
    p1 = make_player("P1")
    p2 = make_player("P2")
    make_stat(match1, p1, t1a, runs=40, balls=30, out=True)
    make_stat(match1, p2, t1a, runs=5, balls=6, out=False)
    make_stat(
        match2, p2, t1b, runs=70, balls=45, out=True, wickets=2, balls_bowled=24, runs_conceded=40
    )

    # Incremental: recompute per match.
    recompute.recompute_match(match1)
    recompute.recompute_match(match2)
    incremental = {
        (r.player_id, r.scope_type, r.scope_ref_id, r.format_type): r.inputs_digest
        for r in PlayerCareerStat.objects.all()
    }
    incremental_team = {
        (r.team_id, r.scope_type, r.scope_ref_id, r.format_type): r.inputs_digest
        for r in TeamStat.objects.all()
    }

    # Wipe and full rebuild.
    PlayerCareerStat.objects.all().delete()
    TeamStat.objects.all().delete()
    HeadToHead.objects.all().delete()
    StatisticsWatermark.objects.all().delete()
    recompute.rebuild_statistics(scope="all")

    rebuilt = {
        (r.player_id, r.scope_type, r.scope_ref_id, r.format_type): r.inputs_digest
        for r in PlayerCareerStat.objects.all()
    }
    rebuilt_team = {
        (r.team_id, r.scope_type, r.scope_ref_id, r.format_type): r.inputs_digest
        for r in TeamStat.objects.all()
    }
    assert rebuilt == incremental
    assert rebuilt_team == incremental_team


def test_backfill_dry_run_writes_nothing() -> None:
    match, team1, _team2 = make_completed_match()
    p = make_player("Dry")
    make_stat(match, p, team1, runs=12)
    recompute.rebuild_statistics(scope="all", dry_run=True)
    assert PlayerCareerStat.objects.count() == 0
    assert TeamStat.objects.count() == 0
    assert StatisticsWatermark.objects.count() == 0


# --------------------------------------------------------------------------- #
# Advisory-lock serialization + topic publishing
# --------------------------------------------------------------------------- #


def test_advisory_lock_serializes_same_key() -> None:
    key = "career:11111111-1111-4111-8111-111111111111:CAREER:-:-:M"
    lock1, lock2 = recompute._lock_pair(key)
    # Session-level advisory locks stack within one session, so the second
    # acquisition must come from a genuinely separate connection.
    other = connections.create_connection("default")
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s::int, %s::int)", [lock1, lock2])
            assert cursor.fetchone()[0] is True
            try:
                other.ensure_connection()
                with other.cursor() as cursor2:
                    cursor2.execute("SELECT pg_try_advisory_lock(%s::int, %s::int)", [lock1, lock2])
                    assert cursor2.fetchone()[0] is False
            finally:
                cursor.execute("SELECT pg_advisory_unlock(%s::int, %s::int)", [lock1, lock2])
    finally:
        other.close()


@pytest.mark.django_db(transaction=True)
def test_stats_and_standings_updated_published_after_recompute() -> None:
    from apps.core import events as core_events

    published: list[tuple[str, object]] = []
    core_events.subscribe(
        core_events.TOPIC_STATS_UPDATED,
        lambda p: published.append((core_events.TOPIC_STATS_UPDATED, p)),
    )
    core_events.subscribe(
        core_events.TOPIC_STANDINGS_UPDATED,
        lambda p: published.append((core_events.TOPIC_STANDINGS_UPDATED, p)),
    )
    from apps.competitions.models import Series

    series = Series.objects.create(name="S1", is_tournament=True)
    match, team1, _team2 = make_completed_match(series=series)
    p = make_player("Pub")
    make_stat(match, p, team1, runs=8)

    with transaction.atomic():
        recompute.recompute_match(match)

    topics = {topic for topic, _ in published}
    assert core_events.TOPIC_STATS_UPDATED in topics
    assert core_events.TOPIC_STANDINGS_UPDATED in topics
    stats_payloads = [q for t, q in published if t == core_events.TOPIC_STATS_UPDATED]
    assert any(pid == str(p.id) for pid in (q.get("player_id") for q in stats_payloads))


def test_events_module_exposes_phase_f_topics() -> None:
    from apps.core import events as core_events

    assert core_events.TOPIC_STATS_UPDATED == "stats.updated"
    assert core_events.TOPIC_STANDINGS_UPDATED == "standings.updated"
    assert core_events.TOPIC_RANKING_UPDATED == "ranking.updated"


def test_invalidation_registry_has_phase_f_invalidators() -> None:
    from apps.core.cache import invalidation

    ns = invalidation.namespaces_for(
        "stats.updated", {"player_id": "11111111-1111-4111-8111-111111111111"}
    )
    assert "statistics:player:11111111-1111-4111-8111-111111111111" in ns
    ns2 = invalidation.namespaces_for("standings.updated", {"series_id": "s1"})
    assert "standings:s1" in ns2
