"""Recompute/verify/event edge branches (Task 10 gate).

The golden pipeline tests cover the happy path; these cover the defensive and
edge branches in ``services/recompute.py``: a stored rule with no operator, a
match with no applicable version, scope validation in ``_matches_in_scope``,
the ``--since`` filter, the sweep's partial-rate accounting, verify findings
(missing version / invalid version / no-snapshot run / un-replayable snapshot /
digest mismatch), and the event handlers' guard clauses.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.fantasy.engine import point_rule
from apps.fantasy.models import (
    FantasyCalcAudit,
    FantasyPointRule,
    PlayerMatchPerformance,
)
from apps.fantasy.services import recompute
from apps.fantasy.services.snapshot import build_snapshot, snapshot_payload

from .factories import add_stat, make_completed_match, make_ruleset_version

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------- #
# Pipeline edge branches
# --------------------------------------------------------------------------- #


def test_rule_without_operator_raises() -> None:
    """A stored rule whose condition JSON lacks ``operator`` fails loudly."""
    match, team1, _ = make_completed_match()
    add_stat(match, team1, runs=58, balls=42, out=True)
    _, version = make_ruleset_version()
    FantasyPointRule.objects.filter(ruleset_version=version).delete()
    FantasyPointRule.objects.create(
        ruleset_version=version,
        event_type="runs",
        role_scope="batting",
        points=Decimal("1.00"),
        condition={},  # no operator
    )

    with pytest.raises(ValueError):
        recompute.recompute_match(match)


def test_match_with_no_applicable_version_returns_empty_report() -> None:
    """All versions skipped (auto_recompute off) -> an empty merged report."""
    match, team1, _ = make_completed_match()
    add_stat(match, team1, runs=58, balls=42, out=True)
    make_ruleset_version(auto_recompute=False)

    report = recompute.recompute_match(match)

    assert report.changed is False
    assert report.versions_computed == ()
    assert not FantasyCalcAudit.objects.filter(match_id=match.id).exists()


def test_scope_requires_ids_for_match_player_series() -> None:
    for scope in ("match", "player", "series"):
        with pytest.raises(ValueError):
            recompute.backfill_matches(scope, ids=None)


def test_unknown_scope_rejected() -> None:
    with pytest.raises(ValueError):
        recompute.backfill_matches("bogus")


def test_backfill_since_filter_excludes_older_matches() -> None:
    """``since`` is applied as ``source_local_date__gte`` (matches are dated
    2026-10-25 in the factory, so a 2030 boundary matches nothing)."""
    match, team1, _ = make_completed_match()
    add_stat(match, team1, runs=10, balls=6)
    make_ruleset_version()

    assert recompute.backfill_matches("all", since="2030-01-01") == 0
    assert recompute.backfill_matches("all", since="2020-01-01") == 1


def test_sweep_counts_partial_heals() -> None:
    """A summary-only match healed by the sweep is accounted in the partial
    rate (metrics side effect exercised by the observability suite)."""
    match, team1, _ = make_completed_match()
    add_stat(match, team1, runs=40, balls=30, out=True, is_partial=True)
    make_ruleset_version(
        rules=(
            point_rule("runs", "per_unit", "1.00", "batting"),
            point_rule("balls", "per_unit", "0.10", "batting"),
        ),
        auto_recompute=True,
    )

    healed = recompute.sweep_stale_fantasy()

    assert healed == 1
    perf = PlayerMatchPerformance.objects.get(match_id=match.id)
    assert perf.is_partial is True
    assert "balls" in perf.unsupported_rules


# --------------------------------------------------------------------------- #
# Event handler guards
# --------------------------------------------------------------------------- #


def test_recompute_event_missing_match_id_is_ignored() -> None:
    recompute._recompute_from_event({}, correction=False)  # no raise
    recompute._recompute_from_event({"match_id": ""}, correction=False)


def test_recompute_event_skips_non_completed_match() -> None:
    from apps.core.enums import MatchStatus
    from tests.phase_c.factories import make_match

    match = make_match(status=MatchStatus.IN_PROGRESS)
    recompute._recompute_from_event({"match_id": str(match.id)}, correction=False)
    assert not FantasyCalcAudit.objects.filter(match_id=match.id).exists()


def test_recompute_event_completed_match_computes() -> None:
    match, team1, _ = make_completed_match()
    add_stat(match, team1, runs=58, balls=42, out=True)
    make_ruleset_version(auto_recompute=True)

    recompute._recompute_from_event({"match_id": str(match.id)}, correction=False)

    assert FantasyCalcAudit.objects.filter(match_id=match.id).exists()


def test_player_merged_event_missing_ids_ignored() -> None:
    recompute._on_player_merged({})
    recompute._on_player_merged({"canonical_player_id": "w", "merged_player_id": ""})


def test_player_merged_skips_non_completed_matches() -> None:
    """A loser PMP on a not-yet-completed match is skipped, then the loser's
    read-model rows are removed regardless."""
    import uuid as uuid_mod

    match, team1, _ = make_completed_match()
    player, _ = add_stat(match, team1, runs=10, balls=6)
    _, version = make_ruleset_version(auto_recompute=True)
    recompute.recompute_match(match)
    # The loser already has a read-model row on the completed match.
    assert PlayerMatchPerformance.objects.filter(match_id=match.id, player_id=player.id).exists()

    # A non-completed match the loser "appears" in (no calc run).
    from apps.core.enums import MatchStatus
    from apps.matches.models import MatchPlayer
    from apps.statistics.models import PlayerMatchStat
    from tests.phase_c.factories import make_match as raw_match

    live = raw_match(status=MatchStatus.IN_PROGRESS)
    MatchPlayer.objects.create(
        match=live,
        player=player,
        team_id=str(team1.id),
        is_playing_xi=True,
    )
    PlayerMatchStat.objects.create(
        match=live,
        player=player,
        team_id=str(team1.id),
        did_bat=True,
        did_bowl=False,
        did_field=True,
        is_playing_xi=True,
        batting=[],
        bowling=[],
        fielding={},
        is_partial=False,
    )
    PlayerMatchPerformance.objects.create(
        match_id=live.id,
        player_id=player.id,
        ruleset_version_id=version.id,
        total_points=0,
        breakdown={},
    )

    recompute._on_player_merged(
        {"canonical_player_id": str(uuid_mod.uuid4()), "merged_player_id": str(player.id)}
    )

    # The completed match was recomputed under the *winner*; the loser's PMP
    # rows (both matches) are deleted.
    assert not PlayerMatchPerformance.objects.filter(player_id=player.id).exists()


# --------------------------------------------------------------------------- #
# Verify findings
# --------------------------------------------------------------------------- #


def _audit(match: object, version: object, **overrides: object) -> FantasyCalcAudit:
    return FantasyCalcAudit.objects.create(
        match_id=match.id,
        ruleset_version_id=version.id,
        engine_version="1.0.0",
        trigger="test",
        **overrides,
    )


def test_matches_in_scope_player_and_series_filters() -> None:
    """The player/series scopes filter by the respective FK even when nothing
    matches (no matches for a random id -> an empty list, no raise)."""
    import uuid as uuid_mod

    assert recompute._matches_in_scope("player", ids=[str(uuid_mod.uuid4())]) == []
    assert recompute._matches_in_scope("series", ids=[str(uuid_mod.uuid4())]) == []


def test_verify_is_clean_for_provisional_run() -> None:
    """A provisional run replays from its stored snapshot but skips the
    current-ledger check (provisional runs never write ledger rows)."""
    from django.test import override_settings

    match, team1, _ = make_completed_match()
    add_stat(match, team1, runs=58, balls=42, out=True)
    make_ruleset_version(auto_recompute=True)

    with override_settings(FANTASY_PROVISIONAL_ENABLED=True):
        recompute.recompute_match(match, provisional=True)

    audit = FantasyCalcAudit.objects.get(match_id=match.id)
    assert audit.is_provisional is True
    findings = recompute.verify_fantasy("all")
    assert findings == []


def test_verify_flags_audit_for_missing_version() -> None:
    import uuid as uuid_mod

    match, team1, _ = make_completed_match()
    add_stat(match, team1, runs=10, balls=6)
    make_ruleset_version()
    FantasyCalcAudit.objects.create(
        match_id=match.id,
        ruleset_version_id=uuid_mod.uuid4(),
        engine_version="1.0.0",
        trigger="test",
    )

    findings = recompute.verify_fantasy("all")

    assert any("references missing version" in f for f in findings)


def test_verify_flags_audit_for_invalid_version() -> None:
    match, team1, _ = make_completed_match()
    add_stat(match, team1, runs=58, balls=42, out=True)
    _, version = make_ruleset_version()
    FantasyPointRule.objects.filter(ruleset_version=version).delete()
    FantasyPointRule.objects.create(
        ruleset_version=version,
        event_type="runs",
        role_scope="batting",
        points=Decimal("1.00"),
        condition={"operator": "per_unit"},
    )
    FantasyPointRule.objects.create(
        ruleset_version=version,
        event_type="runs",
        role_scope="batting",
        points=Decimal("2.00"),
        condition={"operator": "capped", "cap_value": "10.00"},
    )
    _audit(match, version, input_digest="x", output_digest="y", snapshot=b"\x00")

    findings = recompute.verify_fantasy("all")

    assert any("invalid" in f for f in findings)


def test_verify_ignores_run_without_snapshot() -> None:
    """A (provisional-style) audit with no stored snapshot has nothing to
    replay and produces no finding."""
    match, team1, _ = make_completed_match()
    add_stat(match, team1, runs=58, balls=42, out=True)
    _, version = make_ruleset_version()
    _audit(match, version, snapshot=None)

    findings = recompute.verify_fantasy("all")

    assert findings == []


def test_verify_flags_unreplayable_snapshot() -> None:
    match, team1, _ = make_completed_match()
    add_stat(match, team1, runs=58, balls=42, out=True)
    _, version = make_ruleset_version()
    _audit(match, version, snapshot=b"not-gzip-data")

    findings = recompute.verify_fantasy("all")

    assert any("replay failed" in f for f in findings)


def test_verify_flags_snapshot_digest_mismatch() -> None:
    match, team1, _ = make_completed_match()
    add_stat(match, team1, runs=58, balls=42, out=True)
    _, version = make_ruleset_version()
    snapshot = build_snapshot(match)
    _audit(
        match,
        version,
        input_digest=snapshot.digest,
        output_digest=snapshot.digest,
        snapshot=snapshot_payload(snapshot),
        snapshot_digest="deadbeef",
    )

    findings = recompute.verify_fantasy("all")

    assert any("snapshot digest mismatch" in f for f in findings)
