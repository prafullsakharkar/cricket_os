"""Audit rows for applied corrections (owned by ``synchronization``).

The delivery write path (``deliveries.services.write``) calls
:func:`record_correction` whenever it supersedes previously-imported content —
a deliberate, documented cross-module dependency (deliveries → synchronization)
so the correction audit row commits in the same transaction as the supersede
(Phase C decisions, CRICKET_IMPLEMENTATION_ROADMAP.md).
"""

from __future__ import annotations

from typing import Any

from apps.core import events as core_events
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


def apply_manual_correction(
    *,
    match: Any,
    entity_type: str,
    entity_id: Any,
    field_path: str,
    old_value: Any,
    new_value: Any,
    reason: str,
    provider: Any = None,
    actor: Any = None,
    target_revision: int = 1,
    source: Any = None,
) -> DataCorrection:
    """Record an append-only steward/provider correction + event.

    Manual corrections use the same entry point as provider revisions: the
    audit row and the ``correction.applied`` event commit with the change, and
    the post-commit Tier-2 subscriber recomputes statistics. Corrections never
    bypass the delivery write path for ball content (that stays
    ``append_or_revise``); this is for match/entity metadata.
    """
    from apps.matches import event_schemas
    from apps.matches.services import event_log

    # ``applied_by`` is a UUID column; accept a model instance or an id.
    if actor is not None:
        actor = getattr(actor, "id", actor)
    row = record_correction(
        provider=provider,
        entity_type=entity_type,
        entity_id=entity_id,
        field_path=field_path,
        old_value=old_value,
        new_value=new_value,
        reason=reason,
        applied_by=actor,
        target_revision=target_revision,
    )
    payload = {
        "entity_type": entity_type,
        "entity_id": str(entity_id),
        "field_path": field_path,
        "reason": reason,
        "target_revision": target_revision,
        "result_changed": False,
    }
    if match is not None:
        event_log.append_event(match.id, event_schemas.CORRECTION_APPLIED, payload)
        core_events.publish(
            core_events.TOPIC_CORRECTION_APPLIED,
            {
                "match_id": str(match.id),
                "source": getattr(source, "code", None),
                "digest": None,
            },
        )
    return row


__all__ = ["apply_manual_correction", "record_correction"]
