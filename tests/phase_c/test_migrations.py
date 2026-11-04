"""Phase C migrations reverse cleanly (forward/backward), including the
partitioned ``delivery`` table created by hand-written RunSQL."""

from __future__ import annotations

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.django_db

PHASE_C_APPS = [
    "matches",
    "innings",
    "deliveries",
    "scorecards",
    "statistics",
    "ingestion",
    "synchronization",
]

#: Leaf migration node per Phase C app.
LEAVES = [
    ("matches", "0001_initial"),
    ("innings", "0002_initial"),
    ("deliveries", "0003_delivery_partitioned_table"),
    ("scorecards", "0001_initial"),
    ("statistics", "0001_initial"),
    ("ingestion", "0001_initial"),
    ("synchronization", "0001_initial"),
]

#: The 24 Phase C tables (ci/schema_drift_scope.txt additions).
PHASE_C_TABLES = [
    "match",
    "match_team",
    "toss",
    "match_player",
    "match_official",
    "match_result",
    "match_award",
    "innings",
    "innings_event",
    "innings_summary",
    "over",
    "delivery",
    "batting_entry",
    "bowling_entry",
    "fall_of_wicket",
    "partnership",
    "player_match_stat",
    "import_job",
    "ingestion_error",
    "sync_checkpoint",
    "data_correction",
]


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


def test_full_cycle_unapply_reapply_is_symmetric() -> None:
    """Unapplying the Phase C apps drops the tables; re-applying restores them."""
    executor = _executor()
    assert all(node in executor.loader.applied_migrations for node in LEAVES)

    _migrate([(app, None) for app in PHASE_C_APPS])

    with connection.cursor() as cursor:
        for table in PHASE_C_TABLES:
            assert not _table_exists(cursor, table), f"table {table} still exists"

    _migrate(LEAVES)
    with connection.cursor() as cursor:
        for table in PHASE_C_TABLES:
            assert _table_exists(cursor, table), f"table {table} missing"

    # The delivery table is still partitioned after the round-trip.
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_partitioned_table p "
            "JOIN pg_class c ON c.oid = p.partrelid "
            "WHERE c.relname = 'delivery' AND c.relnamespace = 'cricket'::regnamespace"
        )
        assert cursor.fetchone()[0] == 1


def test_delivery_partition_survives_parent_drop() -> None:
    """Dropping the parent drops its partitions (CASCADE), and re-creating the
    parent lets ensure_partition recreate them on demand."""
    from apps.deliveries.services import partitions

    partitions.ensure_partition(2030)
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('cricket.delivery_2030')")
        assert cursor.fetchone()[0] is not None

    _migrate([("deliveries", None)])
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('cricket.delivery_2030')")
        assert cursor.fetchone()[0] is None

    _migrate([("deliveries", "0003_delivery_partitioned_table")])
    partitions.ensure_partition(2030)
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('cricket.delivery_2030')")
        assert cursor.fetchone()[0] is not None
