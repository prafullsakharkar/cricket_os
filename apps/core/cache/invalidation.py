"""Domain-event -> cache-namespace invalidation registry (TESTING §10).

The durable event log and the in-process event bus are the two change feeds.
This module maps **change topics** to the cache namespaces a reader may have
derived from them, then bumps those versions. Bumping is idempotent and
exception-safe: cache invalidation must never break a committed write.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from apps.core import events, metrics
from apps.core.cache import versions

logger = logging.getLogger("apps.core.cache.invalidation")

Invalidator = Callable[[Mapping[str, Any]], Iterable[str]]

_REGISTRY: dict[str, list[Invalidator]] = {}
_SUBSCRIBED: set[str] = set()

_MATCH = "match:{match_id}"
_LIVE = "matches:live"
_UPCOMING = "matches:upcoming"
_RECENT = "matches:recent"


def register(topic: str, invalidator: Invalidator) -> None:
    """Register ``invalidator`` for ``topic`` (no duplicate registrations)."""
    handlers = _REGISTRY.setdefault(topic, [])
    if invalidator not in handlers:
        handlers.append(invalidator)


def _match_id(payload: Mapping[str, Any]) -> str | None:
    value = payload.get("match_id")
    return str(value) if value else None


def _match_live(payload: Mapping[str, Any]) -> set[str]:
    match_id = _match_id(payload)
    return {_MATCH.format(match_id=match_id), _LIVE} if match_id else set()


def _match_completed(payload: Mapping[str, Any]) -> set[str]:
    match_id = _match_id(payload)
    return {_MATCH.format(match_id=match_id), _LIVE, _RECENT} if match_id else set()


def _match_all(payload: Mapping[str, Any]) -> set[str]:
    match_id = _match_id(payload)
    if not match_id:
        return set()
    return {_MATCH.format(match_id=match_id), _LIVE, _UPCOMING, _RECENT}


def _match_recent(payload: Mapping[str, Any]) -> set[str]:
    match_id = _match_id(payload)
    return {_MATCH.format(match_id=match_id), _RECENT} if match_id else set()


def _player_merged(payload: Mapping[str, Any]) -> set[str]:
    namespaces = {"players"}
    for field in ("canonical_player_id", "merged_player_id"):
        value = payload.get(field)
        if value:
            namespaces.add(f"player:{value}")
    return namespaces


def _reference(kind: str) -> Invalidator:
    def invalidate(payload: Mapping[str, Any]) -> set[str]:
        namespaces = {f"{kind}s"}
        value = payload.get(f"{kind}_id")
        if value:
            namespaces.add(f"{kind}:{value}")
        return namespaces

    return invalidate


def _series(payload: Mapping[str, Any]) -> set[str]:
    namespaces = {"series"}
    value = payload.get("series_id")
    if value:
        namespaces.add(f"series:{value}")
    # A series change (name/season/format) is rendered on every match feed.
    namespaces.update({_LIVE, _UPCOMING, _RECENT})
    return namespaces


def _competition(payload: Mapping[str, Any]) -> set[str]:
    namespaces = {"competitions", "series"}
    value = payload.get("competition_id")
    if value:
        namespaces.add(f"competition:{value}")
    return namespaces


_DEFAULTS: dict[str, Invalidator] = {
    events.TOPIC_DELIVERY_APPENDED: _match_live,
    events.TOPIC_DELIVERY_CORRECTED: _match_live,
    events.TOPIC_SCORECARD_UPDATED: _match_live,
    events.TOPIC_INNINGS_UPDATED: _match_live,
    events.TOPIC_MATCH_UPDATED: _match_live,
    events.TOPIC_MATCH_STATE_CHANGED: _match_all,
    events.TOPIC_MATCH_COMPLETED: _match_completed,
    events.TOPIC_MATCH_REOPENED: _match_completed,
    events.TOPIC_CORRECTION_APPLIED: _match_recent,
    events.TOPIC_PLAYER_MERGED: _player_merged,
    events.TOPIC_PLAYER_UPDATED: _reference("player"),
    events.TOPIC_TEAM_UPDATED: _reference("team"),
    events.TOPIC_VENUE_UPDATED: _reference("venue"),
    events.TOPIC_SERIES_UPDATED: _series,
    events.TOPIC_COMPETITION_UPDATED: _competition,
}


def namespaces_for(topic: str, payload: Mapping[str, Any]) -> set[str]:
    """Every cache namespace that ``topic``/``payload`` invalidates."""
    result: set[str] = set()
    for invalidator in _REGISTRY.get(topic, ()):
        try:
            result.update(invalidator(payload))
        except Exception:  # one bad payload must not hide the rest
            logger.warning("cache invalidator failed", extra={"topic": topic})
    return result


def _bump(namespaces: Iterable[str]) -> None:
    versions.bump_many(namespaces)


def invalidate_event(topic: str, payload: Mapping[str, Any]) -> None:
    """Invalidate every namespace derived from one domain event."""
    namespaces = namespaces_for(topic, payload)
    if not namespaces:
        return
    try:
        _bump(namespaces)
    except Exception:  # invalidation must never fail the write path
        logger.warning("cache invalidation failed", extra={"topic": topic})
        return
    metrics.CACHE_INVALIDATIONS.inc(len(namespaces))


def _subscriber(topic: str) -> Callable[[Any], None]:
    def handle(payload: Any) -> None:
        if isinstance(payload, Mapping):
            invalidate_event(topic, payload)

    return handle


def register_defaults() -> None:
    """Install the default registry entries and bus subscriptions (idempotent)."""
    for topic, invalidator in _DEFAULTS.items():
        register(topic, invalidator)
        if topic not in _SUBSCRIBED:
            events.subscribe(topic, _subscriber(topic))
            _SUBSCRIBED.add(topic)


def resubscribe() -> None:
    """Re-attach bus subscriptions after a test (or reload) cleared the bus."""
    _SUBSCRIBED.clear()
    register_defaults()


def registered_topics() -> frozenset[str]:
    """Topics with at least one invalidator (tests/observability)."""
    return frozenset(_REGISTRY)


__all__ = [
    "Invalidator",
    "invalidate_event",
    "namespaces_for",
    "register",
    "register_defaults",
    "registered_topics",
    "resubscribe",
]
