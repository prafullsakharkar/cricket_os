"""Delivery partition management (CRICKET_PERFORMANCE_AND_SCALING §2).

``cricket.delivery`` is RANGE-partitioned by ``match_date``. Partitions are
**created on demand** — the write path calls :func:`ensure_partition` before
every insert — and there is **no DEFAULT partition** so a stray row can never
silently land in a catch-all partition (Phase C decisions; the original
plan's ``delivery_default`` was dropped by design).

``ensure_partition(year)`` is idempotent and race-safe:

1. if the partition already exists → no-op;
2. otherwise serialise concurrent creators on a per-year advisory lock, then
   re-check inside the lock and create the partition.

The lock is transaction-scoped (``pg_advisory_xact_lock``), so the whole
check-then-create runs inside one ``transaction.atomic()`` block.

Pre-creating next year's partition on a schedule and archiving old partitions
are Phase G tickets (see CRICKET_IMPLEMENTATION_ROADMAP.md Phase C decisions).
"""

from __future__ import annotations

from django.db import DatabaseError, connection, transaction

#: Advisory-lock key multiplier so each year maps to a distinct lock.
_ADVISORY_KEY_MULTIPLIER = 7919


def _partition_name(year: int) -> str:
    return f"delivery_{year}"


def _exists(name: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass(%s)", [f"cricket.{name}"])
        return cursor.fetchone()[0] is not None


def _is_duplicate_table(exc: DatabaseError) -> bool:
    """True when the error is PostgreSQL's ``duplicate_table`` (42P07)."""
    from psycopg import errors as pg_errors

    return isinstance(exc.__cause__, pg_errors.DuplicateTable)


def ensure_partition(year: int) -> bool:
    """Create the yearly partition for ``year`` if it does not exist.

    Idempotent: safe to call on every delivery insert. Race-safe: concurrent
    callers serialise on a transaction-scoped advisory lock and re-check
    existence inside it; if two creators still race (e.g. one transaction had
    not yet committed when the other checked), the losing ``CREATE TABLE``
    raises ``duplicate_table`` which is treated as success. Exactly one
    ``CREATE TABLE ... PARTITION OF`` ever succeeds per year. Returns ``True``
    when this call created the partition.
    """
    year = int(year)
    name = _partition_name(year)
    if _exists(name):
        return False
    try:
        with transaction.atomic():
            lock_key = year * _ADVISORY_KEY_MULTIPLIER
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [lock_key])
                # Re-check inside the lock: the creator that held it first may
                # have committed while we waited.
                if _exists(name):
                    return False
                # The identifier comes from an int(year), so interpolation is
                # safe; the range bounds are bound parameters.
                sql = (
                    f"CREATE TABLE cricket.{name} PARTITION OF cricket.delivery "
                    "FOR VALUES FROM (%s) TO (%s)"
                )
                cursor.execute(sql, [f"{year}-01-01", f"{year + 1}-01-01"])
                return True
    except DatabaseError as exc:
        if _is_duplicate_table(exc):
            return False
        raise
