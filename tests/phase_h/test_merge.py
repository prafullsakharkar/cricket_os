"""Player-merge fallout (Task 4, plan ruling 5).

Fantasy tables are NOT registered with the generic merge registry (their
plain-uuid cross references and append-only ledger cannot be re-pointed). The
``player.merged`` subscriber instead recomputes the winner for the union of
the two players' matches after the statistics subscriber has re-pointed the
source rows, so the winner's snapshot now attributes the loser's lines. The
winner's rebuild is digest-identical (a second recompute is a no-op), the
loser's read-model rows are removed, and the loser's ledger history survives
(append-only).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import transaction

from apps.core import events as core_events
from apps.fantasy.models import FantasyCalcAudit, FantasyPointsLedger, PlayerMatchPerformance
from apps.fantasy.services import ledger, recompute

from .factories import add_stat, make_completed_match, make_ruleset_version

pytestmark = pytest.mark.django_db


def _merge(winner_id: str, loser_id: str) -> None:
    """Drive the production wiring: statistics repoints source rows, then the
    fantasy subscriber recomputes — both synchronously inside the merge txn."""
    from apps.statistics.services import recompute as stats_recompute

    core_events.subscribe(core_events.TOPIC_PLAYER_MERGED, stats_recompute._on_player_merged)
    core_events.subscribe(core_events.TOPIC_PLAYER_MERGED, recompute._on_player_merged)
    core_events.publish(
        core_events.TOPIC_PLAYER_MERGED,
        {"canonical_player_id": winner_id, "merged_player_id": loser_id},
        on_commit=False,
    )


def test_merge_recomputes_winner_and_removes_loser_read_model() -> None:
    match1, team1, _ = make_completed_match()
    winner, _ = add_stat(match1, team1, runs=58, balls=42, out=True)
    _, version = make_ruleset_version()
    recompute.recompute_match(match1)

    match2, team2, _ = make_completed_match(format_type="ODI")
    loser, _ = add_stat(match2, team2, runs=30, balls=20, out=True)
    recompute.recompute_match(match2)

    assert PlayerMatchPerformance.objects.filter(player_id=loser.id, match_id=match2.id).exists()

    with transaction.atomic():
        _merge(str(winner.id), str(loser.id))

    # Winner now owns both matches; totals are identical to the loser's run.
    assert PlayerMatchPerformance.objects.filter(player_id=winner.id, match_id=match1.id).exists()
    winner_m2 = PlayerMatchPerformance.objects.get(
        player_id=winner.id, match_id=match2.id, ruleset_version_id=version.id
    )
    assert winner_m2.total_points == Decimal("30.00")
    # Loser's read model is gone; ledger history survives (append-only).
    assert not PlayerMatchPerformance.objects.filter(player_id=loser.id).exists()
    assert FantasyPointsLedger.objects.filter(player_id=loser.id, match_id=match2.id).exists()
    # Current ledger for the winner's inherited match.
    assert ledger.sum_current(match2.id, winner.id, version.id) == Decimal("30.00")


def test_merge_rebuild_is_digest_identical_idempotent() -> None:
    match1, team1, _ = make_completed_match()
    winner, _ = add_stat(match1, team1, runs=20, balls=12)
    _, _version = make_ruleset_version()
    recompute.recompute_match(match1)

    match2, team2, _ = make_completed_match(format_type="ODI")
    loser, _ = add_stat(match2, team2, runs=44, balls=30, out=True)
    recompute.recompute_match(match2)

    with transaction.atomic():
        _merge(str(winner.id), str(loser.id))

    before = FantasyCalcAudit.objects.filter(match_id=match2.id).count()
    report = recompute.recompute_match(match2, trigger="merge")
    assert report.changed is False  # the merged rebuild is digest-identical
    assert FantasyCalcAudit.objects.filter(match_id=match2.id).count() == before
