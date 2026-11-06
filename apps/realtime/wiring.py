"""Composition root hooks for ``apps/realtime`` (injected, never imported).

``apps/realtime`` depends only on ``apps.core`` (import-linter enforced). The
concrete reader (``apps.matches.services.stream_reader.PostgresEventLogReader``)
and policy (``…LicensingMatchPolicy``) live in ``apps/matches``; the composition
root — ``config/asgi.py`` — imports them and injects them here. This module
imports nothing beyond ``apps.core`` and the realtime package, so the contract
stays intact.

The hub and broker fall back to these configured defaults when constructed
without explicit overrides (tests always pass fakes directly). The hub is
installed here too: the SSE view (Group 7) and the WebSocket consumer
(Group 8) must share the *same* hub instance the broker feeds, so the
composition root registers it and the transports read it back.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from apps.core.streaming import EventLogReader, MatchPolicy

if TYPE_CHECKING:
    from apps.realtime.hub import Hub

_reader: EventLogReader | None = None
_policy: MatchPolicy | None = None
_hub: Hub | None = None


def configure(
    *,
    reader: EventLogReader | None = None,
    policy: MatchPolicy | None = None,
    hub: Hub | None = None,
) -> None:
    """Install the implementations (idempotent; tests may swap them)."""
    global _reader, _policy, _hub
    if reader is not None:
        _reader = reader
    if policy is not None:
        _policy = policy
    if hub is not None:
        _hub = hub


def reset() -> None:
    """Drop configured implementations (test isolation)."""
    global _reader, _policy, _hub
    _reader = None
    _policy = None
    _hub = None


def get_reader() -> EventLogReader:
    """The configured log reader; raises when the composition root is absent."""
    if _reader is None:
        raise RuntimeError(
            "realtime reader is not configured — call wiring.configure(reader=…) "
            "from config/asgi.py before starting transports"
        )
    return _reader


def get_policy() -> MatchPolicy | None:
    return _policy


def get_hub() -> Hub:
    """The shared hub (SSE/WS transports attach to it).

    Must be configured by the composition root (``config/asgi.py``) so every
    transport on the node feeds and reads the same fan-out hub that the
    broker populates.
    """
    if _hub is None:
        raise RuntimeError(
            "realtime hub is not configured — call wiring.configure(hub=…) "
            "from config/asgi.py before starting transports"
        )
    return _hub


def configured() -> bool:
    return _reader is not None


__all__ = [
    "configure",
    "configured",
    "get_hub",
    "get_policy",
    "get_reader",
    "reset",
]
