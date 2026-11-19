"""Redis publication of committed match events (ARCHITECTURE §5, PERF §8).

The durable ``match_event_log`` row is the source of truth. Publication is a
best-effort **after-commit** side effect: if Redis is down the ingestion
transaction must still succeed (the log is replayable by Phase E consumers).
Failures are logged and counted, never raised.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from apps.core import metrics
from apps.core.redis import ROLE_PUBSUB, client

logger = logging.getLogger("apps.matches.publication")


def channel_for(match_id: Any) -> str:
    """The pub/sub channel for a match (PERF §8: ``match:{id}``)."""
    return f"match:{match_id}"


def publish_match_event(match_id: Any, event: dict[str, Any]) -> bool:
    """Publish one event to ``match:{id}``; return ``True`` on success.

    Never raises: a Redis outage is logged and counted so it cannot fail a
    committed ingestion or a read request.

    Written to the dedicated ``pubsub`` role (Phase E ledger #9); the realtime
    broker on every web node pattern-subscribes to ``match:*`` on the same role.
    """
    payload = json.dumps(event, separators=(",", ":"), default=str)
    try:
        connection = client(ROLE_PUBSUB)
        connection.publish(channel_for(match_id), payload)
    except Exception:
        metrics.EVENT_PUBLISH_FAILURES.inc()
        logger.warning(
            "event publish failed",
            extra={"match_id": str(match_id), "event_type": event.get("event_type")},
        )
        return False
    return True


__all__ = ["channel_for", "publish_match_event"]
