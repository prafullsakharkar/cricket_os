"""Celery tasks for the Tier-2 fantasy pipeline.

``recompute_match`` is routed to the ``tier2`` queue (``CELERY_TASK_ROUTES``
in settings), keeping heavy recompute work off the live request path. Tests
run eager (``CELERY_TASK_ALWAYS_EAGER``), so the task body is exercised
directly.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from celery import shared_task


@shared_task(name="apps.fantasy.recompute_match")  # type: ignore[untyped-decorator]
def recompute_match_task(match_id: str) -> dict[str, Any] | None:
    """Recompute fantasy points for one match (idempotent).

    Decrements ``fantasy_queue_depth`` (a gauge incremented by
    :func:`enqueue_recompute`) whether the compute succeeds or raises, so the
    gauge tracks pending work, not total failures.
    """
    from apps.fantasy import metrics
    from apps.fantasy.services.recompute import recompute_match
    from apps.matches.models import Match

    try:
        match = Match.objects.filter(id=match_id).first()
        if match is None:
            return None
        report = recompute_match(match, trigger="backfill")
        return dataclasses.asdict(report)
    finally:
        metrics.FANTASY_QUEUE_DEPTH.inc(-1)


def enqueue_recompute(match_id: str) -> Any:
    """Enqueue a match on the tier2 queue (fire-and-forget)."""
    from apps.fantasy import metrics

    metrics.FANTASY_QUEUE_DEPTH.inc()
    return recompute_match_task.delay(str(match_id))


__all__ = ["enqueue_recompute", "recompute_match_task"]
