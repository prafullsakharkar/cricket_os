"""Create the ``cricket`` schema and the extensions the schema relies on.

This migration creates **no tables** — Phase A has no domain models. It only
establishes the namespace (``cricket``) and the ``pg_trgm`` / ``btree_gin``
extensions that later phases' indexes depend on. Every statement is idempotent
so the migration is safe to re-run; the reverse is a no-op because dropping the
schema would destroy data.
"""

from __future__ import annotations

from django.db import migrations

SETUP_SQL = [
    "CREATE SCHEMA IF NOT EXISTS cricket;",
    "CREATE EXTENSION IF NOT EXISTS pg_trgm;",
    "CREATE EXTENSION IF NOT EXISTS btree_gin;",
]


class Migration(migrations.Migration):
    initial = True

    dependencies: list[tuple[str, str]] = []

    operations = [
        migrations.RunSQL(
            sql=SETUP_SQL,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
