"""Abstract base models shared by every domain app.

Phase A defines no concrete models — only the mixins later phases will build
on: a UUIDv7 primary key, UTC timestamps, and the ingestion-provenance columns
from DOMAIN_MODEL §0.
"""

from __future__ import annotations

from django.db import models

from apps.core.uuid7 import uuid7


class UUIDPrimaryKeyModel(models.Model):
    """Give a model a time-ordered UUIDv7 primary key named ``id``."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)

    class Meta:
        abstract = True


class TimeStampedModel(models.Model):
    """Track ``created_at`` / ``updated_at`` (timestamptz, always UTC)."""

    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)

    class Meta:
        abstract = True


class ProvenanceModel(models.Model):
    """Provenance columns required on every ingestion-sourced table."""

    # NULL means "not sourced from a provider" (internally-created row), which
    # is distinct from an empty provider id — so null is intentional here.
    source_provider_id = models.TextField(null=True, blank=True)
    provider_updated_at = models.DateTimeField(null=True, blank=True)
    ingested_at = models.DateTimeField(null=True, blank=True)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        abstract = True


class BaseModel(UUIDPrimaryKeyModel, TimeStampedModel, ProvenanceModel):
    """Convenience base: UUIDv7 PK + timestamps + provenance."""

    class Meta:
        abstract = True
