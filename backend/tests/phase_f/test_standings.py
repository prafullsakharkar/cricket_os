"""Phase F Group 3: tournament standings + NRR.

Covers the brief's standings requirements: per-series/stage recompute with the
series' ``points_system`` overriding the format default (``dataclasses.replace``
on ``RuleConfig``), the most-recent-first 5-window form string, steward-set
``qualify_status``/``no_of_qual`` passthrough across recomputes, the engine's
NRR edge cases (full quota when bowled out, no-result exclusion, ``None`` for a
side with no countable overs), ``group_name`` from the stage, and the
reconciliation hook (``standings:{series}:{stage|'-'}`` job + ``reconciliation_mismatch``
DLQ row) that never corrects silently. The NRR assertions use an independent
plain-Python oracle over the raw innings figures.
"""

from __future__ import annotations

import datetime
from datetime import UTC, date, time
from decimal import Decimal

import pytest

from apps.competitions.models import MatchStage, Series
from apps.ingestion.models import IngestionError
from apps.innings.models import Innings, InningsSummary
from apps.matches.models import Match, MatchResult
from apps.statistics.models import TournamentStandings
from apps.statistics.services import recompute
from apps.synchronization.models import ReconciliationJob

from ..phase_c.factories import make_match, make_match_teams, make_provider, make_team

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #


def make_series(*, points_system: dict | None = None, name: str = "World Cup") -> Series:
    return Series.objects.create(
        name=name,
        series_type="tournament",
        is_tournament=True,
        points_system=points_system,
    )


def make_series_result(
    series: Series,
    team1: object,
    team2: object,
    winner: object | None,
    result_type: str,
    *,
    start_date: date,
    stage: MatchStage | None = None,
    runs1: int = 0,
    wickets1: int = 0,
    balls1: int = 0,
    all_out1: bool = False,
    runs2: int = 0,
    wickets2: int = 0,
    balls2: int = 0,
    all_out2: bool = False,
    no_innings: bool = False,
) -> Match:
    match = make_match(
        start_date=start_date,
        format_type="T20I",
        status="COMPLETED",
        end_time_utc=datetime.datetime.combine(start_date, time(19, 0), tzinfo=UTC),
        series=series,
        stage=stage,
    )
    make_match_teams(match, team1, team2)
    MatchResult.objects.create(match=match, result_type=result_type, winner_team=winner)
    if not no_innings:
        inn1 = Innings.objects.create(
            match=match,
            innings_number=1,
            batting_team=team1,
            bowling_team=team2,
            all_out=all_out1,
            status="COMPLETED",
        )
        InningsSummary.objects.create(
            innings=inn1, runs=runs1, wickets=wickets1, legal_balls=balls1
        )
        inn2 = Innings.objects.create(
            match=match,
            innings_number=2,
            batting_team=team2,
            bowling_team=team1,
            all_out=all_out2,
            status="COMPLETED",
        )
        InningsSummary.objects.create(
            innings=inn2, runs=runs2, wickets=wickets2, legal_balls=balls2
        )
    return match


def recompute_standings(series: Series, stage: MatchStage | None = None) -> None:
    key = f"standings:{series.id}:{stage.id if stage else '-'}"
    recompute.recompute_keys([key])


def standings_rows(
    series: Series, stage: MatchStage | None = None
) -> dict[str, TournamentStandings]:
    rows = TournamentStandings.objects.filter(
        series_id=series.id, stage_id=stage.id if stage else None
    )
    return {str(row.team_id): row for row in rows}


def oracle_nrr(
    *,
    runs_for: int,
    balls_faced: int,
    all_out: bool,
    runs_against: int,
    balls_bowled: int,
    opponent_all_out: bool,
    overs_limit: int = 20,
    balls_per_over: int = 6,
) -> float | None:
    """Independent plain-Python NRR (E14 full-quota rule), no engine imports."""
    overs_faced = overs_limit if all_out else balls_faced / balls_per_over
    overs_bowled = overs_limit if opponent_all_out else balls_bowled / balls_per_over
    if overs_faced == 0 or overs_bowled == 0:
        return None
    return round(runs_for / overs_faced - runs_against / overs_bowled, 3)


