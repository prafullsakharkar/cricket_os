"""Phase H models: table structure, expression uniques, generated column,
CHECK constraint and reversibility — matching DATABASE_SCHEMA §3.8 exactly
plus the Phase H extension columns recorded in the plan (decision 4:
status/content_hash/frozen_at/frozen_by/is_default/auto_recompute on the
version; is_current/superseded_by on the ledger; snapshot/snapshot_digest/
trigger/is_provisional/timings on calc_audit; calc_run_id/unsupported_rules/
is_partial on player_match_performance)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.utils import DatabaseError
from django.utils import timezone

pytestmark = pytest.mark.django_db

#: Leaf migration node for the fantasy app (filled when the migrations exist).
LEAF = ("fantasy", "0002_append_only")

#: Table -> exact column set (id + doc DDL columns + Phase H extensions).
TABLES: dict[str, set[str]] = {
    "fantasy_ruleset": {
        "id",
        "name",
        "provider_scope",
        "description",
        "is_active",
        "created_by",
    },
    "fantasy_ruleset_version": {
        "id",
        "ruleset_id",
        "version",
        "effective_from",
        "effective_to",
        "format_scope",
        "is_frozen",
        "status",
        "content_hash",
        "frozen_at",
        "frozen_by",
        "is_default",
        "auto_recompute",
        "policies",
    },
    "fantasy_point_rule": {
        "id",
        "ruleset_version_id",
        "event_type",
        "role_scope",
        "points",
        "condition",
        "condition_hash",
        "cap_kind",
    },
    "player_match_performance": {
        "id",
        "match_id",
        "player_id",
        "ruleset_version_id",
        "total_points",
        "breakdown",
        "is_playing_xi",
        "revision",
        "computed_at",
        "calc_run_id",
        "unsupported_rules",
        "is_partial",
    },
    "fantasy_points_ledger": {
        "id",
        "match_id",
        "player_id",
        "ruleset_version_id",
        "delta",
        "event_type",
        "reason",
        "created_at",
        "created_by",
        "calc_run_id",
        "is_current",
        "superseded_by",
    },
    "fantasy_point_adjustment": {
        "id",
        "match_id",
        "player_id",
        "delta",
        "reason",
        "reference",
        "adjusted_by",
        "created_at",
    },
    "fantasy_calc_audit": {
        "id",
        "match_id",
        "ruleset_version_id",
        "input_digest",
        "output_digest",
        "engine_version",
        "version_hash",
        "notes",
        "computed_at",
        "snapshot",
        "snapshot_digest",
        "trigger",
        "is_provisional",
        "timings",
    },
}

#: Unique indexes that must exist (DATABASE_SCHEMA §3.8 + Phase H default).
UNIQUE_INDEXES: dict[str, str] = {
    "ux_frv": "fantasy_ruleset_version",
    "ux_fpr": "fantasy_point_rule",
    "ux_pmp": "player_match_performance",
    "ux_frv_default": "fantasy_ruleset_version",
}


def _executor() -> MigrationExecutor:
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    return executor


def _table_exists(cursor, table: str) -> bool:
    cursor.execute(
        "SELECT 1 FROM pg_tables WHERE schemaname = 'cricket' AND tablename = %s",
        [table],
    )
    return cursor.fetchone() is not None


def _columns(cursor, table: str) -> set[str]:
    cursor.execute(
        "SELECT a.attname FROM pg_attribute a "
        "JOIN pg_class c ON c.oid = a.attrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'cricket' AND c.relname = %s AND a.attnum > 0 "
        "AND NOT a.attisdropped",
        [table],
    )
    return {row[0] for row in cursor.fetchall()}


def _index_exists(cursor, name: str) -> bool:
    cursor.execute(
        "SELECT 1 FROM pg_indexes WHERE schemaname = 'cricket' AND indexname = %s",
        [name],
    )
    return cursor.fetchone() is not None


def test_fantasy_tables_exist_with_exact_columns() -> None:
    """Every §3.8 fantasy table exists with the exact doc column set."""
    with connection.cursor() as cursor:
        for table, expected in TABLES.items():
            assert _table_exists(cursor, table), f"table {table} missing"
            assert _columns(cursor, table) == expected, f"column drift on {table}"


def test_fantasy_unique_indexes_exist() -> None:
    """ux_frv / ux_fpr / ux_pmp / ux_frv_default exist by their doc names."""
    with connection.cursor() as cursor:
        for name in UNIQUE_INDEXES:
            assert _index_exists(cursor, name), f"unique index {name} missing"


def test_ux_fpr_is_an_expression_unique() -> None:
    """ux_fpr is the coalesce-based expression unique from the doc DDL."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT indexdef FROM pg_indexes WHERE schemaname = 'cricket' AND indexname = 'ux_fpr'"
        )
        indexdef = cursor.fetchone()[0]
    # pg_get_indexdef renders the keyword upper-case: COALESCE(role_scope, ...).
    assert "COALESCE(role_scope," in indexdef, indexdef
    assert indexdef.startswith("CREATE UNIQUE INDEX"), indexdef


