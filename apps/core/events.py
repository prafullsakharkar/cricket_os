"""In-process event bus (ARCHITECTURE §5).

Writes use ``transaction.atomic()``; events are published *after commit* via
``transaction.on_commit`` so consumers never observe rolled-back data. The
``on_commit=False`` escape hatch is reserved for synchronous, same-transaction
subscribers — the audit trail must commit or roll back with its change.

Handlers are registered in each app's ``AppConfig.ready()`` with
:func:`subscribe`. A handler that raises propagates: for post-commit handlers
that is an application error after the commit; for synchronous handlers it
fails the surrounding transaction (which is exactly what audit wants).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.db import transaction

#: Audit-worthy topic: a duplicate player merge completed.
TOPIC_PLAYER_MERGED = "player.merged"
#: Audit-worthy topic: a provider-entity mapping was verified by a steward.
TOPIC_PROVIDER_MAP_VERIFIED = "provider_map.verified"
#: Audit-worthy topic: a duplicate candidate was reviewed (rejected/merged).
TOPIC_DUPLICATE_REVIEWED = "player_duplicate.reviewed"

_handlers: dict[str, list[Callable[[Any], None]]] = {}


def subscribe(topic: str, handler: Callable[[Any], None]) -> None:
    """Register ``handler(payload)`` to run for ``topic`` on every publish."""
    _handlers.setdefault(topic, []).append(handler)


def _dispatch(topic: str, payload: Any) -> None:
    """Call every handler for ``topic``; exceptions propagate to the caller."""
    for handler in list(_handlers.get(topic, ())):
        handler(payload)


def publish(topic: str, payload: Any, *, on_commit: bool = True) -> None:
    """Publish ``payload`` on ``topic``.

    By default handlers run after the surrounding transaction commits
    (``transaction.on_commit``). Pass ``on_commit=False`` for synchronous
    in-transaction subscribers such as the audit log.
    """
    if on_commit:
        transaction.on_commit(lambda: _dispatch(topic, payload))
    else:
        _dispatch(topic, payload)


def clear_subscriptions() -> None:
    """Drop every registered handler (test isolation)."""
    _handlers.clear()


__all__ = [
    "TOPIC_DUPLICATE_REVIEWED",
    "TOPIC_PLAYER_MERGED",
    "TOPIC_PROVIDER_MAP_VERIFIED",
    "clear_subscriptions",
    "publish",
    "subscribe",
]
