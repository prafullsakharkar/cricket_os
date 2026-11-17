"""Phase D group 3: live ``apply_batch``, shadow verify, result service."""

from __future__ import annotations

from dataclasses import replace

import pytest

from apps.core import metrics
from apps.core.enums import MatchStatus
from apps.deliveries.models import Delivery
from apps.deliveries.services.validation import DeliveryDraft
from apps.ingestion.models import IngestionError
from apps.innings.models import InningsSummary
from apps.live.services import verify as shadow
from apps.live.services.apply import LiveInningsBatch, LiveState, apply_batch
from apps.matches import event_schemas
from apps.matches.models import MatchEventLog, MatchResult
from apps.matches.services import result_service
from apps.scorecards.models import BattingEntry
from apps.statistics.models import PlayerMatchStat

from ..phase_c.factories import (
    make_match,
    make_match_teams,
    make_player,
    make_provider,
    make_team,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _reset_shadow() -> None:
    shadow.reset_counters()


def make_context(*, format_type: str = "T20I", overs_limit: int = 20) -> dict[str, object]:
    match = make_match(
        format_type=format_type,
        overs_limit=overs_limit,
        status=MatchStatus.IN_PROGRESS,
    )
    team1 = make_team("India")
    team2 = make_team("Australia")
    make_match_teams(match, team1, team2)
    batter = make_player("Virat Kohli")
    non_striker = make_player("Rohit Sharma")
    bowler = make_player("Mitchell Starc")
    return {
        "match": match,
        "team1": team1,
        "team2": team2,
        "batter": batter,
        "non_striker": non_striker,
        "bowler": bowler,
    }


def ball(ctx: dict[str, object], seq: int, runs: int = 0, **overrides: object) -> DeliveryDraft:
    base = DeliveryDraft(
        delivery_seq=seq,
        over_number=(seq - 1) // 6 + 1,
        ball_in_over=(seq - 1) % 6 + 1,
        bowler_id=ctx["bowler"].id,  # type: ignore[attr-defined]
        batter_id=ctx["batter"].id,  # type: ignore[attr-defined]
        non_striker_id=ctx["non_striker"].id,  # type: ignore[attr-defined]
        runs_off_bat=runs,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def batch(ctx: dict[str, object], seqs: list[int], runs: int = 1, innings: int = 1):
    return LiveInningsBatch(
        innings_number=innings,
        drafts=tuple(ball(ctx, seq, runs) for seq in seqs),
    )


def _event_types(match_id: object) -> list[str]:
    return list(
        MatchEventLog.objects.filter(match_id=match_id)
        .order_by("seq")
        .values_list("event_type", flat=True)
    )


def test_apply_batch_appends_rebuilds_and_logs(make_match) -> None:
    ctx = make_context()
    match = ctx["match"]
    report = apply_batch(match, [batch(ctx, [1, 2, 3], runs=4)], source=make_provider("replay"))
    assert (report.appended, report.corrected, report.noop) == (3, 0, 0)
    assert report.changed is True
    assert Delivery.objects.filter(match_id=match.id, is_current=True).count() == 3
    summary = InningsSummary.objects.get(innings_id=report.innings[0])
    assert summary.runs == 12
    match.refresh_from_db()
    assert match.last_ingested_at is not None
    types = _event_types(match.id)
    assert types[0] == event_schemas.DELIVERY_APPENDED
    assert types[-1] == event_schemas.INNINGS_UPDATED
    seqs = list(
        MatchEventLog.objects.filter(match_id=match.id)
        .order_by("seq")
        .values_list("seq", flat=True)
    )
    assert seqs == list(range(1, len(seqs) + 1))


def test_duplicate_batch_is_a_noop(make_match) -> None:
    ctx = make_context()
    match = ctx["match"]
    records = [batch(ctx, [1, 2, 3])]
    apply_batch(match, records)
    before_events = MatchEventLog.objects.filter(match_id=match.id).count()
    report = apply_batch(match, records)
    assert (report.appended, report.corrected, report.noop) == (0, 0, 3)
    assert report.changed is False
    assert Delivery.objects.filter(match_id=match.id, is_current=True).count() == 3
    assert MatchEventLog.objects.filter(match_id=match.id).count() == before_events


def test_out_of_order_batches_converge_to_the_same_digest() -> None:
    clean = make_context()
    shuffled = make_context()
    records = list(range(1, 13))
    clean_report = apply_batch(clean["match"], [batch(clean, records, runs=2)])
    # Apply in two reversed slices; append_or_revise sorts inside each batch
    # and the engine rebuild reads stored rows ordered by delivery_seq.
    apply_batch(shuffled["match"], [batch(shuffled, list(reversed(records[:6])), runs=2)])
    shuffled_report = apply_batch(
        shuffled["match"], [batch(shuffled, list(reversed(records[6:])), runs=2)]
    )
    clean_summary = InningsSummary.objects.get(innings_id=clean_report.innings[0])
    shuffled_summary = InningsSummary.objects.get(innings_id=shuffled_report.innings[0])
    assert (clean_summary.runs, clean_summary.legal_balls) == (
        shuffled_summary.runs,
        shuffled_summary.legal_balls,
    )
    assert clean_summary.runs == 24


def test_higher_revision_is_a_correction(make_match) -> None:
    ctx = make_context()
    match = ctx["match"]
    apply_batch(match, [batch(ctx, [1], runs=4)])
    report = apply_batch(
        match,
        [LiveInningsBatch(innings_number=1, drafts=(ball(ctx, 1, 6, revision=2),))],
    )
    assert report.corrected == 1
    assert event_schemas.DELIVERY_CORRECTED in _event_types(match.id)
    from apps.synchronization.models import DataCorrection

    assert DataCorrection.objects.filter(entity_type="delivery").count() == 1


def test_shadow_verify_detects_and_heals_corruption(make_match) -> None:
    ctx = make_context()
    match = ctx["match"]
    apply_batch(match, [batch(ctx, [1, 2, 3], runs=4)])
    entry = BattingEntry.objects.get(
        innings_id=match.innings.first().id, player_id=ctx["batter"].id
    )
    original = entry.runs
    BattingEntry.objects.filter(pk=entry.pk).update(runs=999)
    metrics.reset()
    ok = shadow.verify(match.innings.first())
    assert ok is False
    healed = BattingEntry.objects.get(innings_id=entry.innings_id, player_id=entry.player_id)
    assert healed.runs == original
    assert IngestionError.objects.filter(stage="shadow_verify").exists()


def test_live_deliveries_never_touch_tier2(make_match) -> None:
    ctx = make_context()
    before = list(PlayerMatchStat.objects.all().values_list("id", "revision"))
    apply_batch(ctx["match"], [batch(ctx, [1, 2, 3])])
    assert list(PlayerMatchStat.objects.all().values_list("id", "revision")) == before


def test_lifecycle_transition_is_carried_by_the_batch(make_match) -> None:
    ctx = make_context()
    match = ctx["match"]
    match.status = MatchStatus.SCHEDULED
    match.save(update_fields=["status"])
    report = apply_batch(
        match,
        [],
        state=LiveState(MatchStatus.TOSS, state_title="Toss", status_detail="AUS won the toss"),
    )
    assert report.state_changed is True
    match.refresh_from_db()
    assert match.status == MatchStatus.TOSS
    assert match.state_title == "Toss"
    assert _event_types(match.id) == [event_schemas.MATCH_STATE_CHANGED]


# --------------------------------------------------------------------------- #
# Result service
# --------------------------------------------------------------------------- #


def _finish_t20(ctx: dict[str, object]) -> None:
    match = ctx["match"]
    apply_batch(match, [batch(ctx, list(range(1, 7)), runs=6, innings=1)])
    # 60 runs in the first innings (6 balls x 6 = 36, 6 more = 36; use 10 balls)
    apply_batch(match, [batch(ctx, list(range(7, 11)), runs=6, innings=1)])
    apply_batch(match, [batch(ctx, list(range(1, 12)), runs=6, innings=2)])


def test_result_service_completes_once(make_match) -> None:
    ctx = make_context()
    match = ctx["match"]
    _finish_t20(ctx)
    first = result_service.finalise(match)
    assert first.completed_now is True
    assert first.result_type == "WIN"
    assert first.winner_team_id == str(ctx["team2"].id)
    assert MatchResult.objects.filter(match_id=match.id).count() == 1
    assert _event_types(match.id).count(event_schemas.MATCH_COMPLETED) == 1

    second = result_service.finalise(match)
    assert second.completed_now is False
    assert second.already_complete is True
    assert _event_types(match.id).count(event_schemas.MATCH_COMPLETED) == 1


def test_dls_target_is_never_computed(make_match) -> None:
    ctx = make_context()
    match = ctx["match"]
    match.is_dls_applied = True
    match.save(update_fields=["is_dls_applied"])
    apply_batch(match, [batch(ctx, [1, 2], innings=1)])
    apply_batch(match, [batch(ctx, [1, 2], innings=2)])
    from apps.scoring.errors import MissingDlsTargetError

    with pytest.raises(MissingDlsTargetError):
        result_service.finalise(match)


def test_supplied_dls_target_is_used(make_match) -> None:
    ctx = make_context()
    match = ctx["match"]
    match.is_dls_applied = True
    match.save(update_fields=["is_dls_applied"])
    apply_batch(match, [batch(ctx, [1, 2, 3], runs=4, innings=1)])
    apply_batch(
        match,
        [
            LiveInningsBatch(
                innings_number=2,
                drafts=(ball(ctx, 1, 6), ball(ctx, 2, 6)),
                dls_target=10,
            )
        ],
    )
    result = result_service.finalise(match)
    assert result.result_type == "WIN"
    assert result.is_dls is True


def test_innings_creation_infers_teams() -> None:
    ctx = make_context()
    report = apply_batch(ctx["match"], [batch(ctx, [1], innings=2)])
    innings = ctx["match"].innings.get(id=report.innings[0])
    # Innings 2: the second-listed team bats.
    assert innings.batting_team_id == ctx["team2"].id
    assert innings.bowling_team_id == ctx["team1"].id
