"""App configuration for ``core``.

As well as registering the app, this ensures the dedicated PostgreSQL schema
exists on every new connection. Django creates ``django_migrations`` (and the
``auth``/``sessions``/``admin`` tables) *before* our initial migration runs, so
without this the migration bookkeeping would land in ``public`` rather than the
``cricket`` schema named in the connection's ``search_path``.
"""

from __future__ import annotations

from typing import Any

from django.apps import AppConfig
from django.db.backends.signals import connection_created

SCHEMA_SQL = "CREATE SCHEMA IF NOT EXISTS cricket"


def ensure_cricket_schema(sender: Any, connection: Any, **kwargs: Any) -> None:
    """Create the ``cricket`` schema if this is a PostgreSQL connection."""
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute(SCHEMA_SQL)


class CoreConfig(AppConfig):
    """Cross-cutting app: UUIDv7, base models, enums, errors, pagination."""

    name = "apps.core"
    label = "core"
    verbose_name = "Core"
    default_auto_field = "django.db.models.UUIDField"

    def ready(self) -> None:
        connection_created.connect(
            ensure_cricket_schema,
            dispatch_uid="apps.core.ensure_cricket_schema",
        )