# --------------------------------------------------------------------------- #
# Points
# --------------------------------------------------------------------------- #


def test_default_points_two_wins_and_two_losses() -> None:
    series = make_series()
    india, australia = make_team("India"), make_team("Australia")
    make_series_result(series, india, australia, india, "WIN", start_date=date(2026, 2, 1))
    make_series_result(series, india, australia, india, "WIN", start_date=date(2026, 2, 3))

    recompute_standings(series)
    rows = standings_rows(series)

    assert rows[str(india.id)].points == 4  # 2 + 2
    assert rows[str(india.id)].matches_played == 2
    assert rows[str(india.id)].matches_won == 2
    assert rows[str(australia.id)].points == 0
    assert rows[str(australia.id)].matches_lost == 2
    assert rows[str(india.id)].rank == 1
    assert rows[str(australia.id)].rank == 2


def test_points_system_overrides_format_defaults() -> None:
    series = make_series(points_system={"points_win": 4, "points_no_result": 0, "no_of_qual": 2})
    india, australia = make_team("India"), make_team("Australia")
    make_series_result(series, india, australia, india, "WIN", start_date=date(2026, 2, 1))
    make_series_result(series, india, australia, None, "NO_RESULT", start_date=date(2026, 2, 3))

    recompute_standings(series)
    rows = standings_rows(series)

    # Custom win value (4, not 2); no-result worth 0 (not the default 1).
    assert rows[str(india.id)].points == 4
    assert rows[str(india.id)].no_result == 1
    assert rows[str(australia.id)].points == 0


def test_points_digest_tracks_points_system_changes() -> None:
    """Changing points_system must change the inputs digest (recompute triggers)."""
    series = make_series()
    india, australia = make_team("India"), make_team("Australia")
    make_series_result(series, india, australia, india, "WIN", start_date=date(2026, 2, 1))

    recompute_standings(series)
    row = standings_rows(series)[str(india.id)]
    digest_default = row.inputs_digest
    assert row.points == 2

    series.points_system = {"points_win": 4}
    series.save(update_fields=["points_system"])
    recompute_standings(series)

    row.refresh_from_db()
    assert row.points == 4
    assert row.inputs_digest != digest_default


# --------------------------------------------------------------------------- #
# Form
# --------------------------------------------------------------------------- #


def test_form_string_most_recent_first_window_5() -> None:
    series = make_series()
    india, australia = make_team("India"), make_team("Australia")
    # In date order: W, L, T, N, W, W  → the 5 most recent are L,T,N,W,W and
    # the form string reads most-recent-first: WWNTL.
    make_series_result(series, india, australia, india, "WIN", start_date=date(2026, 2, 1))
    make_series_result(series, india, australia, australia, "WIN", start_date=date(2026, 2, 3))
    make_series_result(
        series, india, australia, None, "TIE", start_date=date(2026, 2, 5), no_innings=True
    )
    make_series_result(
        series, india, australia, None, "NO_RESULT", start_date=date(2026, 2, 7), no_innings=True
    )
    make_series_result(series, india, australia, india, "WIN", start_date=date(2026, 2, 9))
    make_series_result(series, india, australia, india, "WIN", start_date=date(2026, 2, 11))

    recompute_standings(series)
    rows = standings_rows(series)

    assert rows[str(india.id)].form == "WWNTL"
    # Australia in date order: L,W,T,N,L,L → last 5 W,T,N,L,L reversed = LLNTW.
    assert rows[str(australia.id)].form == "LLNTW"


# --------------------------------------------------------------------------- #
# qualify_status / no_of_qual passthrough
# --------------------------------------------------------------------------- #


