"""Dependency health probes used by the ``/api/v1/health`` endpoint.

Each probe returns ``None`` on success and raises on failure; the view
translates a raised exception into a degraded check. They are module-level
functions so tests (and operators) can patch or call them directly.
"""

from __future__ import annotations

from django.db import connection
from django_redis import get_redis_connection


def check_database() -> None:
    """Round-trip a trivial query to prove the database is reachable."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()


def check_redis() -> None:
    """Ping Redis to prove the cache/broker dependency is reachable."""
    get_redis_connection("default").ping()
