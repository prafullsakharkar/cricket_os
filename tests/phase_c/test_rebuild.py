"""Engine-driven rebuild pipeline (scorecards.services.rebuild).

Covers the SCORING_ENGINE_API §7 mapping end-to-end:

* current deliveries -> DeliveryRecord, engine outputs -> derived rows;
* determinism digest: unchanged data rebuilds to the same digest (no revision
  bump, no events); changed ball content always changes the digest;
* rebuild_match derives match_result + match status and fires match.completed;
* Tier-1 rebuilds never touch player_match_stat; Tier-2 stats build exactly
  once from match.completed and are idempotent on re-run;
* summary-only innings are skipped (batch_complete=False).
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from apps.core import events as core_events
from apps.core.enums import DismissalType
from apps.deliveries.models import Over
from apps.deliveries.services.validation import DeliveryDraft
from apps.deliveries.services.write import append_or_revise
from apps.innings.models import Innings, InningsSummary
from apps.matches.models import MatchResult
from apps.scorecards.models import BattingEntry, BowlingEntry, FallOfWicket, Partnership
from apps.scorecards.services.rebuild import rebuild_innings, rebuild_match
from apps.statistics.models import PlayerMatchStat

from .factories import (
    make_innings,
    make_match,
    make_match_teams,
    make_player,
    make_provider,
    make_team,
)

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def make_context(innings_status: str = "IN_PROGRESS") -> dict[str, object]:
    """Match + two teams + two innings + the players that appear on the field."""
    match = make_match()
    t1 = make_team("India")
    t2 = make_team("Australia")
    make_match_teams(match, t1, t2)
    i1 = make_innings(match, t1, t2, innings_number=1, status=innings_status)
    i2 = make_innings(match, t2, t1, innings_number=2, status=innings_status)
    bowler = make_player("Jasprit Bumrah")
    batter = make_player("Virat Kohli")
    non_striker = make_player("Rohit Sharma")
    return {
        "match": match,
        "t1": t1,
        "t2": t2,
        "i1": i1,
        "i2": i2,
        "bowler": bowler,
        "batter": batter,
        "non_striker": non_striker,
    }


def ball(
    ctx: dict[str, object],
    seq: int,
    over: int,
    ball_in_over: int,
    runs: int = 0,
    *,
    wides: int = 0,
    noballs: int = 0,
    wicket: str | None = None,
) -> DeliveryDraft:
    kw: dict[str, object] = {
        "delivery_seq": seq,
        "over_number": over,
        "ball_in_over": ball_in_over,
        "runs_off_bat": runs,
        "wides": wides,
        "noballs": noballs,
    }
    if wicket:
        kw.update(
            is_wicket=True,
            dismissal_type=wicket,
            dismissed_player_id=ctx["batter"].id,  # type: ignore[attr-defined]
            # BOWLED is a bowler-credited dismissal; adapters set this flag.
            bowler_credited_wicket=True,
        )
    base = DeliveryDraft(
        delivery_seq=seq,
        over_number=over,
        ball_in_over=ball_in_over,
        bowler_id=ctx["bowler"].id,  # type: ignore[attr-defined]
        batter_id=ctx["batter"].id,  # type: ignore[attr-defined]
        non_striker_id=ctx["non_striker"].id,  # type: ignore[attr-defined]
    )
    return replace(base, **kw)  # type: ignore[arg-type]


def load_innings(i1: Innings, ctx: dict[str, object], specs: list[tuple[int, int, int]]) -> None:
    """Load a sequence of (over, ball, runs) deliveries into ``i1``."""
    drafts = [
        ball(ctx, seq, over, ball_in_over, runs)
        for seq, (over, ball_in_over, runs) in enumerate(specs, start=1)
    ]
    append_or_revise(i1, drafts, source=make_provider(), suppress_events=True)


def register_participants(ctx: dict[str, object]) -> None:
    from apps.matches.models import MatchPlayer

    match = ctx["match"]
    MatchPlayer.objects.create(
        match=match, player=ctx["batter"], team=ctx["t1"], is_playing_xi=True
    )
    MatchPlayer.objects.create(
        match=match, player=ctx["bowler"], team=ctx["t2"], is_playing_xi=True
    )
    MatchPlayer.objects.create(
        match=match, player=ctx["non_striker"], team=ctx["t1"], is_playing_xi=True
    )


# --------------------------------------------------------------------------- #
# Single-innings rebuild
# --------------------------------------------------------------------------- #


class TestRebuildInnings:
    def test_full_derived_rows_written(self) -> None:
        ctx = make_context()
        load_innings(
            ctx["i1"], ctx, [(1, 1, 1), (1, 2, 1), (1, 3, 1), (1, 4, 1), (1, 5, 1), (1, 6, 1)]
        )
        report = rebuild_innings(ctx["i1"])

        assert report.changed is True
        assert report.innings_id == str(ctx["i1"].id)

        summary = InningsSummary.objects.get(innings=ctx["i1"])
        assert (summary.runs, summary.wickets, summary.legal_balls) == (6, 0, 6)
        assert summary.overs_display == "1.0"
        assert summary.batch_complete is True

        batting = BattingEntry.objects.get(innings=ctx["i1"], player=ctx["batter"])
        assert (batting.runs, batting.balls, batting.is_out) == (6, 6, False)

        (bowling,) = BowlingEntry.objects.filter(innings=ctx["i1"])
        assert bowling.player_id == ctx["bowler"].id
        assert (bowling.legal_balls, bowling.runs_conceded, bowling.wickets) == (6, 6, 0)
        assert bowling.overs_display == "1.0"

        (over,) = Over.objects.filter(innings=ctx["i1"])
        assert (over.over_number, over.is_complete, over.deliveries_count, over.runs_conceded) == (
            1,
            True,
            6,
            6,
        )

        (partnership,) = Partnership.objects.filter(innings=ctx["i1"])
        assert partnership.wicket_number == 0
        assert partnership.unbroken is True
        assert (partnership.runs, partnership.balls) == (6, 6)
        assert partnership.batter1_id == ctx["batter"].id
        assert partnership.batter2_id == ctx["non_striker"].id

        assert FallOfWicket.objects.filter(innings=ctx["i1"]).count() == 0

        ctx["i1"].refresh_from_db()
        assert ctx["i1"].status == "IN_PROGRESS"

    def test_wicket_produces_fow_and_closed_partnership(self) -> None:
        ctx = make_context()
        drafts = [
            ball(ctx, 1, 1, 1, 0),
            ball(ctx, 2, 1, 2, 0),
            ball(ctx, 3, 1, 3, 0),
            ball(ctx, 4, 1, 4, 0),
            ball(ctx, 5, 1, 5, 0, wicket=DismissalType.BOWLED),
        ]
        append_or_revise(ctx["i1"], drafts, source=make_provider(), suppress_events=True)
        rebuild_innings(ctx["i1"])

        summary = InningsSummary.objects.get(innings=ctx["i1"])
        assert (summary.runs, summary.wickets) == (0, 1)

        (fow,) = FallOfWicket.objects.filter(innings=ctx["i1"])
        assert (fow.wicket_number, fow.score, fow.delivery_seq) == (1, 0, 5)
        assert fow.dismissed_player_id == ctx["batter"].id

        (partnership,) = Partnership.objects.filter(innings=ctx["i1"])
        assert partnership.unbroken is False
        assert partnership.runs == 0

        batting = BattingEntry.objects.get(innings=ctx["i1"], player=ctx["batter"])
        assert (batting.is_out, batting.dismissal_type, batting.balls) == (True, "BOWLED", 5)

        (bowling,) = BowlingEntry.objects.filter(innings=ctx["i1"])
        assert (bowling.wickets, bowling.runs_conceded) == (1, 0)

        ctx["i1"].refresh_from_db()
        assert ctx["i1"].status == "IN_PROGRESS"

    def test_rebuild_is_deterministic_and_noop_when_unchanged(self) -> None:
        ctx = make_context()
        load_innings(
            ctx["i1"], ctx, [(1, 1, 1), (1, 2, 1), (1, 3, 1), (1, 4, 1), (1, 5, 1), (1, 6, 1)]
        )
        first = rebuild_innings(ctx["i1"])
        second = rebuild_innings(ctx["i1"])

        assert first.changed is True
        assert second.changed is False
        assert second.digest == first.digest, "unchanged data must digest identically"

        ctx["i1"].refresh_from_db()
        first_rev = ctx["i1"].revision
        rebuild_innings(ctx["i1"])
        ctx["i1"].refresh_from_db()
        assert ctx["i1"].revision == first_rev, "no-op rebuild must not bump revision"

    def test_corrected_ball_changes_digest(self) -> None:
        ctx = make_context()
        load_innings(
            ctx["i1"], ctx, [(1, 1, 1), (1, 2, 1), (1, 3, 1), (1, 4, 1), (1, 5, 1), (1, 6, 1)]
        )
        first = rebuild_innings(ctx["i1"])

        # Ball 6 becomes a boundary: same revision, new content -> correction.
        corrected = ball(ctx, 6, 1, 6, 4)
        append_or_revise(ctx["i1"], [corrected], source=make_provider(), suppress_events=True)
        second = rebuild_innings(ctx["i1"])

        assert second.changed is True
        assert second.digest != first.digest, "changed content must change the digest"
        summary = InningsSummary.objects.get(innings=ctx["i1"])
        assert summary.runs == 9

    def test_overs_split_across_two_overs(self) -> None:
        ctx = make_context()
        load_innings(
            ctx["i1"],
            ctx,
            [
                (1, 1, 1),
                (1, 2, 1),
                (1, 3, 1),
                (1, 4, 1),
                (1, 5, 1),
                (1, 6, 1),
                (2, 1, 4),
                (2, 2, 4),
            ],
        )
        rebuild_innings(ctx["i1"])
        overs = list(Over.objects.filter(innings=ctx["i1"]).order_by("over_number"))
        assert [o.over_number for o in overs] == [1, 2]
        assert overs[0].is_complete is True
        assert overs[1].is_complete is False
        summary = InningsSummary.objects.get(innings=ctx["i1"])
        assert summary.overs_display == "1.2"


# --------------------------------------------------------------------------- #
# Match-level rebuild + result
# --------------------------------------------------------------------------- #


class TestRebuildMatch:
    def test_chase_derives_win_by_wickets(self) -> None:
        ctx = make_context()
        load_innings(
            ctx["i1"], ctx, [(1, 1, 1), (1, 2, 1), (1, 3, 1), (1, 4, 1), (1, 5, 1), (1, 6, 1)]
        )
        load_innings(
            ctx["i2"], ctx, [(1, 1, 4), (1, 2, 4), (1, 3, 4), (1, 4, 4), (1, 5, 1), (1, 6, 1)]
        )

        report = rebuild_match(ctx["match"])

        assert report.changed is True
        assert report.match_completed is True

        result = MatchResult.objects.get(match=ctx["match"])
        assert result.result_type == "WIN"
        assert result.winner_team_id == ctx["t2"].id
        assert result.margin_type == "wickets"
        assert result.margin_value == 10
        assert result.completed_at is not None

        ctx["match"].refresh_from_db()
        assert ctx["match"].status == "COMPLETED"
        assert ctx["match"].result_type == "WIN"
        assert ctx["match"].winner_team_id == ctx["t2"].id

    def test_match_rebuild_noop_on_rerun(self) -> None:
        ctx = make_context()
        load_innings(
            ctx["i1"], ctx, [(1, 1, 1), (1, 2, 1), (1, 3, 1), (1, 4, 1), (1, 5, 1), (1, 6, 1)]
        )
        load_innings(
            ctx["i2"], ctx, [(1, 1, 4), (1, 2, 4), (1, 3, 4), (1, 4, 4), (1, 5, 1), (1, 6, 1)]
        )
        first = rebuild_match(ctx["match"])
        second = rebuild_match(ctx["match"])

        assert second.changed is False
        assert second.digest == first.digest
        assert second.match_completed is False

        ctx["match"].refresh_from_db()
        first_rev = ctx["match"].revision
        rebuild_match(ctx["match"])
        ctx["match"].refresh_from_db()
        assert ctx["match"].revision == first_rev

    def test_tie_when_scores_level_and_innings_closed(self) -> None:
        ctx = make_context()
        # Innings 2 is all out at the same score -> the chase is closed and tied.
        Innings.objects.filter(id=ctx["i2"].id).update(all_out=True)
        load_innings(
            ctx["i1"], ctx, [(1, 1, 1), (1, 2, 1), (1, 3, 1), (1, 4, 1), (1, 5, 1), (1, 6, 1)]
        )
        load_innings(
            ctx["i2"], ctx, [(1, 1, 1), (1, 2, 1), (1, 3, 1), (1, 4, 1), (1, 5, 1), (1, 6, 1)]
        )
        rebuild_match(ctx["match"])
        result = MatchResult.objects.get(match=ctx["match"])
        assert result.result_type == "TIE"
        assert result.winner_team_id is None

    def test_tier1_rebuild_never_writes_stats(self) -> None:
        ctx = make_context()
        register_participants(ctx)
        load_innings(
            ctx["i1"], ctx, [(1, 1, 1), (1, 2, 1), (1, 3, 1), (1, 4, 1), (1, 5, 1), (1, 6, 1)]
        )
        load_innings(
            ctx["i2"], ctx, [(1, 1, 4), (1, 2, 4), (1, 3, 4), (1, 4, 4), (1, 5, 1), (1, 6, 1)]
        )
        rebuild_innings(ctx["i1"])
        rebuild_innings(ctx["i2"])
        rebuild_match(ctx["match"])
        assert PlayerMatchStat.objects.filter(match=ctx["match"]).count() == 0

    def test_all_innings_derived_rows_rebuilt(self) -> None:
        ctx = make_context()
        load_innings(
            ctx["i1"], ctx, [(1, 1, 1), (1, 2, 1), (1, 3, 1), (1, 4, 1), (1, 5, 1), (1, 6, 1)]
        )
        load_innings(
            ctx["i2"], ctx, [(1, 1, 4), (1, 2, 4), (1, 3, 4), (1, 4, 4), (1, 5, 1), (1, 6, 1)]
        )
        rebuild_match(ctx["match"])
        for innings in (ctx["i1"], ctx["i2"]):
            assert InningsSummary.objects.filter(innings=innings).exists()
            assert BattingEntry.objects.filter(innings=innings).exists()
            assert BowlingEntry.objects.filter(innings=innings).exists()
            assert Over.objects.filter(innings=innings).exists()


# --------------------------------------------------------------------------- #
# Summary-only innings are skipped
# --------------------------------------------------------------------------- #


class TestSummaryOnly:
    def test_rebuild_innings_skips_summary_only(self) -> None:
        ctx = make_context()
        InningsSummary.objects.create(innings=ctx["i1"], runs=150, wickets=5, batch_complete=False)
        # Provider-written derived rows must survive a rebuild call.
        BattingEntry.objects.create(innings=ctx["i1"], player=ctx["batter"], runs=55, balls=40)

        report = rebuild_innings(ctx["i1"])

        assert report.skipped_reason == "summary_only"
        assert report.changed is False
        (batting,) = BattingEntry.objects.filter(innings=ctx["i1"])
        assert batting.runs == 55

    def test_rebuild_match_skips_all_summary_only(self) -> None:
        ctx = make_context()
        InningsSummary.objects.create(innings=ctx["i1"], runs=150, wickets=5, batch_complete=False)
        InningsSummary.objects.create(innings=ctx["i2"], runs=120, wickets=8, batch_complete=False)
        report = rebuild_match(ctx["match"])
        assert report.skipped_reason == "summary_only"
        assert report.changed is False


# --------------------------------------------------------------------------- #
# Events + Tier-2 statistics (transaction=True so on_commit fires)
# --------------------------------------------------------------------------- #


@pytest.mark.django_db(transaction=True)
class TestEventsAndStats:
    @pytest.fixture(autouse=True)
    def _clear_events(self) -> None:
        core_events.clear_subscriptions()
        yield
        core_events.clear_subscriptions()

    def test_match_completed_event_fires_and_builds_stats_exactly_once(self) -> None:
        from apps.statistics.services.stats import _on_match_event

        ctx = make_context()
        register_participants(ctx)
        load_innings(
            ctx["i1"], ctx, [(1, 1, 1), (1, 2, 1), (1, 3, 1), (1, 4, 1), (1, 5, 1), (1, 6, 1)]
        )
        load_innings(
            ctx["i2"], ctx, [(1, 1, 4), (1, 2, 4), (1, 3, 4), (1, 4, 4), (1, 5, 1), (1, 6, 1)]
        )

        completed: list[dict[str, object]] = []
        core_events.subscribe(core_events.TOPIC_MATCH_COMPLETED, lambda p: completed.append(p))
        # Production wiring: the statistics module listens for completion.
        core_events.subscribe(core_events.TOPIC_MATCH_COMPLETED, _on_match_event)

        report = rebuild_match(ctx["match"])

        assert report.match_completed is True
        assert len(completed) == 1
        # Stats built once by the event handler.
        assert PlayerMatchStat.objects.filter(match=ctx["match"]).count() == 3

        # Idempotent: re-running the match rebuild publishes nothing and the
        # stats handler (already ran) leaves revisions untouched.
        second = rebuild_match(ctx["match"])
        assert second.changed is False
        revisions = set(
            PlayerMatchStat.objects.filter(match=ctx["match"]).values_list("revision", flat=True)
        )
        assert revisions == {1}

    def test_stats_build_is_idempotent_when_called_directly(self) -> None:
        from apps.statistics.services.stats import build_match_stats

        ctx = make_context()
        register_participants(ctx)
        load_innings(
            ctx["i1"], ctx, [(1, 1, 1), (1, 2, 1), (1, 3, 1), (1, 4, 1), (1, 5, 1), (1, 6, 1)]
        )
        load_innings(
            ctx["i2"], ctx, [(1, 1, 4), (1, 2, 4), (1, 3, 4), (1, 4, 4), (1, 5, 1), (1, 6, 1)]
        )
        rebuild_match(ctx["match"])

        first = build_match_stats(ctx["match"])
        second = build_match_stats(ctx["match"])

        assert first > 0
        assert second == 0, "identical stats must not be rewritten"

    def test_correction_to_completed_match_publishes_correction_applied(self) -> None:
        ctx = make_context()
        load_innings(
            ctx["i1"], ctx, [(1, 1, 1), (1, 2, 1), (1, 3, 1), (1, 4, 1), (1, 5, 1), (1, 6, 1)]
        )
        load_innings(
            ctx["i2"], ctx, [(1, 1, 4), (1, 2, 4), (1, 3, 4), (1, 4, 4), (1, 5, 1), (1, 6, 1)]
        )
        rebuild_match(ctx["match"])

        corrected: list[dict[str, object]] = []
        core_events.subscribe(core_events.TOPIC_CORRECTION_APPLIED, lambda p: corrected.append(p))

        changed = ball(ctx, 6, 1, 6, 4)  # last ball of innings 1: 1 run -> 4
        append_or_revise(ctx["i1"], [changed], source=make_provider(), suppress_events=True)
        report = rebuild_match(ctx["match"])

        assert report.changed is True
        assert len(corrected) == 1
        assert MatchResult.objects.get(match=ctx["match"]).result_type == "WIN"

    def test_scorecard_updated_published_only_on_change(self) -> None:
        ctx = make_context()
        load_innings(
            ctx["i1"], ctx, [(1, 1, 1), (1, 2, 1), (1, 3, 1), (1, 4, 1), (1, 5, 1), (1, 6, 1)]
        )
        updated: list[dict[str, object]] = []
        core_events.subscribe(core_events.TOPIC_SCORECARD_UPDATED, lambda p: updated.append(p))

        rebuild_innings(ctx["i1"])
        assert len(updated) == 1

        rebuild_innings(ctx["i1"])
        assert len(updated) == 1, "no-op rebuild must not re-publish"
