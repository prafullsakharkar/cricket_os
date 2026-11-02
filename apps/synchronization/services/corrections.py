"""Audit rows for applied corrections (owned by ``synchronization``).

The delivery write path (``deliveries.services.write``) calls
:func:`record_correction` whenever it supersedes previously-imported content —
a deliberate, documented cross-module dependency (deliveries → synchronization)
so the correction audit row commits in the same transaction as the supersede
(Phase C decisions, CRICKET_IMPLEMENTATION_ROADMAP.md).
"""

from __future__ import annotations

from typing import Any

from apps.synchronization.models import DataCorrection


def record_correction(
    *,
    provider: Any,
    entity_type: str,
    entity_id: Any,
    provider_entity_id: str | None = None,
    field_path: str | None = None,
    old_value: dict[str, Any] | None = None,
    new_value: dict[str, Any] | None = None,
    reason: str | None = None,
    applied_by: Any = None,
    source_revision: int | None = None,
    target_revision: int,
) -> DataCorrection:
    """Persist one ``DataCorrection`` row (call inside the caller's transaction).

    ``old_value``/``new_value`` are JSON payloads (the content before/after the
    correction). The caller is responsible for not passing secrets or
    credential material — delivery content is public cricket data, but the
    redaction policy (CRICKET_SECURITY_AND_RELIABILITY §4) still applies to
    anything written here.
    """
    return DataCorrection.objects.create(
        provider=provider,
        entity_type=entity_type,
        entity_id=entity_id,
        provider_entity_id=provider_entity_id,
        field_path=field_path,
        old_value=old_value,
        new_value=new_value,
        reason=reason,
        applied_by=applied_by,
        source_revision=source_revision,
        target_revision=target_revision,
    )
