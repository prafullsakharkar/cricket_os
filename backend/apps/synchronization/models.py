"""Sync checkpoints and applied data corrections.

``SyncCheckpoint`` records the last committed position per ``(provider, scope)``
so a crashed import can resume without duplicating or losing matches
(CRICKET_DATA_INGESTION §3.5). It is advanced **only after** the surrounding
transaction commits.

``DataCorrection`` is the audit row written whenever the delivery write path
supersedes previously-imported content (a provider revision bump, or a manual
correction). ``source_revision``/``target_revision`` capture the superseded →
new revision transition.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone

from apps.core.models import UUIDPrimaryKeyModel


class SyncCheckpoint(UUIDPrimaryKeyModel):
    provider = models.ForeignKey(
        "providers.DataProvider",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="checkpoints",
        db_index=False,
    )
    scope_key = models.TextField()
    last_delivery_seq = models.BigIntegerField(null=True, blank=True)
    last_provider_updated_at = models.DateTimeField(null=True, blank=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    cursor = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "sync_checkpoint"
        constraints = [
            models.UniqueConstraint(
                fields=["provider_id", "scope_key"],
                name="ux_sync",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.provider_id}:{self.scope_key}"


class ReconciliationJob(UUIDPrimaryKeyModel):
    """A detected divergence between provider state and derived state.

    Rows are operational: a mismatch is recorded, surfaced in admin and
    *never* auto-corrected silently. ``scope_key`` identifies the comparison
    (e.g. ``state:<match id>``) so re-running a reconciliation is idempotent
    and cannot open duplicate jobs for the same still-unresolved divergence.
    """

    SCOPE_CHECKPOINT = "checkpoint"
    SCOPE_STATE = "state"
    SCOPE_FIXTURES = "fixtures"
    STATUS_OPEN = "open"
    STATUS_RESOLVED = "resolved"
    STATUS_IGNORED = "ignored"

    scope_key = models.TextField()
    expected_state = models.JSONField(null=True, blank=True)
    observed_state = models.JSONField(null=True, blank=True)
    differences = models.JSONField(null=True, blank=True)
    status = models.TextField(default=STATUS_OPEN)
    created_at = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "reconciliation_job"
        indexes = [
            models.Index(fields=["scope_key", "status"], name="ix_reconciliation_scope"),
        ]

    def __str__(self) -> str:
        return f"{self.scope_key}:{self.status}"


class DataCorrection(UUIDPrimaryKeyModel):
    provider = models.ForeignKey(
        "providers.DataProvider",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="corrections",
        db_index=False,
    )
    entity_type = models.TextField()
    entity_id = models.UUIDField()
    provider_entity_id = models.TextField(null=True, blank=True)
    field_path = models.TextField(null=True, blank=True)
    old_value = models.JSONField(null=True, blank=True)
    new_value = models.JSONField(null=True, blank=True)
    reason = models.TextField(null=True, blank=True)
    applied_by = models.UUIDField(null=True, blank=True)
    applied_at = models.DateTimeField(default=timezone.now)
    source_revision = models.IntegerField(null=True, blank=True)
    target_revision = models.IntegerField()

    class Meta:
        db_table = "data_correction"
        indexes = [
            models.Index(fields=["entity_type", "entity_id"], name="ix_correction_entity"),
        ]

    def __str__(self) -> str:
        return f"{self.entity_type}:{self.entity_id} {self.source_revision}->{self.target_revision}"
