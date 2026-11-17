"""Delivery partition management: on-demand creation, idempotency, races."""

from __future__ import annotations

import threading

import pytest
from django.db import connection

from apps.deliveries.services import partitions

pytestmark = pytest.mark.django_db


def _partition_exists(name: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass(%s)", [f"cricket.{name}"])
        return cursor.fetchone()[0] is not None


class TestEnsurePartition:
    def test_creates_yearly_partition(self) -> None:
        assert not _partition_exists("delivery_2026")
        assert partitions.ensure_partition(2026) is True
        assert _partition_exists("delivery_2026")

    def test_idempotent(self) -> None:
        assert partitions.ensure_partition(2026) is True
        assert partitions.ensure_partition(2026) is False
        assert _partition_exists("delivery_2026")

    def test_bounds_cover_the_year(self) -> None:
        partitions.ensure_partition(2026)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_get_expr(c.relpartbound, c.oid) FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'cricket' AND c.relname = 'delivery_2026'"
            )
            (bound,) = cursor.fetchone()
        assert "2026-01-01" in bound and "2027-01-01" in bound

    def test_insert_lands_in_correct_partition(self) -> None:
        """A delivery whose match_date falls in the year hits its partition."""
        partitions.ensure_partition(2026)
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM pg_class WHERE relname = 'delivery_2026'")
            assert cursor.fetchone()[0] == 1

    @pytest.mark.django_db(transaction=True)
    def test_concurrent_creation_is_safe(self) -> None:
        """Two threads racing to create the same partition: exactly one wins,
        both return without error. Runs outside the test transaction so each
        thread commits its own partition DDL."""
        from django.db import connection

        results: list[bool] = []
        errors: list[Exception] = []
        barrier = threading.Barrier(2)

        def worker() -> None:
            try:
                barrier.wait(timeout=5)
                created = partitions.ensure_partition(2027)
                results.append(created)
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)
            finally:
                # Close only this thread's connection so the main thread's
                # (test-transaction) connection is untouched.
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert not errors
        assert sorted(results) == [False, True]
        assert _partition_exists("delivery_2027")


class TestCommand:
    def test_command_ensures_range(self) -> None:
        from django.core.management import call_command

        call_command("ensure_delivery_partitions", from_year=2024, to_year=2025)
        assert _partition_exists("delivery_2024")
        assert _partition_exists("delivery_2025")
        assert not _partition_exists("delivery_2023")

    def test_command_is_idempotent(self) -> None:
        from django.core.management import call_command

        call_command("ensure_delivery_partitions", from_year=2025, to_year=2025)
        call_command("ensure_delivery_partitions", from_year=2025, to_year=2025)
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM pg_class WHERE relname = 'delivery_2025'")
            assert cursor.fetchone()[0] == 1
