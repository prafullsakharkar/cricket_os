"""Phase I Task 2: shadow comparison unit tests.

* a clean replay corpus produces **zero** findings (no convention false-
  positives between the read models and the independent raw-delivery
  derivation);
* injected mismatches of every category are detected with the right severity;
* the comparison is deterministic and **never auto-corrects** (match rows are
  not written by the comparison);
* findings carry the match id for DLQ triage.
"""

from __future__ import annotations

import pytest

from apps.shadow.cards import Card, CardInnings
from apps.shadow.compare import (
    CATEGORIES,
    SEVERITY_CRITICAL,
    SEVERITY_DIFF,
    ComparisonReport,
    compare_match,
)

pytestmark = pytest.mark.django_db


def _finished_t20() -> object:
    from tests.phase_d.test_live_apply import _finish_t20, make_context

    ctx = make_context()
    _finish_t20(ctx)
    return ctx["match"]


# --------------------------------------------------------------------------- #
# Clean baseline + determinism
# --------------------------------------------------------------------------- #


def test_clean_replay_corpus_is_consistent() -> None:
    match = _finished_t20()
    report = compare_match(match)
    assert isinstance(report, ComparisonReport)
    assert report.mismatch_count == 0, [f.as_dict() for f in report.findings]


def test_comparison_is_deterministic() -> None:
    match = _finished_t20()
    first = [f.as_dict() for f in compare_match(match).findings]
    second = [f.as_dict() for f in compare_match(match).findings]
    assert first == second


def test_categories_are_the_briefs_six() -> None:
    assert set(CATEGORIES) == {"totals", "wickets", "extras", "result", "batting", "bowling"}


# --------------------------------------------------------------------------- #
# Injected mismatches of every category
# --------------------------------------------------------------------------- #


def test_injected_totals_mismatch_is_critical() -> None:
    match = _finished_t20()
    summary = match.innings.order_by("innings_number").first().summary
    summary.runs += 1
    summary.save(update_fields=["runs"])

    report = compare_match(match)
    findings = [f for f in report.findings if f.category == "totals"]
    assert findings, "totals mismatch not detected"
    assert findings[0].severity == SEVERITY_CRITICAL
    assert findings[0].field == "runs"
    assert findings[0].match_id == str(match.id)


def test_injected_wickets_mismatch_is_critical() -> None:
    match = _finished_t20()
    summary = match.innings.order_by("innings_number").first().summary
    summary.wickets += 1
    summary.save(update_fields=["wickets"])

    report = compare_match(match)
    findings = [f for f in report.findings if f.category == "wickets"]
    assert findings
    assert findings[0].severity == SEVERITY_CRITICAL


def test_injected_extras_mismatch_is_critical() -> None:
    match = _finished_t20()
    summary = match.innings.order_by("innings_number").first().summary
    extras = dict(summary.extras or {})
    extras["wides"] = extras.get("wides", 0) + 5
    summary.extras = extras
    summary.save(update_fields=["extras"])

    report = compare_match(match)
    findings = [f for f in report.findings if f.category == "extras"]
    assert findings
    assert findings[0].severity == SEVERITY_CRITICAL
    assert findings[0].field == "wides"


def test_injected_batting_line_mismatch_is_diff() -> None:
    match = _finished_t20()
    innings = match.innings.order_by("innings_number").first()
    entry = innings.batting_entries.first()
    entry.runs += 4
    entry.save(update_fields=["runs"])

    report = compare_match(match)
    findings = [f for f in report.findings if f.category == "batting"]
    assert findings
    assert findings[0].severity == SEVERITY_DIFF
    assert findings[0].field == "runs"
    assert findings[0].entity_key == str(entry.player_id)


def test_injected_bowling_line_mismatch_is_diff() -> None:
    match = _finished_t20()
    innings = match.innings.order_by("innings_number").first()
    entry = innings.bowling_entries.first()
    entry.runs_conceded += 3
    entry.save(update_fields=["runs_conceded"])

    report = compare_match(match)
    findings = [f for f in report.findings if f.category == "bowling"]
    assert findings
    assert findings[0].severity == SEVERITY_DIFF
    assert findings[0].field == "runs"


def test_injected_status_mismatch_is_critical() -> None:
    match = _finished_t20()
    # The comparison runs on completed matches; finalise so ours is COMPLETED.
    from apps.matches.services import result_service

    result_service.finalise(match)
    assert str(match.status) == "COMPLETED"

    provider_card = Card(match_id=str(match.id), status="IN_PROGRESS")
    report = compare_match(match, provider=provider_card)
    findings = [f for f in report.findings if f.category == "result"]
    assert findings
    assert findings[0].severity == SEVERITY_CRITICAL
    assert findings[0].field == "status"


# --------------------------------------------------------------------------- #
# No auto-correction + queue semantics
# --------------------------------------------------------------------------- #


def test_comparison_never_auto_corrects_match_rows() -> None:
    match = _finished_t20()
    summary = match.innings.order_by("innings_number").first().summary
    before = summary.runs
    summary.runs += 1
    summary.save(update_fields=["runs"])

    report = compare_match(match)
    assert report.mismatch_count >= 1

    summary.refresh_from_db()
    assert summary.runs == before + 1  # the injected wrong value is untouched
    match.refresh_from_db()
    assert match.revision == 1  # no revision bump, no correction applied


