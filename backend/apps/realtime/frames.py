"""Client-facing realtime frames (API_SPEC §3.8, FRONTEND_CONTRACT §2).

A :class:`Frame` is the normalized unit delivered to a subscriber. The broker
receives the raw Redis pub/sub message (the log envelope plus ``seq``/
``created_at`` added by ``append_events``), ``parse_frame`` validates and
normalizes it, and the hub coalesces several frames into one ``batch`` frame
per ≤1 Hz tick.

Frame JSON shape delivered to a client::

    {"seq": 5, "event_type": "delivery.appended", "match_id": "…",
     "payload": {"v": 1, "match_id": "…", "delivery_seq": 42, …},
     "created_at": "2026-…Z"}

Control frames the hub itself synthesizes (never from the log):

* ``batch`` — ``payload.events`` holds the coalesced frames, ``seq`` is the
  maximum sequence in the batch;
* ``resync_required`` — the client's ``last_seq`` cannot be replayed
  (``payload.reason``); the client must refetch the REST snapshot;
* ``server.draining`` — the node is shutting down (``payload.reason``); the
  client should reconnect (to any node) after a short backoff.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from apps.core.streaming import (
    CONTROL_EVENT_TYPES,
    IMMEDIATE_EVENT_TYPES,
    StreamEvent,
)

#: Reject oversized event payloads (a delivery JSON is ~200 B; this is a
#: generous ceiling that stops a runaway payload from ballooning the queue).
MAX_EVENT_BYTES = 16_000

_TOP_LEVEL_KEYS = frozenset({"seq", "event_type", "match_id", "created_at"})

#: Hub notices that are delivered but never advance a subscriber's position
#: (a ``batch`` frame, by contrast, IS delivery progress and advances it).
NOTICE_EVENT_TYPES = frozenset({"resync_required", "server.draining"})


class FrameError(ValueError):
    """A broker message could not be turned into a valid frame."""


@dataclass(frozen=True)
class Frame:
    """One normalized event or control message for a subscriber."""

    seq: int
    event_type: str
    match_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "event_type": self.event_type,
            "match_id": self.match_id,
            "payload": dict(self.payload),
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"), default=str)


def parse_frame(data: str | bytes | dict[str, Any] | Frame) -> Frame:
    """Normalize a broker message (or a frame) into a validated :class:`Frame`.

    Raises :class:`FrameError` for malformed or oversized messages; the broker
    counts those and moves on (a single corrupt message must not kill the
    subscription loop).
    """
    if isinstance(data, Frame):
        return data
    if isinstance(data, (str, bytes)):
        try:
            raw: Any = json.loads(data)
        except (json.JSONDecodeError, TypeError) as exc:
            raise FrameError(f"invalid json: {exc}") from exc
    else:
        raw = data
    if not isinstance(raw, dict):
        raise FrameError("frame must be a JSON object")

    seq = raw.get("seq")
    event_type = raw.get("event_type")
    match_id = raw.get("match_id")
    if not isinstance(seq, int) or seq < 0:
        raise FrameError(f"invalid seq {seq!r}")
    if not isinstance(event_type, str) or not event_type:
        raise FrameError("missing event_type")
    if not isinstance(match_id, str) or not match_id:
        raise FrameError("missing match_id")

    rest = {k: v for k, v in raw.items() if k not in _TOP_LEVEL_KEYS}
    # A client-frame (the shape ``to_dict`` produces) nests its payload under
    # ``payload``; a broker message puts the envelope fields at the top level.
    if set(rest) == {"payload"} and isinstance(rest.get("payload"), dict):
        payload = rest["payload"]
    else:
        payload = rest
    if len(json.dumps(raw, separators=(",", ":"), default=str)) > MAX_EVENT_BYTES:
        raise FrameError("frame exceeds MAX_EVENT_BYTES")
    return Frame(
        seq=seq,
        event_type=event_type,
        match_id=match_id,
        payload=payload,
        created_at=str(raw.get("created_at") or ""),
    )


def from_stream_event(event: StreamEvent) -> Frame:
    """A log-replay event as a live frame (backfill/subscribe replay)."""
    return Frame(
        seq=event.seq,
        event_type=event.event_type,
        match_id=event.match_id,
        payload=dict(event.payload),
        created_at=event.created_at,
    )


def make_batch(match_id: str, frames: list[Frame]) -> Frame:
    """Coalesce ``frames`` into one ``batch`` frame (≤1 Hz tick).

    The batch's ``seq`` is the maximum sequence, so a subscriber's position
    advances past the whole batch in one step.
    """
    ordered = sorted(frames, key=lambda f: f.seq)
    return Frame(
        seq=ordered[-1].seq,
        event_type="batch",
        match_id=match_id,
        payload={"count": len(ordered), "events": [f.to_dict() for f in ordered]},
        created_at=ordered[-1].created_at,
    )


def control_frame(event_type: str, match_id: str, *, seq: int, reason: str = "") -> Frame:
    """A hub-synthesized control frame (``resync_required``/``server.draining``)."""
    payload: dict[str, Any] = {}
    if reason:
        payload["reason"] = reason
    return Frame(seq=seq, event_type=event_type, match_id=match_id, payload=payload)


def is_immediate(event_type: str) -> bool:
    """Corrections and terminal transitions flush immediately (ledger #6)."""
    return event_type in IMMEDIATE_EVENT_TYPES


def is_control(event_type: str) -> bool:
    """Hub-synthesized frames (``batch`` and notices) vs. log events."""
    return event_type in CONTROL_EVENT_TYPES


def is_notice(event_type: str) -> bool:
    """Notices never advance a subscriber's ``last_seq`` (and never dedupe)."""
    return event_type in NOTICE_EVENT_TYPES


__all__ = [
    "MAX_EVENT_BYTES",
    "Frame",
    "FrameError",
    "control_frame",
    "from_stream_event",
    "is_control",
    "is_immediate",
    "is_notice",
    "make_batch",
    "parse_frame",
]
