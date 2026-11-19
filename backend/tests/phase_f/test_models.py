"""Phase F models: table structure, expression uniques, CHECK constraint,
reversibility — matching DATABASE_SCHEMA §3.7 exactly (plus the brief's
watermark/labelling columns recorded in the Phase F ledger, decision 3)."""

from __future__ import annotations

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.django_db

PHASE_F_APPS = ["statistics", "rankings"]

#: Leaf migration node per Phase F app (filled when the migrations exist).
LEAVES = [
    ("statistics", "0002_phase_f_aggregates"),
    ("rankings", "0001_initial"),
]

#: Table -> exact column set (id + doc DDL columns + added watermark columns).
TABLES: dict[str, set[str]] = {
    "player_career_stat": {
        "id",
        "player_id",
        "scope_type",
        "scope_ref_id",
        "format_type",
        "gender",
        "matches",
        "innings",
        "not_outs",
        "runs",
        "balls_faced",
        "high_score",
        "high_score_not_out",
        "fifties",
        "hundreds",
        "fours",
        "sixes",
        "batting_avg",
        "strike_rate",
        "catches",
        "stumpings",
        "balls_bowled",
        "runs_conceded",
        "wickets",
        "maidens",
        "best_wickets",
        "best_runs",
        "five_wicket_hauls",
        "bowling_avg",
        "economy",
        "bowling_sr",
        "revision",
        "computed_at",
        "rule_version",
        "inputs_digest",
        "source_kind",
        "matches_counted",
        "partial_matches",
        "first_match_date",
        "last_match_date",
        "is_partial",
    },
    "team_stat": {
        "id",
        "team_id",
        "scope_type",
        "scope_ref_id",
        "format_type",
        "matches",
        "won",
        "lost",
        "tied",
        "drawn",
        "no_result",
        "runs_scored",
        "runs_conceded",
        "wickets_taken",
        "nrr",
        "revision",
        "computed_at",
        "rule_version",
        "inputs_digest",
        "source_kind",
        "matches_counted",
        "partial_matches",
        "first_match_date",
        "last_match_date",
        "is_partial",
    },
    "head_to_head": {
        "id",
        "team_low_id",
        "team_high_id",
        "format_type",
        "matches",
        "low_wins",
        "high_wins",
        "ties",
        "draws",
        "no_results",
        "last_match_id",
        "computed_at",
        "rule_version",
        "inputs_digest",
        "source_kind",
        "matches_counted",
        "partial_matches",
        "first_match_date",
        "last_match_date",
        "is_partial",
    },
    "player_ranking": {
        "id",
        "player_id",
        "team_id",
        "gender",
        "format_type",
        "category",
        "rank",
        "rating",
        "points",
        "trend",
        "best_rank",
        "best_rating",
        "as_of_date",
        "source_provider_id",
    },
    "leaderboard": {
        "id",
        "scope_type",
        "scope_ref_id",
        "metric",
        "format_type",
        "rank",
        "player_id",
        "team_id",
        "value",
        "supporting",
        "computed_at",
        "rule_version",
        "inputs_digest",
        "source_kind",
        "matches_counted",
        "partial_matches",
        "first_match_date",
        "last_match_date",
        "is_partial",
    },
    "form_trend": {
        "id",
        "player_id",
        "format_type",
        "window_size",
        "metric",
        "value",
        "series",
        "computed_at",
        "rule_version",
        "inputs_digest",
        "source_kind",
        "matches_counted",
        "partial_matches",
        "first_match_date",
        "last_match_date",
        "is_partial",
    },
    "tournament_standings": {
        "id",
        "series_id",
        "stage_id",
        "team_id",
        "group_name",
        "rank",
        "matches_played",
        "matches_won",
        "matches_lost",
        "matches_tied",
        "matches_drawn",
        "no_result",
        "points",
        "nrr",
        "form",
        "qualify_status",
        "no_of_qual",
        "revision",
        "computed_at",
        "rule_version",
        "inputs_digest",
        "source_kind",
        "matches_counted",
        "partial_matches",
        "first_match_date",
        "last_match_date",
        "is_partial",
    },
    "statistics_watermark": {
        "id",
        "match_id",
        "source_digest",
        "aggregate_keys",
        "status",
        "recomputed_at",
    },
}

#: Unique indexes that must exist (DATABASE_SCHEMA §3.7 expression uniques).
UNIQUE_INDEXES: dict[str, str] = {
    "ux_career_scope": "player_career_stat",
    "ux_team_stat_scope": "team_stat",
    "ux_h2h": "head_to_head",
    "ux_ranking": "player_ranking",
    "ux_leaderboard": "leaderboard",
    "ux_form_trend": "form_trend",
    "ux_standings": "tournament_standings",
    "ux_watermark": "statistics_watermark",
}

#: Plain lookup indexes (doc DDL).
PLAIN_INDEXES: dict[str, str] = {
    "ix_ranking_lookup": "player_ranking",
}


def _executor() -> MigrationExecutor:
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    return executor


def _migrate(targets: list[tuple[str, str | None]]) -> None:
    _executor().migrate(targets)


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


def test_phase_f_aggregate_tables_exist_with_exact_columns() -> None:
    with connection.cursor() as cursor:
        for table, expected in TABLES.items():
            assert _table_exists(cursor, table), f"table {table} missing"
            assert _columns(cursor, table) == expected, f"column drift on {table}"


def test_expression_unique_indexes_exist() -> None:
    with connection.cursor() as cursor:
        for name in UNIQUE_INDEXES:
            assert _index_exists(cursor, name), f"unique index {name} missing"
        for name in PLAIN_INDEXES:
            assert _index_exists(cursor, name), f"index {name} missing"


def test_h2h_order_check_constraint_exists() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM pg_constraint c "
            "JOIN pg_class t ON t.oid = c.conrelid "
            "JOIN pg_namespace n ON n.oid = t.relnamespace "
            "WHERE n.nspname = 'cricket' AND t.relname = 'head_to_head' "
            "AND c.conname = 'ck_h2h_order' AND c.contype = 'c'"
        )
        assert cursor.fetchone() is not None, "ck_h2h_order missing"


def test_phase_f_tables_reverse_cleanly() -> None:
    """Unapplying the Phase F apps drops the tables; re-applying restores them."""
    executor = _executor()
    assert all(node in executor.loader.applied_migrations for node in LEAVES)

    _migrate([(app, None) for app in PHASE_F_APPS])
    with connection.cursor() as cursor:
        for table in TABLES:
            assert not _table_exists(cursor, table), f"table {table} still exists"

    _migrate(LEAVES)
    with connection.cursor() as cursor:
        for table in TABLES:
            assert _table_exists(cursor, table), f"table {table} missing after reapply"


def test_model_field_defaults() -> None:
    """The brief's watermark columns default to sane values on the models."""
    from apps.statistics.models import PlayerCareerStat

    row = PlayerCareerStat(player_id=None)  # type: ignore[arg-type]
    assert row.revision == 1
    assert row.source_kind == "computed"
    assert row.matches_counted == 0
    assert row.partial_matches == 0
    assert row.is_partial is False