def test_report_finding_is_queued_via_registered_reporter() -> None:
    """Findings are handed to the reporter hook (never auto-corrected).

    ``compare_match`` is side-effect free by design; the driver calls
    ``report_finding`` per finding. This test pins the queue wiring: the
    registered reporter receives every finding with its match id.
    """
    from apps.shadow.compare import (
        register_shadow_comparison_reporter,
        report_finding,
    )

    match = _finished_t20()
    summary = match.innings.order_by("innings_number").first().summary
    summary.runs += 1
    summary.save(update_fields=["runs"])

    received: list[object] = []

    def reporter(finding: object) -> None:
        received.append(finding)

    register_shadow_comparison_reporter(reporter)
    try:
        report = compare_match(match)
        assert report.mismatch_count >= 1
        for finding in report.findings:
            report_finding(finding)
        assert len(received) == report.mismatch_count
        assert received[0].match_id == str(match.id)  # type: ignore[attr-defined]
    finally:
        register_shadow_comparison_reporter(None)


# --------------------------------------------------------------------------- #
# Injected-card coverage of defensive branches
# --------------------------------------------------------------------------- #


def _card_with_innings(innings: list[object], status: str = "COMPLETED") -> Card:
    return Card(match_id="M", status=status, innings=tuple(innings))


def test_overs_display_mismatch_is_reported() -> None:
    match = _finished_t20()
    ours = _card_with_innings(
        [CardInnings(innings_number=1, runs=60, overs_display="10.0")],
        status=str(match.status),
    )
    second = _card_with_innings(
        [CardInnings(innings_number=1, runs=60, overs_display="9.4")],
        status=str(match.status),
    )
    report = compare_match(match, ours=ours, second=second)
    findings = [f for f in report.findings if f.category == "totals" and f.field == "overs_display"]
    assert findings


def test_second_only_extras_mismatch_is_reported() -> None:
    """Provider agrees with ours, but the second source disagrees."""
    match = _finished_t20()
    ours = _card_with_innings(
        [CardInnings(innings_number=1, runs=60, extras={"wides": 0, "total": 0})],
        status=str(match.status),
    )
    provider = _card_with_innings(
        [CardInnings(innings_number=1, runs=60, extras={"wides": 0, "total": 0})],
        status=str(match.status),
    )
    second = _card_with_innings(
        [CardInnings(innings_number=1, runs=60, extras={"wides": 4, "total": 4})],
        status=str(match.status),
    )
    report = compare_match(match, ours=ours, provider=provider, second=second)
    findings = [f for f in report.findings if f.category == "extras" and f.field == "wides"]
    assert findings
    assert findings[0].provider == 0  # provider side agrees with ours


def test_batter_absent_from_second_source_is_skipped() -> None:
    """A batter missing from the second derivation is not a value diff."""
    from apps.shadow.cards import CardBatting

    match = _finished_t20()
    ours = _card_with_innings(
        [
            CardInnings(
                innings_number=1,
                batting=(CardBatting(player_key="P0", runs=10, balls=5),),
            )
        ],
        status=str(match.status),
    )
    second = _card_with_innings([CardInnings(innings_number=1)], status=str(match.status))
    report = compare_match(match, ours=ours, second=second)
    assert [f for f in report.findings if f.category == "batting"] == []


def test_bowler_absent_from_second_source_is_skipped() -> None:
    from apps.shadow.cards import CardBowling

    match = _finished_t20()
    ours = _card_with_innings(
        [CardInnings(innings_number=1, bowling=(CardBowling(player_key="P2", legal_balls=6),))],
        status=str(match.status),
    )
    second = _card_with_innings([CardInnings(innings_number=1)], status=str(match.status))
    report = compare_match(match, ours=ours, second=second)
    assert [f for f in report.findings if f.category == "bowling"] == []


def test_provider_card_without_status_is_skipped() -> None:
    """A provider card with no status cannot produce a status mismatch."""
    match = _finished_t20()
    provider = Card(match_id=str(match.id), status=None, innings=())
    report = compare_match(match, provider=provider)
    assert [f for f in report.findings if f.category == "result"] == []


def test_by_severity_aggregates_findings() -> None:
    match = _finished_t20()
    summary = match.innings.order_by("innings_number").first().summary
    summary.runs += 1  # critical totals finding
    summary.save(update_fields=["runs"])
    report = compare_match(match)
    assert report.by_severity["critical"] >= 1


# --------------------------------------------------------------------------- #
# Second-source derivation sanity
# --------------------------------------------------------------------------- #


def test_second_source_aggregation_matches_engine_conventions() -> None:
    """The raw-delivery derivation must agree with the read models exactly."""
    from apps.shadow.cards import card_from_deliveries, card_from_read_models

    match = _finished_t20()
    ours = card_from_read_models(match)
    second = card_from_deliveries(match)

    assert len(ours.innings) == len(second.innings) == 2
    for our_inn, second_inn in zip(ours.innings, second.innings, strict=False):
        assert our_inn.runs == second_inn.runs
        assert our_inn.wickets == second_inn.wickets
        assert our_inn.extras == second_inn.extras
        # Per-batter lines agree (runs/balls/fours/sixes/dismissal).
        our_batting = {b.player_key: b for b in our_inn.batting}
        for key, second_bat in {b.player_key: b for b in second_inn.batting}.items():
            our_bat = our_batting[key]
            assert our_bat.runs == second_bat.runs
            assert our_bat.balls == second_bat.balls
            assert our_bat.fours == second_bat.fours
            assert our_bat.sixes == second_bat.sixes
            assert our_bat.dismissal == second_bat.dismissal
        # Per-bowler lines agree (legal_balls/runs/wickets).
        our_bowling = {b.player_key: b for b in our_inn.bowling}
        for key, second_bowl in {b.player_key: b for b in second_inn.bowling}.items():
            our_bowl = our_bowling[key]
            assert our_bowl.legal_balls == second_bowl.legal_balls
            assert our_bowl.runs == second_bowl.runs
            assert our_bowl.wickets == second_bowl.wickets
