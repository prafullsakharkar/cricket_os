"""Provider-delivery corrections on live/completed matches (DATA_INGESTION §4.5).

The single entry point for *content* corrections (a provider revises a ball
after it was ingested). It composes the primitives owned by other apps, all in
ONE transaction under the per-match advisory lock:

1. ``live.services.apply_batch`` supersedes the affected deliveries through the
   Phase C write path (which writes the ``DataCorrection`` audit row), rebuilds
   the innings (Tier 1) and appends ``delivery.corrected``/``innings.updated``
   event-log rows;
2. ``matches.services.result_service.reevaluate`` re-derives the result **in
   place** — the match stays COMPLETED, so ``match.completed`` is published
   exactly once per match and the changed outcome is audited/flagged
   (``result_changed``) by the result service itself;
3. a ``correction.applied`` event-log row is appended when the result did not
   change (a content-only correction still has a durable event); the
   Tier-2 statistics rebuild subscribers fire on the ``correction.applied`` /
   ``scorecard.updated`` topics that the rebuild modules already publish.

A genuinely resumed match (a completed/abandoned match where play actually
restarts) must go through the explicit, audited ``lifecycle.reopen`` first —
this module is for content corrections that re-derive in place.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from django.db import transaction

from apps.matches import event_schemas
from apps.matches.models import Match
from apps.matches.services import event_log
from apps.matches.services.result_service import ResultReport, reevaluate

_TERMINAL = frozenset({"COMPLETED", "ABANDONED", "CANCELLED", "FORFEITED"})


@dataclass(frozen=True)
class CorrectionReport:
    """What one provider correction did."""

    match_id: str
    superseded: int
    result: ResultReport
    result_changed: bool
    correction_ids: tuple[str, ...]


def apply_delivery_correction(
    match: Match,
    records: Iterable[Any],
    *,
    reason: str,
    source: Any = None,
    provider: Any = None,
) -> CorrectionReport:
    """Apply a provider revision of delivery content to ``match``.

    ``records`` are ``LiveInningsBatch`` objects (same shape as
    ``apply_batch``). Safe to run twice: the second run is a no-op supersede
    (idempotent) and the result re-derivation converges to the same digest.
    """
    from apps.live.services.apply import ApplyReport, apply_batch

    with transaction.atomic():
        event_log.lock_match(match.id)
        match.refresh_from_db()
        was_terminal = match.status in _TERMINAL
        before_ids = _delivery_correction_ids()

        batch_report: ApplyReport = apply_batch(
            match,
            records,
            source=source,
            provider=provider,
        )
        result: ResultReport = reevaluate(match, source=source)

        if was_terminal and batch_report.changed and not result.result_changed:
            # The result service already appended ``correction.applied`` (with
            # result_changed=True) when the outcome changed; a content-only
            # correction still needs a durable event-log row. A no-op re-run
            # (``batch_report.changed`` False) appends nothing, so re-applying
            # a correction is idempotent.
            event_log.append_event(
                match.id,
                event_schemas.CORRECTION_APPLIED,
                {
                    "entity_type": "delivery",
                    "entity_id": str(match.id),
                    "field_path": "deliveries",
                    "reason": reason,
                    "target_revision": match.revision,
                    "result_changed": False,
                },
            )

    return CorrectionReport(
        match_id=str(match.id),
        superseded=batch_report.corrected,
        result=result,
        result_changed=result.result_changed,
        correction_ids=tuple(pk for pk in _delivery_correction_ids() if pk not in before_ids),
    )


def _delivery_correction_ids() -> frozenset[str]:
    """Ids of ``DataCorrection`` rows written so far in this transaction."""
    from apps.synchronization.models import DataCorrection

    return frozenset(
        str(pk)
        for pk in DataCorrection.objects.filter(entity_type="delivery").values_list("id", flat=True)
    )


__all__ = ["CorrectionReport", "apply_delivery_correction"]
