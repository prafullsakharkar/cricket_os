"""Phase F Group 9: edge-path coverage for the statistics read services.

The read API tests exercise the happy paths end-to-end; this file closes the
edge paths (unknown entities, empty aggregate rows, canonical H2H mapping,
leaderboard format filter) by calling the services directly so the Phase F
90% overall coverage gate holds without a second pass over the HTTP layer.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.core.errors import NotFound
from apps.statistics.models import HeadToHead, Leaderboard
from apps.statistics.services.reads import (
    head_to_head,
    leaderboard_rows,
    player_statistics,
    team_statistics,
)

from ..phase_c.factories import make_player, make_team

pytestmark = pytest.mark.django_db


def test_player_statistics_rejects_unsupported_scope() -> None:
    player = make_player("Edge Batter")
    with pytest.raises(ValueError, match="unsupported scope"):
        player_statistics(player.id, scope="TEAM")


def test_player_statistics_unknown_player_not_found() -> None:
    with pytest.raises(NotFound):
        player_statistics("00000000-0000-0000-0000-000000000001")


def test_player_statistics_empty_aggregate_row() -> None:
    player = make_player("Empty Batter")
    body = player_statistics(player.id)
    assert body["official"]["batting"]["matches"] == 0
    assert body["official"]["batting"]["high_score"] is None
    assert body["official"]["bowling"]["best"]["wickets"] is None


def test_team_statistics_unknown_team_not_found() -> None:
    with pytest.raises(NotFound):
        team_statistics("00000000-0000-0000-0000-000000000002")


def test_team_statistics_empty_aggregate_row() -> None:
    team = make_team("Empty XI")
    body = team_statistics(team.id)
    assert body["official"]["matches"] == 0
    assert body["source_kind"] == "computed"


def test_leaderboard_rows_format_filter() -> None:
    player = make_player("Lb Edge")
    Leaderboard.objects.create(
        scope_type="CAREER",
        scope_ref_id=None,
        metric="runs",
        format_type="T20I",
        rank=1,
        player=player,
        value=Decimal("100"),
        inputs_digest="digest",
    )
    t20 = leaderboard_rows(scope="CAREER", scope_ref_id=None, format_type="T20I")
    assert [r["player"]["id"] for r in t20] == [str(player.id)]
    other = leaderboard_rows(scope="CAREER", scope_ref_id=None, format_type="ODI")
    assert other == []


def test_head_to_head_empty_row_and_mapping() -> None:
    low, high = make_team("Alpha"), make_team("Omega")
    # No H2H row yet: empty aggregate with names resolved.
    body = head_to_head(low.id, high.id)
    assert body["matches"] == 0
    assert body["team1"]["name"] == "Alpha"

    HeadToHead.objects.create(
        team_low=low,
        team_high=high,
        format_type=None,
        matches=3,
        low_wins=1,
        high_wins=2,
    )
    # Request teams in reverse order: team1 is the canonical high id, so the
    # response must map low/high wins back.
    reversed_body = head_to_head(high.id, low.id)
    assert reversed_body["team1_wins"] == 2
    assert reversed_body["team2_wins"] == 1


def test_head_to_head_unknown_team_not_found() -> None:
    with pytest.raises(NotFound):
        head_to_head("00000000-0000-0000-0000-000000000003", "00000000-0000-0000-0000-000000000004")
