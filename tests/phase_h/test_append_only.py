"""Append-only + frozen-immutability enforcement (Task 1, plan decision 1).

The ledger, adjustments and calc audits are append-only: UPDATE and DELETE are
blocked by DB triggers for EVERY role (the app connects as the table owner /
superuser in this repo, so only trigger-level enforcement is meaningful — Phase
G's REVOKE-based role enforcement does not exist yet). Frozen ruleset versions
and their point rules are immutable at the database level too: no UPDATE,
DELETE, and no INSERT of a new rule once the parent version is frozen.

Every blocked call runs inside its own ``transaction.atomic()`` savepoint so a
trigger RAISE (which aborts the surrounding transaction) rolls back to the
savepoint and the test's outer transaction stays healthy.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from django.db import connection, transaction
from django.db.utils import DatabaseError

from apps.fantasy.models import (
    FantasyCalcAudit,
    FantasyPointAdjustment,
    FantasyPointRule,
    FantasyPointsLedger,
    FantasyRuleset,
    FantasyRulesetVersion,
)

pytestmark = pytest.mark.django_db


def _assert_blocked(call: Callable[[], Any]) -> None:
    """Assert ``call`` raises a DatabaseError, keeping the test transaction usable."""
    with pytest.raises(DatabaseError), transaction.atomic():
        call()


def _make_ruleset_and_version(frozen: bool = False) -> tuple[FantasyRuleset, FantasyRulesetVersion]:
    ruleset = FantasyRuleset.objects.create(name="test", provider_scope="generic")
    version = FantasyRulesetVersion.objects.create(
        ruleset=ruleset, version=1, effective_from="2026-01-01T00:00:00Z"
    )
    if frozen:
        version.is_frozen = True
        version.status = "frozen"
        version.save(update_fields=["is_frozen", "status"])
    return ruleset, version


def _make_rule(version: FantasyRulesetVersion, event_type: str = "run") -> FantasyPointRule:
    return FantasyPointRule.objects.create(
        ruleset_version=version,
        event_type=event_type,
        role_scope="batter",
        points="1.00",
        condition=None,
    )


def _freeze(version: FantasyRulesetVersion) -> None:
    version.is_frozen = True
    version.status = "frozen"
    version.save(update_fields=["is_frozen", "status"])


def _make_ledger_row() -> FantasyPointsLedger:
    return FantasyPointsLedger.objects.create(
        match_id="00000000-0000-0000-0000-000000000001",
        player_id="00000000-0000-0000-0000-000000000002",
        ruleset_version_id="00000000-0000-0000-0000-000000000003",
        delta="5.00",
        event_type="run",
    )


def test_ledger_rejects_update() -> None:
    row = _make_ledger_row()
    _assert_blocked(lambda: FantasyPointsLedger.objects.filter(id=row.id).update(delta="9.00"))


def test_ledger_rejects_delete() -> None:
    row = _make_ledger_row()
    _assert_blocked(lambda: row.delete())


def test_ledger_rejects_raw_sql_update_as_admin_role() -> None:
    """The trigger fires even for a superuser / table-owner connection."""
    row = _make_ledger_row()

    def raw_update() -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE cricket.fantasy_points_ledger SET delta = 9.00 WHERE id = %s",
                [str(row.id)],
            )

    _assert_blocked(raw_update)
    row.refresh_from_db()
    assert row.delta == Decimal("5.00")


def test_ledger_rejects_raw_sql_delete() -> None:
    row = _make_ledger_row()

    def raw_delete() -> None:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM cricket.fantasy_points_ledger WHERE id = %s", [str(row.id)])

    _assert_blocked(raw_delete)
    row.refresh_from_db()
    assert row.is_current is True


def test_adjustment_rejects_update_and_delete() -> None:
    adj = FantasyPointAdjustment.objects.create(
        match_id="00000000-0000-0000-0000-000000000001",
        player_id="00000000-0000-0000-0000-000000000002",
        delta="2.00",
        reason="provider correction",
    )
    _assert_blocked(lambda: FantasyPointAdjustment.objects.filter(id=adj.id).update(delta="3.00"))
    _assert_blocked(lambda: adj.delete())


def test_calc_audit_rejects_update_and_delete() -> None:
    audit = FantasyCalcAudit.objects.create(
        match_id="00000000-0000-0000-0000-000000000001",
        ruleset_version_id="00000000-0000-0000-0000-000000000003",
        input_digest="a" * 64,
        output_digest="b" * 64,
        engine_version="1.0.0",
    )
    _assert_blocked(
        lambda: FantasyCalcAudit.objects.filter(id=audit.id).update(output_digest="c" * 64)
    )
    _assert_blocked(lambda: audit.delete())


def test_frozen_version_rejects_update_and_delete() -> None:
    _, version = _make_ruleset_and_version(frozen=True)
    _assert_blocked(
        lambda: FantasyRulesetVersion.objects.filter(id=version.id).update(is_frozen=False)
    )
    _assert_blocked(lambda: version.delete())


def test_frozen_version_rules_reject_update_delete_and_insert() -> None:
    _, version = _make_ruleset_and_version(frozen=False)
    rule = _make_rule(version)
    _freeze(version)
    _assert_blocked(lambda: FantasyPointRule.objects.filter(id=rule.id).update(points="9.00"))
    _assert_blocked(lambda: rule.delete())
    _assert_blocked(lambda: _make_rule(version, event_type="six"))


def test_cannot_create_rule_against_already_frozen_version() -> None:
    _, version = _make_ruleset_and_version(frozen=True)
    _assert_blocked(lambda: _make_rule(version))


def test_draft_version_stays_mutable() -> None:
    _, version = _make_ruleset_and_version(frozen=False)
    rule = _make_rule(version)
    # Draft versions may be edited and deleted; their rules too.
    FantasyRulesetVersion.objects.filter(id=version.id).update(
        effective_from="2026-02-01T00:00:00Z"
    )
    FantasyPointRule.objects.filter(id=rule.id).update(points="2.00")
    rule.delete()
    version.delete()


def test_freeze_transition_is_the_only_way_to_mutate_status() -> None:
    """A draft can transition draft->frozen once; the second write is blocked."""
    _, version = _make_ruleset_and_version(frozen=False)
    # draft -> frozen is allowed (OLD.is_frozen is False).
    _freeze(version)
    version.refresh_from_db()
    assert version.status == "frozen"
    # Any later write is blocked.
    _assert_blocked(
        lambda: FantasyRulesetVersion.objects.filter(id=version.id).update(is_frozen=False)
    )


def test_unfrozen_version_rules_insert_allowed() -> None:
    _, version = _make_ruleset_and_version(frozen=False)
    rule = _make_rule(version)
    assert rule.condition_hash is None or len(rule.condition_hash) == 32
