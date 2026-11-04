"""Frame normalization, validation and coalescing (API_SPEC §3.8).

The broker receives raw Redis pub/sub JSON (the log envelope plus ``seq``/
``created_at``); ``parse_frame`` validates and normalizes it into a
:class:`Frame`. Control frames (``batch``/``resync_required``/
``server.draining``) are hub-synthesized; ``make_batch`` coalesces ≤1 Hz.
"""

from __future__ import annotations

import json

import pytest

from apps.core.streaming import StreamEvent
from apps.realtime.frames import (
    MAX_EVENT_BYTES,
    Frame,
    FrameError,
    control_frame,
    from_stream_event,
    is_control,
    is_immediate,
    make_batch,
    parse_frame,
)

EVENT = {
    "seq": 5,
    "event_type": "delivery.appended",
    "match_id": "m-1",
    "created_at": "2026-11-04T10:00:00Z",
    "v": 1,
    "delivery_seq": 42,
    "runs": {"total": 1},
}


def test_parse_frame_normalizes_broker_message() -> None:
    frame = parse_frame(json.dumps(EVENT))
    assert frame.seq == 5
    assert frame.event_type == "delivery.appended"
    assert frame.match_id == "m-1"
    assert frame.created_at == "2026-11-04T10:00:00Z"
    # Envelope fields that are not transport-level live in payload.
    assert frame.payload["v"] == 1
    assert frame.payload["delivery_seq"] == 42
    assert "seq" not in frame.payload
    assert "event_type" not in frame.payload


def test_parse_frame_accepts_dict_and_frame() -> None:
    assert parse_frame(EVENT).seq == 5
    frame = Frame(seq=1, event_type="x", match_id="m")
    assert parse_frame(frame) is frame


def test_parse_frame_rejects_malformed_inputs() -> None:
    for bad in (
        "not json",
        json.dumps({"seq": "five", "event_type": "x", "match_id": "m"}),
        json.dumps({"seq": 1, "match_id": "m"}),  # missing event_type
        json.dumps({"seq": 1, "event_type": "x"}),  # missing match_id
        json.dumps([1, 2, 3]),
        json.dumps({"seq": -1, "event_type": "x", "match_id": "m"}),
    ):
        with pytest.raises(FrameError):
            parse_frame(bad)


def test_parse_frame_rejects_oversized_messages() -> None:
    big = dict(EVENT, payload={"blob": "x" * MAX_EVENT_BYTES})
    with pytest.raises(FrameError):
        parse_frame(json.dumps(big))


def test_round_trip_to_dict_and_json() -> None:
    frame = parse_frame(EVENT)
    assert parse_frame(frame.to_json()).to_dict() == frame.to_dict()


def test_from_stream_event_keeps_seq_and_payload() -> None:
    event = StreamEvent(
        seq=7,
        event_type="match.completed",
        match_id="m-1",
        payload={"v": 1, "match_id": "m-1", "winner": "home"},
        created_at="2026-11-04T11:00:00Z",
    )
    frame = from_stream_event(event)
    assert frame.seq == 7
    assert frame.event_type == "match.completed"
    assert frame.payload["winner"] == "home"


def test_make_batch_coalesces_sorted_frames_with_max_seq() -> None:
    frames = [
        Frame(seq=3, event_type="delivery.appended", match_id="m-1"),
        Frame(seq=1, event_type="delivery.appended", match_id="m-1"),
        Frame(seq=2, event_type="innings.updated", match_id="m-1"),
    ]
    batch = make_batch("m-1", frames)
    assert batch.event_type == "batch"
    assert batch.seq == 3
    assert batch.payload["count"] == 3
    assert [e["seq"] for e in batch.payload["events"]] == [1, 2, 3]
    assert is_control(batch.event_type)


def test_immediate_and_control_classifiers() -> None:
    assert is_immediate("correction.applied")
    assert is_immediate("match.completed")
    assert not is_immediate("delivery.appended")
    for control in ("batch", "resync_required", "server.draining"):
        assert is_control(control)
    assert not is_control("delivery.appended")


def test_control_frame_carries_reason() -> None:
    frame = control_frame("resync_required", "m-1", seq=9, reason="retention_window")
    assert frame.event_type == "resync_required"
    assert frame.payload["reason"] == "retention_window"
    assert frame.seq == 9
