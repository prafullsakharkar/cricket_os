"""Composition root hooks for ``apps/realtime`` (injected, never imported).

``apps/realtime`` depends only on ``apps.core`` (import-linter enforced). The
concrete reader (``apps.matches.services.stream_reader.PostgresEventLogReader``)
and policy (``…LicensingMatchPolicy``) live in ``apps/matches``; the composition
root — ``config/asgi.py`` — imports them and injects them here. This module
imports nothing beyond ``apps.core`` and the realtime package, so the contract
stays intact.

The hub and broker fall back to these configured defaults when constructed
without explicit overrides (tests always pass fakes directly).
"""

from __future__ import annotations

from apps.core.streaming import EventLogReader, MatchPolicy

_reader: EventLogReader | None = None
_policy: MatchPolicy | None = None


def configure(*, reader: EventLogReader | None = None, policy: MatchPolicy | None = None) -> None:
    """Install the implementations (idempotent; tests may swap them)."""
    global _reader, _policy
    if reader is not None:
        _reader = reader
    if policy is not None:
        _policy = policy


def reset() -> None:
    """Drop configured implementations (test isolation)."""
    global _reader, _policy
    _reader = None
    _policy = None


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


def configured() -> bool:
    return _reader is not None


__all__ = [
    "configure",
    "configured",
    "get_policy",
    "get_reader",
    "reset",
]
