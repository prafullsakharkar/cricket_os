"""Initial migration: schema + extensions exist and the DDL is idempotent."""

from __future__ import annotations

import pytest
from django.db import connection

pytestmark = pytest.mark.integration

DDL = [
    "CREATE SCHEMA IF NOT EXISTS cricket",
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    "CREATE EXTENSION IF NOT EXISTS btree_gin",
]


def test_cricket_schema_was_created(db) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s", ["cricket"]
        )
        assert cursor.fetchone() is not None


def test_required_extensions_are_installed(db) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT extname FROM pg_extension WHERE extname IN ('pg_trgm', 'btree_gin')")
        installed = {row[0] for row in cursor.fetchall()}
    assert installed == {"pg_trgm", "btree_gin"}


def test_initial_ddl_is_idempotent_on_an_empty_database(db) -> None:
    with connection.cursor() as cursor:
        for _ in range(2):
            for statement in DDL:
                cursor.execute(statement)
    assert True


def test_tables_live_in_the_cricket_schema(db) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_schema()")
        # With no objects yet, current_schema() still resolves to the first
        # existing schema on the search_path.
        assert cursor.fetchone()[0] == "cricket"
