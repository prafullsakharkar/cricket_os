"""Phase D group 1: lifecycle state machine + status text (pure + DB)."""

from __future__ import annotations

import pytest

from apps.core.enums import MatchStatus
from apps.matches.services import state as sm
from apps.matches.services import state_text

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------- #
# Pure transition table
# --------------------------------------------------------------------------- #


def test_transition_table_covers_every_status() -> None:
    assert set(sm.ALLOWED_TRANSITIONS) == set(MatchStatus.values)


def test_every_status_reapplied_is_a_noop() -> None:
    for status in MatchStatus.values:
        assert sm.can_transition(status, status) is True


def test_every_pair_matches_the_explicit_table() -> None:
    for from_status in MatchStatus.values:
        for to_status in MatchStatus.values:
            expected = from_status == to_status or to_status in sm.ALLOWED_TRANSITIONS[from_status]
            assert sm.can_transition(from_status, to_status) is expected, (
                from_status,
                to_status,
            )


def test_unknown_status_is_rejected() -> None:
    assert sm.can_transition(MatchStatus.SCHEDULED, "NOT_A_STATUS") is False


def test_terminal_states_only_reopen_explicitly() -> None:
    for terminal in sm.TERMINAL:
        for target in MatchStatus.values:
            if target == MatchStatus.IN_PROGRESS:
                assert sm.can_transition(terminal, target) is False
                assert sm.can_transition(terminal, target, reopen=True) is True
            elif target != terminal:
                assert sm.can_transition(terminal, target, reopen=True) is False


# --------------------------------------------------------------------------- #
# DB enforcement
# --------------------------------------------------------------------------- #


def test_legal_transition_updates_lifecycle_and_text(make_match) -> None:
    match = make_match()
    result = sm.transition(
        match,
        MatchStatus.IN_PROGRESS,
        state_title="Day 1 - Morning",
        status_detail="IND 12/0",
    )
    assert result.changed is True
    assert result.previous_status == MatchStatus.SCHEDULED
    match.refresh_from_db()
    assert match.status == MatchStatus.IN_PROGRESS
    assert match.state_title == "Day 1 - Morning"
    assert match.status_detail == "IND 12/0"
    assert match.last_ingested_at is None  # only ingestion stamps freshness
    assert result.payload == {
        "match_id": str(match.id),
        "from": "SCHEDULED",
        "to": "IN_PROGRESS",
        "state_title": "Day 1 - Morning",
        "status_detail": "IND 12/0",
        "reopened": False,
        "source": None,
    }


def test_reapplying_current_status_is_idempotent(make_match) -> None:
    match = make_match(status=MatchStatus.IN_PROGRESS)
    before = match.revision
    result = sm.transition(match, MatchStatus.IN_PROGRESS, status_detail="ignored")
    assert result.changed is False
    match.refresh_from_db()
    assert match.revision == before
    assert match.status_detail is None


def test_illegal_transition_raises_typed_error(make_match) -> None:
    match = make_match(status=MatchStatus.IN_PROGRESS)
    with pytest.raises(sm.IllegalTransition) as excinfo:
        sm.transition(match, MatchStatus.TOSS)
    assert excinfo.value.code == "unprocessable"
    assert excinfo.value.details == {
        "match_id": str(match.id),
        "from": "IN_PROGRESS",
        "to": "TOSS",
    }
    match.refresh_from_db()
    assert match.status == MatchStatus.IN_PROGRESS


def test_completed_match_does_not_go_back_without_reopen(make_match) -> None:
    match = make_match(status=MatchStatus.COMPLETED, result_type="WIN", result_text="won")
    with pytest.raises(sm.IllegalTransition):
        sm.transition(match, MatchStatus.IN_PROGRESS)
    reopened = sm.reopen(match, reason="provider correction")
    assert reopened.changed is True
    assert reopened.reopened is True
    match.refresh_from_db()
    assert match.status == MatchStatus.IN_PROGRESS
    assert match.result_type is None
    assert match.result_text is None
    assert match.end_time_utc is None
    assert match.status_detail == "provider correction"


def test_terminal_transition_stamps_end_time(make_match) -> None:
    match = make_match(status=MatchStatus.IN_PROGRESS)
    sm.transition(match, MatchStatus.ABANDONED)
    match.refresh_from_db()
    assert match.end_time_utc is not None


def test_full_limited_overs_path_exercises_every_edge(make_match) -> None:
    match = make_match(format_type="T20I")
    path = [
        MatchStatus.TOSS,
        MatchStatus.IN_PROGRESS,
        MatchStatus.DRINKS,
        MatchStatus.IN_PROGRESS,
        MatchStatus.INNINGS_BREAK,
        MatchStatus.IN_PROGRESS,
        MatchStatus.COMPLETED,
    ]
    for target in path:
        assert sm.transition(match, target).changed is True
    match.refresh_from_db()
    assert match.status == MatchStatus.COMPLETED


# --------------------------------------------------------------------------- #
# Status text (pure)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("status", "day", "session", "expected"),
    [
        (MatchStatus.IN_PROGRESS, 4, "Tea", "Day 4 - Tea"),
        (MatchStatus.IN_PROGRESS, 2, None, "Day 2"),
        (MatchStatus.IN_PROGRESS, None, "Tea", "In progress"),
        (MatchStatus.INNINGS_BREAK, None, None, "Innings break"),
        (MatchStatus.RAIN_DELAY, None, None, "Rain delay"),
        (MatchStatus.STUMPS, None, None, "Stumps"),
        ("UNKNOWN", None, None, "Unknown"),
    ],
)
def test_state_title(status: str, day: int | None, session: str | None, expected: str) -> None:
    assert state_text.state_title(status, day=day, session=session) == expected


def test_detail_formatters() -> None:
    assert state_text.lead_detail("AUS", 50) == "AUS lead by 50 runs"
    assert state_text.lead_detail("AUS", 1) == "AUS lead by 1 run"
    assert state_text.trail_detail("IND", 12) == "IND trail by 12 runs"
    assert state_text.chase_detail("IND", 45, 30) == "IND need 45 runs from 30 balls"
    assert state_text.chase_detail("IND", 45, 1) == "IND need 45 runs from 1 ball"
    assert state_text.chase_detail("IND", 45, None) == "IND need 45 runs"
    assert state_text.all_out_detail("IND", 250) == "IND all out for 250"
    assert state_text.target_detail("IND", 251) == "IND target 251"
