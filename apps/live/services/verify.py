"""Shadow verification for Tier-1 derived rows (ROADMAP Phase D).

The pure engine deliberately exposes **no** incremental ``apply_delivery``
(SCORING_ENGINE_API §7); a live batch therefore rebuilds the affected innings
from the stored current deliveries (~3 ms for 300 balls). Because the rebuild
is already a full recomputation, "shadow verify" is a second, independent
comparison of the persisted rows against the engine output: if a derived row
was corrupted (or a partial write slipped through), the digests differ, we
heal by rebuilding again, write an ``IngestionError`` (``stage=shadow_verify``)
and bump the mismatch metric.

Verification runs every ``LIVE_SHADOW_VERIFY_EVERY`` appended batches (and can
be forced by the caller at innings end).
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from apps.core import metrics
from apps.ingestion.services.jobs import record_error
from apps.innings.models import Innings
from apps.scorecards.services.rebuild import verify_innings as _verify

logger = logging.getLogger("apps.live.verify")

_COUNTS: dict[str, int] = {}
_LOCK = threading.Lock()


def reset_counters() -> None:
    """Reset the per-match verification counters (test isolation)."""
    with _LOCK:
        _COUNTS.clear()


def should_verify(match_id: Any, *, every: int, force: bool = False) -> bool:
    """True when this batch should run a shadow verification."""
    if force or every <= 0:
        return force
    with _LOCK:
        count = _COUNTS.get(str(match_id), 0) + 1
        _COUNTS[str(match_id)] = count
    return count % every == 0


def verify(innings: Innings, *, force: bool = True) -> bool:
    """Compare persisted Tier-1 rows to a fresh engine rebuild.

    Returns ``True`` when they agree. On a mismatch the innings is rebuilt
    (healing the rows), an ``IngestionError`` is recorded and
    ``shadow_verify_mismatches_total`` is incremented.
    """
    if _verify(innings):
        return True
    metrics.SHADOW_VERIFY_MISMATCHES.inc()
    record_error(
        None,
        stage="shadow_verify",
        payload={"match_id": str(innings.match_id), "innings_id": str(innings.id)},
        error_class="ShadowVerifyMismatch",
        error_detail="persisted Tier-1 rows differed from a fresh engine rebuild; healed",
    )
    logger.warning(
        "shadow verify mismatch healed",
        extra={"match_id": str(innings.match_id), "innings_id": str(innings.id)},
    )
    # ``rebuild_innings`` persists the engine output because the stored digest
    # no longer matches; this is the heal step.
    from apps.scorecards.services.rebuild import rebuild_innings

    rebuild_innings(innings)
    return False


__all__ = ["reset_counters", "should_verify", "verify"]