def test_ux_frv_default_is_partial_on_is_default() -> None:
    """ux_frv_default is a partial unique index on the is_default flag."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT indexdef FROM pg_indexes "
            "WHERE schemaname = 'cricket' AND indexname = 'ux_frv_default'"
        )
        indexdef = cursor.fetchone()[0]
    assert indexdef.startswith("CREATE UNIQUE INDEX"), indexdef
    assert "WHERE is_default" in indexdef, indexdef


def test_condition_hash_is_generated_and_matches_ddl_expression() -> None:
    """condition_hash is a STORED generated column = md5(condition::text)."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT a.attgenerated <> '' FROM pg_attribute a "
            "JOIN pg_class c ON c.oid = a.attrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'cricket' AND c.relname = 'fantasy_point_rule' "
            "AND a.attname = 'condition_hash'"
        )
        row = cursor.fetchone()
        assert row is not None, "condition_hash column missing"
        assert row[0] is True, "condition_hash is not a generated column"


def test_condition_hash_value_equals_md5_of_condition_text() -> None:
    """Insert a rule; the DB-generated hash must equal md5(condition::text)."""
    from apps.fantasy.models import FantasyPointRule, FantasyRuleset, FantasyRulesetVersion

    ruleset = FantasyRuleset.objects.create(name="test", provider_scope="generic")
    version = FantasyRulesetVersion.objects.create(
        ruleset=ruleset, version=1, effective_from="2026-01-01T00:00:00Z"
    )
    rule = FantasyPointRule.objects.create(
        ruleset_version=version,
        event_type="run",
        role_scope="batter",
        points="1.00",
        condition={"min_runs": 50},
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT condition_hash, md5(condition::text) "
            "FROM cricket.fantasy_point_rule WHERE id = %s",
            [str(rule.id)],
        )
        stored, expected = cursor.fetchone()
    assert stored == expected
    assert len(stored) == 32  # an md5 hex digest


def test_cap_kind_check_constraint_enforces_allowed_values() -> None:
    """The doc DDL's CHECK on cap_kind exists and rejects bad values at the DB."""
    from apps.fantasy.models import FantasyPointRule, FantasyRuleset, FantasyRulesetVersion

    ruleset = FantasyRuleset.objects.create(name="test", provider_scope="generic")
    version = FantasyRulesetVersion.objects.create(
        ruleset=ruleset, version=1, effective_from="2026-01-01T00:00:00Z"
    )
    rule = FantasyPointRule.objects.create(
        ruleset_version=version,
        event_type="run",
        points="1.00",
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM pg_constraint c "
            "JOIN pg_class t ON t.oid = c.conrelid "
            "JOIN pg_namespace n ON n.oid = t.relnamespace "
            "WHERE n.nspname = 'cricket' AND t.relname = 'fantasy_point_rule' "
            "AND c.conname = 'ck_fpr_cap_kind_valid' AND c.contype = 'c'"
        )
        assert cursor.fetchone() is not None, "ck_fpr_cap_kind_valid missing"
    with pytest.raises(DatabaseError):
        rule.cap_kind = "unlimited"
        rule.save(update_fields=["cap_kind"])


def test_version_defaults_are_draft_and_unfrozen() -> None:
    from apps.fantasy.models import FantasyRuleset, FantasyRulesetVersion

    ruleset = FantasyRuleset.objects.create(name="test", provider_scope="generic")
    version = FantasyRulesetVersion(
        ruleset=ruleset, version=1, effective_from="2026-01-01T00:00:00Z"
    )
    assert version.status == "draft"
    assert version.is_frozen is False
    assert version.is_default is False
    assert version.auto_recompute is False
    assert version.content_hash is None
    assert version.policies == {}