def test_qualify_status_and_no_of_qual_preserved_across_recomputes() -> None:
    series = make_series(points_system={"no_of_qual": 2})
    india, australia = make_team("India"), make_team("Australia")
    make_series_result(series, india, australia, india, "WIN", start_date=date(2026, 2, 1))

    recompute_standings(series)
    row = standings_rows(series)[str(india.id)]
    assert row.no_of_qual == 2  # from points_system for a fresh row
    assert row.qualify_status is None

    # A steward marks qualification; a later recompute must not wipe it.
    row.qualify_status = "Qualified"
    row.save(update_fields=["qualify_status"])

    make_series_result(series, india, australia, india, "WIN", start_date=date(2026, 2, 3))
    recompute_standings(series)
    row.refresh_from_db()
    assert row.qualify_status == "Qualified"
    assert row.no_of_qual == 2


def test_no_of_qual_absent_without_points_system() -> None:
    series = make_series()
    india, australia = make_team("India"), make_team("Australia")
    make_series_result(series, india, australia, india, "WIN", start_date=date(2026, 2, 1))

    recompute_standings(series)
    row = standings_rows(series)[str(india.id)]
    assert row.no_of_qual is None
    assert row.qualify_status is None


# --------------------------------------------------------------------------- #
# NRR edge cases (independent oracle)
# --------------------------------------------------------------------------- #


def test_nrr_full_quota_when_bowled_out() -> None:
    series = make_series()
    india, australia = make_team("India"), make_team("Australia")
    # India 160/10 in 110 balls (all out → NRR charges the full 20-over quota);
    # Australia 120/8 in 110 balls (not all out → 110/6 overs charged).
    make_series_result(
        series,
        india,
        australia,
        india,
        "WIN",
        start_date=date(2026, 2, 1),
        runs1=160,
        wickets1=10,
        balls1=110,
        all_out1=True,
        runs2=120,
        wickets2=8,
        balls2=110,
        all_out2=False,
    )

    recompute_standings(series)
    rows = standings_rows(series)

    expected_india = oracle_nrr(
        runs_for=160,
        balls_faced=110,
        all_out=True,
        runs_against=120,
        balls_bowled=110,
        opponent_all_out=False,
    )
    expected_australia = oracle_nrr(
        runs_for=120,
        balls_faced=110,
        all_out=False,
        runs_against=160,
        balls_bowled=110,
        opponent_all_out=True,
    )
    assert expected_india is not None and expected_australia is not None
    assert rows[str(india.id)].nrr == Decimal(f"{expected_india:.3f}")
    assert rows[str(australia.id)].nrr == Decimal(f"{expected_australia:.3f}")
    # Full quota was charged: 160/20 - 120/(110/6) ≈ 1.455.
    assert rows[str(india.id)].nrr == Decimal("1.455")


def test_nrr_no_result_match_excluded() -> None:
    series = make_series()
    india, australia = make_team("India"), make_team("Australia")
    # A countable win: India 160/20 overs, Australia 120 all out in 110 balls
    # (full quota charged against them → 20 overs). NRR = 8.0 - 6.0 = 2.0.
    make_series_result(
        series,
        india,
        australia,
        india,
        "WIN",
        start_date=date(2026, 2, 1),
        runs1=160,
        wickets1=3,
        balls1=120,
        all_out1=False,
        runs2=120,
        wickets2=10,
        balls2=110,
        all_out2=True,
    )
    # A no-result adds points (default 1) but nothing to NRR.
    make_series_result(
        series, india, australia, None, "NO_RESULT", start_date=date(2026, 2, 3), no_innings=True
    )

    recompute_standings(series)
    row = standings_rows(series)[str(india.id)]

    assert row.no_result == 1
    assert row.points == 3  # 2 (win) + 1 (no result)
    assert row.nrr == Decimal("2.000")


def test_nrr_none_when_only_no_result_matches() -> None:
    series = make_series()
    india, australia = make_team("India"), make_team("Australia")
    make_series_result(
        series, india, australia, None, "NO_RESULT", start_date=date(2026, 2, 1), no_innings=True
    )
    make_series_result(
        series, india, australia, None, "NO_RESULT", start_date=date(2026, 2, 3), no_innings=True
    )

    recompute_standings(series)
    rows = standings_rows(series)

    assert rows[str(india.id)].nrr is None
    assert rows[str(australia.id)].nrr is None
    assert rows[str(india.id)].no_result == 2
    assert rows[str(india.id)].points == 2  # 1 per no-result


