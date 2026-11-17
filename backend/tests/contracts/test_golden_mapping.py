"""Golden mapping tests: Raw -> canonical, pinned (Phase I Task 1).

The Phase I provider gate FAILS (docs/PROVIDER_DECISION.md is a placeholder),
so there is no licensed adapter to golden-test. This suite pins the boundary
any future adapter must meet, using the ReplayAdapter as the precedent:

* ``raw``  = the recorded rows the adapter reads (the Phase C ``delivery``
  table, i.e. the provider-shaped input for replay);
* ``canonical`` = the normalized records the live pipeline consumes
  (``LiveInningsBatch``/``DeliveryDraft`` from ``iter_deliveries``,
  ``LiveState`` from ``get_state``, the card from ``get_scorecard``).

The snapshot is projected to a deterministic shape: player UUIDs are replaced
with stable tokens (``P0..Pn`` assigned by sorted id order) so the golden file
does not depend on per-run factory UUIDs; every cricket-meaningful field is
pinned verbatim. Regenerate deliberately with
``PHASE_I_REGEN_GOLDEN=1 uv run pytest tests/contracts/test_golden_mapping.py -q``
and review the diff — never regenerate to hide a behaviour change.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.django_db

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
GOLDEN_FILE = GOLDEN_DIR / "replay_t20_21balls.json"

REGEN = os.environ.get("PHASE_I_REGEN_GOLDEN") == "1"


def _recorded_match() -> object:
    """The deterministic T20 fixture (21 deliveries: 10 + 11 balls)."""
    from tests.phase_d.test_live_apply import _finish_t20, make_context

    ctx = make_context()
    _finish_t20(ctx)
    return ctx["match"]


def _player_tokens(ids: set[object]) -> dict[str, str]:
    """Map player UUIDs to stable P0..Pn tokens by sorted id order."""
    ordered = sorted({str(i) for i in ids if i is not None})
    return {uuid: f"P{n}" for n, uuid in enumerate(ordered)}


def _projection(match: object) -> dict[str, object]:
    """The deterministic Raw->canonical snapshot for the recorded match."""
    from apps.providers.adapters.replay import ReplayAdapter

    adapter = ReplayAdapter()
    match_id = str(match.id)  # type: ignore[attr-defined]

    tokens = _player_tokens({})
    # First pass collects every player id referenced by the deliveries.
    for batch in adapter.iter_deliveries(match_id, None):
        for draft in batch.drafts:
            tokens = _player_tokens(
                {
                    *tokens,
                    draft.bowler_id,
                    draft.batter_id,
                    draft.non_striker_id,
                    draft.dismissed_player_id,
                    draft.fielder1_id,
                    draft.fielder2_id,
                    draft.fielder3_id,
                }
            )

    def _ball(draft: object) -> dict[str, object]:
        return {
            "delivery_seq": draft.delivery_seq,
            "over_number": draft.over_number,
            "ball_in_over": draft.ball_in_over,
            "bowler": tokens[str(draft.bowler_id)],
            "batter": tokens[str(draft.batter_id)],
            "non_striker": tokens[str(draft.non_striker_id)],
            "runs_off_bat": draft.runs_off_bat,
            "wides": draft.wides,
            "noballs": draft.noballs,
            "byes": draft.byes,
            "leg_byes": draft.leg_byes,
            "penalty_runs": draft.penalty_runs,
            "is_wicket": draft.is_wicket,
            "dismissal_type": draft.dismissal_type,
            "bowler_credited_wicket": draft.bowler_credited_wicket,
            "revision": draft.revision,
        }

    deliveries: list[dict[str, object]] = []
    for batch in adapter.iter_deliveries(match_id, None):
        for draft in batch.drafts:
            deliveries.append({"innings_number": batch.innings_number, **_ball(draft)})

    state = adapter.get_state(match_id)
    card = adapter.get_scorecard(match_id)
    card["match_id"] = "M"  # token: the recorded match's UUID is per-run
    return {
        "match": {
            "id_token": "M",
            "status": str(match.status),  # type: ignore[attr-defined]
            "format_type": str(match.format_type),  # type: ignore[attr-defined]
        },
        "state": {
            "status": state.status,
            "state_title": state.state_title,
            "status_detail": state.status_detail,
        },
        "card": card,
        "deliveries": deliveries,
    }


def test_golden_mapping_matches_recorded_fixture() -> None:
    match = _recorded_match()
    snapshot = _projection(match)

    if REGEN:
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        GOLDEN_FILE.write_text(
            json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        pytest.skip("PHASE_I_REGEN_GOLDEN=1: golden file rewritten, review the diff")

    assert GOLDEN_FILE.is_file(), (
        f"golden file missing: {GOLDEN_FILE}. Regenerate deliberately with "
        f"PHASE_I_REGEN_GOLDEN=1 and review the diff."
    )
    expected = json.loads(GOLDEN_FILE.read_text(encoding="utf-8"))
    assert snapshot == expected, (
        "ReplayAdapter Raw->canonical mapping drifted from the golden snapshot. "
        "If the change is intentional, regenerate with PHASE_I_REGEN_GOLDEN=1 "
        "and review the diff."
    )


def test_golden_mapping_covers_every_delivery_and_innings() -> None:
    match = _recorded_match()
    snapshot = _projection(match)

    deliveries = snapshot["deliveries"]
    assert len(deliveries) == 21  # innings 1: 10 balls, innings 2: 11 balls
    innings_numbers = {d["innings_number"] for d in deliveries}
    assert innings_numbers == {1, 2}

    card = snapshot["card"]
    assert [s["innings_number"] for s in card["summaries"]] == [1, 2]
    for summary in card["summaries"]:
        assert summary["runs"] > 0
        assert summary["wickets"] >= 0

    # Player tokens are stable and consistent across the whole stream
    # (the fixture uses exactly three players: one bowler P2, two batters;
    # P1 appears only as non-striker because no wicket rotates the batters).
    tokens = (
        {d["bowler"] for d in deliveries}
        | {d["batter"] for d in deliveries}
        | {d["non_striker"] for d in deliveries}
    )
    assert tokens == {"P0", "P1", "P2"}
    assert {d["bowler"] for d in deliveries} == {"P2"}  # one bowler throughout
