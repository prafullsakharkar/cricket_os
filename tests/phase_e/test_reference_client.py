"""Reference client (ledger Group 9): pure codec/parser unit tests.

The client is a dependency-free dev tool; its network paths are exercised by
hand against a running server (documented in tools/reference_client/README.md).
What is testable without a socket is tested here: the SSE block parser, the
frame model validator, and the RFC 6455 frame codec (encode/decode round-trip
and incremental buffering), including the canonical handshake vector from
RFC 6455 §1.3.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "realtime_client",
    Path(__file__).resolve().parents[2] / "tools" / "reference_client" / "realtime_client.py",
)
assert _SPEC is not None and _SPEC.loader is not None
client = importlib.util.module_from_spec(_SPEC)
# Register so the dataclass machinery can resolve the module's namespace.
sys.modules["realtime_client"] = client
_SPEC.loader.exec_module(client)


def test_parse_sse_block_builds_a_frame() -> None:
    block = (
        "id: 7\n"
        "event: delivery.appended\n"
        'data: {"seq":7,"event_type":"delivery.appended","match_id":"m-1",'
        '"payload":{"delivery_seq":7},"created_at":"2026-11-04T10:00:00Z"}\n\n'
    )
    frame = client.parse_sse_block(block)
    assert frame.seq == 7
    assert frame.event_type == "delivery.appended"
    assert frame.match_id == "m-1"
    assert frame.payload == {"delivery_seq": 7}


def test_parse_sse_block_requires_a_data_line() -> None:
    with pytest.raises(client.ReferenceClientError):
        client.parse_sse_block("id: 1\nevent: batch\n\n")


def test_parse_sse_block_rejects_invalid_json() -> None:
    with pytest.raises(client.ReferenceClientError):
        client.parse_sse_block("data: not-json\n\n")


def test_is_heartbeat_detects_comment_only_blocks() -> None:
    assert client.is_heartbeat(": ping\n\n") is True
    assert client.is_heartbeat(": ping\n") is True
    assert client.is_heartbeat("id: 1\ndata: {}\n\n") is False


def test_frame_from_dict_validates_shape() -> None:
    frame = client.frame_from_dict(
        {"seq": 1, "event_type": "batch", "match_id": "m", "payload": {"count": 2}}
    )
    assert frame.event_type == "batch"
    assert frame.payload == {"count": 2}
    with pytest.raises(client.ReferenceClientError):
        client.frame_from_dict("not-a-dict")
    with pytest.raises(client.ReferenceClientError):
        client.frame_from_dict({"seq": "nope", "event_type": "x", "match_id": "m"})


def test_ws_handshake_accept_matches_rfc6455_vector() -> None:
    # RFC 6455 §1.3 worked example.
    key = "dGhlIHNhbXBsZSBub25jZQ=="
    assert client._ws_accept(key) == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="


def test_ws_frame_round_trip() -> None:
    payload = b'{"seq":1,"event_type":"delivery.appended","match_id":"m-1"}'
    encoded = client._encode_ws_frame(payload, 0x1)  # client frame, masked
    frames, remaining = client._decode_ws_frames(bytearray(encoded))
    assert remaining == bytearray()
    assert frames == [(0x1, payload)]


def test_ws_frame_round_trip_long_payload() -> None:
    payload = b"x" * 300  # forces the 16-bit extended length
    encoded = client._encode_ws_frame(payload, 0x2)
    frames, remaining = client._decode_ws_frames(bytearray(encoded))
    assert frames == [(0x2, payload)]
    assert remaining == bytearray()


def test_ws_decoder_buffers_partial_frames() -> None:
    payload = b'{"seq":1,"event_type":"delivery.appended"}'
    encoded = client._encode_ws_frame(payload, 0x1)
    # Feed the encoded frame one byte at a time; nothing decodes until it is
    # complete, then the whole frame appears in one step.
    buffer = bytearray()
    frames: list[tuple[int, bytes]] = []
    for byte in encoded:
        buffer += bytes([byte])
        decoded, buffer = client._decode_ws_frames(buffer)
        frames.extend(decoded)
    assert frames == [(0x1, payload)]
    assert buffer == bytearray()


def test_ws_decoder_handles_multiple_frames_and_leftover() -> None:
    first = client._encode_ws_frame(b"a" * 5, 0x1)
    second = client._encode_ws_frame(b"b" * 5, 0x1)
    third = client._encode_ws_frame(b"c" * 5, 0x1)
    frames, remaining = client._decode_ws_frames(bytearray(first + second + third[:3]))
    assert [payload for _, payload in frames] == [b"a" * 5, b"b" * 5]
    assert len(remaining) == len(third[:3])  # the partial third frame stays