# --------------------------------------------------------------------------- #
# Stage scoping + group name
# --------------------------------------------------------------------------- #


def test_group_name_from_stage() -> None:
    series = make_series()
    stage = MatchStage.objects.create(
        series=series, name="Group A", stage_type="group", group_name="A"
    )
    india, australia = make_team("India"), make_team("Australia")
    make_series_result(
        series, india, australia, india, "WIN", start_date=date(2026, 2, 1), stage=stage
    )

    recompute_standings(series, stage)
    row = standings_rows(series, stage)[str(india.id)]

    assert row.group_name == "A"
    assert row.stage_id == stage.id


def test_stage_scoped_standings_do_not_leak() -> None:
    series = make_series()
    group_a = MatchStage.objects.create(
        series=series, name="Group A", stage_type="group", group_name="A"
    )
    final = MatchStage.objects.create(
        series=series, name="Final", stage_type="final", group_name=None
    )
    india, australia = make_team("India"), make_team("Australia")
    make_series_result(
        series, india, australia, india, "WIN", start_date=date(2026, 2, 1), stage=group_a
    )
    make_series_result(
        series, india, australia, india, "WIN", start_date=date(2026, 2, 3), stage=final
    )

    recompute_standings(series, group_a)
    rows_a = standings_rows(series, group_a)
    assert rows_a[str(india.id)].matches_played == 1

    recompute_standings(series, final)
    rows_f = standings_rows(series, final)
    assert rows_f[str(india.id)].matches_played == 1
    assert rows_f[str(india.id)].group_name is None


# --------------------------------------------------------------------------- #
# Reconciliation hook
# --------------------------------------------------------------------------- #


def _provider_rows(series: Series) -> dict[str, dict]:
    """The pipeline's own standings, shaped like a provider would send them."""
    rows = standings_rows(series)
    return {
        tid: {
            "played": r.matches_played,
            "won": r.matches_won,
            "lost": r.matches_lost,
            "tied": r.matches_tied,
            "drawn": r.matches_drawn,
            "no_result": r.no_result,
            "points": r.points,
            "nrr": float(r.nrr) if r.nrr is not None else None,
        }
        for tid, r in rows.items()
    }


def test_reconcile_standings_no_diff_returns_none() -> None:
    series = make_series()
    india, australia = make_team("India"), make_team("Australia")
    make_series_result(series, india, australia, india, "WIN", start_date=date(2026, 2, 1))
    recompute_standings(series)

    job = recompute.reconcile_standings(series.id, _provider_rows(series), provider=make_provider())
    assert job is None
    assert not ReconciliationJob.objects.filter(scope_key=f"standings:{series.id}:-").exists()


def test_reconcile_standings_mismatch_opens_job_and_dlq() -> None:
    from apps.ingestion.services.reporters import register_default_reporters

    register_default_reporters()

    series = make_series()
    india, australia = make_team("India"), make_team("Australia")
    make_series_result(series, india, australia, india, "WIN", start_date=date(2026, 2, 1))
    recompute_standings(series)

    wrong = _provider_rows(series)
    wrong[str(india.id)]["points"] = 99

    job = recompute.reconcile_standings(series.id, wrong, provider=make_provider())

    assert job is not None
    assert job.scope_key == f"standings:{series.id}:-"
    assert job.status == ReconciliationJob.STATUS_OPEN
    assert job.differences[str(india.id)]["points"] == {"ours": 2, "provider": 99}
    assert ReconciliationJob.objects.filter(scope_key=job.scope_key).count() == 1

    err = IngestionError.objects.filter(stage="reconciliation_mismatch").latest("created_at")
    assert err.error_class == "ReconciliationMismatch"
    assert err.payload["scope_key"] == job.scope_key

    # Re-running the same reconciliation refreshes, never duplicates.
    job2 = recompute.reconcile_standings(series.id, wrong, provider=make_provider())
    assert job2.id == job.id
    assert ReconciliationJob.objects.filter(scope_key=job.scope_key).count() == 1