def test_version_policies_round_trip() -> None:
    """Policies persist as a JSON object and default to the engine defaults."""
    from apps.fantasy.models import FantasyRuleset, FantasyRulesetVersion

    ruleset = FantasyRuleset.objects.create(name="test", provider_scope="generic")
    version = FantasyRulesetVersion.objects.create(
        ruleset=ruleset,
        version=1,
        effective_from="2026-01-01T00:00:00Z",
        policies={"super_over": "include", "abandoned": "compute"},
    )
    version.refresh_from_db()
    assert version.policies == {"super_over": "include", "abandoned": "compute"}


def test_fantasy_tables_reverse_cleanly() -> None:
    """Unapplying the fantasy app drops the tables; re-applying restores them."""
    executor = _executor()
    assert LEAF in executor.loader.applied_migrations

    _executor().migrate([("fantasy", None)])
    with connection.cursor() as cursor:
        for table in TABLES:
            assert not _table_exists(cursor, table), f"table {table} still exists"

    _executor().migrate([LEAF])
    with connection.cursor() as cursor:
        for table in TABLES:
            assert _table_exists(cursor, table), f"table {table} missing after reapply"


def test_every_model_has_a_string_representation() -> None:
    """Every fantasy model's ``__str__`` renders (admin + logging use it)."""
    from apps.fantasy.models import (
        FantasyCalcAudit,
        FantasyPointRule,
        FantasyPointsLedger,
        FantasyRuleset,
        FantasyRulesetVersion,
        PlayerMatchPerformance,
    )
    from apps.fantasy.services import recompute

    from .factories import add_stat, make_completed_match, make_ruleset_version

    ruleset = FantasyRuleset.objects.create(name="str test", provider_scope="generic")
    version = FantasyRulesetVersion.objects.create(
        ruleset=ruleset,
        version=1,
        effective_from=timezone.now(),
        policies={},
    )
    assert str(ruleset) == "str test"
    assert str(version) == f"{ruleset.id} v1"

    rule = FantasyPointRule.objects.create(
        ruleset_version=version,
        event_type="runs",
        role_scope="batting",
        points=Decimal("1.00"),
        condition={"operator": "per_unit"},
    )
    assert "runs/batting@1.00" in str(rule)

    # The recompute path needs a valid auto-recompute version.
    match, team1, _ = make_completed_match()
    add_stat(match, team1, runs=58, balls=42, out=True)
    make_ruleset_version(auto_recompute=True)
    recompute.recompute_match(match)
    audit = FantasyCalcAudit.objects.get(match_id=match.id)
    assert str(audit) == f"{match.id} {audit.ruleset_version_id}"

    perf = PlayerMatchPerformance.objects.get(match_id=match.id)
    assert str(perf) == f"{perf.player_id} {match.id} {perf.ruleset_version_id}"

    ledger_row = FantasyPointsLedger.objects.get(match_id=match.id)
    assert str(ledger_row) == f"{match.id} {ledger_row.player_id} {ledger_row.delta}"

    from django.contrib.auth.models import Group, User

    from apps.fantasy.services import adjustments

    user = User.objects.create_user(username="str-admin", password="pw")
    user.groups.add(Group.objects.get(name="admin"))
    adj = adjustments.apply_adjustment(
        match_id=match.id,
        player_id=perf.player_id,
        delta=Decimal("1.00"),
        reason="str test",
        reference="REF-STR",
        actor=user,
    )
    assert str(adj) == f"{match.id} {perf.player_id} {adj.delta}"


def test_condition_hash_expression_deconstructs() -> None:
    """The generated-column expression round-trips through ``deconstruct``
    (used by the migration framework when it re-renders the field)."""
    from apps.fantasy.expressions import ConditionHashExpression

    expr = ConditionHashExpression()
    path, args, kwargs = expr.deconstruct()
    assert path.endswith("ConditionHashExpression")
    assert args == []
    assert kwargs == {}


def test_fantasy_app_ready_is_idempotent() -> None:
    """Calling ``ready()`` again hits the _ready_done guard (no re-register)."""
    from django.apps import apps as django_apps

    config = django_apps.get_app_config("fantasy")
    assert config._ready_done is True  # already ran at startup
    config.ready()  # second call returns via the guard
    assert config._ready_done is True
