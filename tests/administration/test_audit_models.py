"""AuditLog model basics (DATABASE_SCHEMA §3.8)."""

from __future__ import annotations

import pytest
from django.db import connection

from apps.administration.models import AuditLog

pytestmark = pytest.mark.django_db


def test_row_created_with_auto_timestamp() -> None:
    row = AuditLog.objects.create(action="player.merged", entity_type="player")
    assert row.created_at is not None
    assert row.pk is not None


def test_ip_accepts_ipv4_and_ipv6() -> None:
    AuditLog.objects.create(action="a", ip="192.0.2.1")
    AuditLog.objects.create(action="b", ip="2001:db8::1")
    assert AuditLog.objects.count() == 2


def test_nullable_fields_default_to_null() -> None:
    row = AuditLog.objects.create(action="a")
    assert row.actor_id is None
    assert row.actor_label is None
    assert row.before is None
    assert row.after is None


def test_indexes_exist() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname='cricket' AND indexname IN "
            "('ix_audit_entity', 'ix_audit_created')"
        )
        names = {row[0] for row in cursor.fetchall()}
    assert names == {"ix_audit_entity", "ix_audit_created"}


def test_entity_id_is_polymorphic() -> None:
    row = AuditLog.objects.create(
        action="provider_map.verified",
        entity_type="provider_entity_map",
        entity_id="88888888-8888-7888-8888-888888888888",
    )
    assert str(row.entity_id) == "88888888-8888-7888-8888-888888888888"
