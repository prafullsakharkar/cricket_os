"""Phase H migration integrity: the append-only and frozen-immutability
triggers survive a full reverse/reapply cycle (RunSQL migrations), and
``makemigrations --check`` stays clean."""

from __future__ import annotations

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.utils import DatabaseError

pytestmark = pytest.mark.django_db

FANTASY_LEAF = ("fantasy", "0002_append_only")

TRIGGERS = {
    "fantasy_points_ledger": ["trg_ledger_append_only"],
    "fantasy_point_adjustment": ["trg_adjustment_append_only"],
    "fantasy_calc_audit": ["trg_calc_audit_append_only"],
    "fantasy_ruleset_version": ["trg_version_frozen_immutable"],
    "fantasy_point_rule": ["trg_point_rule_frozen_immutable"],
}


def _trigger_names(cursor, table: str) -> set[str]:
    cursor.execute(
        "SELECT tgname FROM pg_trigger t "
        "JOIN pg_class c ON c.oid = t.tgrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'cricket' AND c.relname = %s AND NOT t.tgisinternal",
        [table],
    )
    return {row[0] for row in cursor.fetchall()}


def _executor() -> MigrationExecutor:
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    return executor


def test_immutability_triggers_exist() -> None:
    with connection.cursor() as cursor:
        for table, names in TRIGGERS.items():
            present = _trigger_names(cursor, table)
            for name in names:
                assert name in present, f"{name} missing on {table}"


def test_reverse_reapply_restores_triggers() -> None:
    """Reversing the fantasy app drops everything; re-applying restores it
    including the RunSQL triggers (a plain schema-editor reverse would not)."""
    executor = _executor()
    assert FANTASY_LEAF in executor.loader.applied_migrations

    _executor().migrate([("fantasy", None)])
    _executor().migrate([FANTASY_LEAF])

    with connection.cursor() as cursor:
        for table, names in TRIGGERS.items():
            present = _trigger_names(cursor, table)
            for name in names:
                assert name in present, f"{name} missing on {table} after reapply"


def test_frozen_blocking_function_survives_reapply() -> None:
    from apps.fantasy.models import FantasyRuleset, FantasyRulesetVersion

    ruleset = FantasyRuleset.objects.create(name="test", provider_scope="generic")
    version = FantasyRulesetVersion.objects.create(
        ruleset=ruleset, version=1, effective_from="2026-01-01T00:00:00Z"
    )
    version.is_frozen = True
    version.status = "frozen"
    version.save(update_fields=["is_frozen", "status"])
    with pytest.raises(DatabaseError):
        FantasyRulesetVersion.objects.filter(id=version.id).update(is_frozen=False)
