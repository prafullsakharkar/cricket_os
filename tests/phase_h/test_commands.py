"""``fantasy_backfill`` / ``fantasy_verify`` commands (Task 4).

Backfill: resumable (idempotent), safe to run twice, dry-run writes nothing,
scoped by ids/since. Verify: a clean ledger exits 0; a corrupted ledger or a
tampered audit (detected by recomputing from the stored snapshot and by
summing the current ledger rows) exits nonzero with a report.
"""

from __future__ import annotations

from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.fantasy.models import FantasyCalcAudit, FantasyPointsLedger, PlayerMatchPerformance
from apps.fantasy.services import recompute
from apps.fantasy.services.snapshot import build_snapshot, snapshot_payload

from .factories import add_stat, make_completed_match, make_ruleset_version

pytestmark = pytest.mark.django_db


def _run(*args: str) -> str:
    out = StringIO()
    call_command(*args, stdout=out)
    return out.getvalue()


def test_backfill_dry_run_reports_without_writing() -> None:
    match, team1, _ = make_completed_match()
    add_stat(match, team1, runs=58, balls=42, out=True)
    _, _version = make_ruleset_version()

    output = _run("fantasy_backfill", "all", "--dry-run")

    assert "would recompute 1 matches" in output
    assert not FantasyCalcAudit.objects.filter(match_id=match.id).exists()
    assert not FantasyPointsLedger.objects.filter(match_id=match.id).exists()


def test_backfill_computes_and_is_safe_to_run_twice() -> None:
    match, team1, _ = make_completed_match()
    _player, _ = add_stat(match, team1, runs=58, balls=42, out=True)
    _, _version = make_ruleset_version()

    _run("fantasy_backfill", "all")

    assert FantasyCalcAudit.objects.filter(match_id=match.id).count() == 1
    assert PlayerMatchPerformance.objects.get(match_id=match.id).total_points == Decimal("58.00")

    _run("fantasy_backfill", "all")
    assert FantasyCalcAudit.objects.filter(match_id=match.id).count() == 1
    assert FantasyPointsLedger.objects.filter(match_id=match.id).count() == 1


def test_backfill_resumes_after_a_crash() -> None:
    """A match whose run is missing gets healed by a later backfill."""
    match1, t1, _ = make_completed_match()
    match2, t2, _ = make_completed_match(format_type="ODI")
    add_stat(match1, t1, runs=10, balls=6)
    add_stat(match2, t2, runs=20, balls=12)
    _, _version = make_ruleset_version()

    _run("fantasy_backfill", "match", "--ids", str(match1.id))
    assert FantasyCalcAudit.objects.filter(match_id=match1.id).exists()
    assert not FantasyCalcAudit.objects.filter(match_id=match2.id).exists()

    _run("fantasy_backfill", "all")
    assert FantasyCalcAudit.objects.filter(match_id=match2.id).exists()


def test_verify_clean_ledger_exits_zero() -> None:
    match, team1, _ = make_completed_match()
    add_stat(match, team1, runs=58, balls=42, out=True)
    make_ruleset_version()
    recompute.recompute_match(match)

    output = _run("fantasy_verify", "all")

    assert "OK" in output


def test_verify_detects_corrupted_ledger() -> None:
    match, team1, _ = make_completed_match()
    player, _ = add_stat(match, team1, runs=58, balls=42, out=True)
    _, version = make_ruleset_version()
    recompute.recompute_match(match)

    audit = (
        FantasyCalcAudit.objects.filter(match_id=match.id).order_by("-computed_at", "-id").first()
    )
    # Tampering simulated at write time (INSERT is the only legal mutation):
    # a bogus current row for the same run with a wrong delta.
    FantasyPointsLedger.objects.create(
        match_id=match.id,
        player_id=player.id,
        ruleset_version_id=version.id,
        delta=Decimal("999.00"),
        event_type="computed",
        calc_run_id=audit.id,
    )

    with pytest.raises(CommandError) as excinfo:
        _run("fantasy_verify", "all")
    assert "ledger" in str(excinfo.value)


def test_verify_detects_corrupted_audit_snapshot() -> None:
    match, team1, _ = make_completed_match()
    add_stat(match, team1, runs=58, balls=42, out=True)
    _, version = make_ruleset_version()
    recompute.recompute_match(match)

    # A second audit whose stored snapshot belongs to a *different* match and
    # whose output digest does not reproduce from that snapshot.
    other, team2, _ = make_completed_match(format_type="ODI")
    add_stat(other, team2, runs=5, balls=4)
    other_snapshot = build_snapshot(other)
    FantasyCalcAudit.objects.create(
        match_id=match.id,
        ruleset_version_id=version.id,
        input_digest=other_snapshot.digest,
        output_digest="deadbeef",
        engine_version="1.0.0",
        snapshot=snapshot_payload(other_snapshot),
        snapshot_digest=other_snapshot.digest,
        trigger="tampered",
        version_hash="deadbeef",
    )

    with pytest.raises(CommandError) as excinfo:
        _run("fantasy_verify", "all")
    assert "audit" in str(excinfo.value)
