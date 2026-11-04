#!/usr/bin/env python3
"""Reference client for the Cricket OS realtime feeds (Phase E, ledger Group 9).

A dependency-free client for both transports — SSE (plain HTTP, stdlib
``urllib``) and WebSocket (a minimal RFC 6455 client over a raw socket) —
demonstrating the parity contract in ``docs/FRONTEND_CONTRACT.md``:

* **Resume** — ``--from-seq N`` replays the log from N (SSE sends
  ``Last-Event-ID`` + ``from_seq``; WS sends ``from_seq``). A client that
  reconnects after a drop passes its last seen ``seq`` and receives exactly
  the events after it.
* **Frames** — every event is ``{seq, event_type, match_id, payload,
  created_at}`` on both transports; control frames are ``batch``,
  ``resync_required`` and ``server.draining``.
* **Recovery** — ``resync_required`` means the retained window no longer
  covers the requested position: refetch the match snapshot over REST and
  resubscribe from its ``meta.event_seq``. ``server.draining`` means the node
  is shutting down: reconnect elsewhere.

Usage:
    uv run python tools/reference_client/realtime_client.py \\
        --transport sse --base-url http://localhost:8000 --match <uuid> \\
        --from-seq 0 --seconds 30

Only the Python standard library is used, so the script runs anywhere the
server is reachable (no package install needed).
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import json
import os
import socket
import ssl
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, BinaryIO

#: RFC 6455 magic GUID used to derive Sec-WebSocket-Accept.
_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

#: WS opcodes.
_OP_CONT = 0x0
_OP_TEXT = 0x1
_OP_BINARY = 0x2
_OP_CLOSE = 0x8
_OP_PING = 0x9
_OP_PONG = 0xA


class ReferenceClientError(Exception):
    """A transport-level failure surfaced to the CLI."""


# --------------------------------------------------------------------------- #
# Frame model (mirrors apps/realtime/frames.Frame.to_dict)
# --------------------------------------------------------------------------- #


@dataclass
class Frame:
    seq: int
    event_type: str
    match_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""


def parse_sse_block(block: str) -> Frame:
    """Parse one SSE event block (``id:``/``event:``/``data:`` lines).

    Raises :class:`ReferenceClientError` for malformed blocks. The caller
    skips comment-only blocks (heartbeats) before calling this.
    """
    fields: dict[str, str] = {}
    for line in block.splitlines():
        if not line or line.startswith(":"):
            continue  # comment / empty
        key, _, value = line.partition(":")
        if key in ("id", "event", "data"):
            fields[key] = value.lstrip(" ")
    if "data" not in fields:
        raise ReferenceClientError("SSE block has no data line")
    try:
        raw = json.loads(fields["data"])
    except json.JSONDecodeError as exc:
        raise ReferenceClientError(f"invalid frame JSON: {exc}") from exc
    return frame_from_dict(raw)


def frame_from_dict(raw: Any) -> Frame:
    """Validate one decoded frame into the :class:`Frame` model."""
    if not isinstance(raw, dict):
        raise ReferenceClientError("frame must be a JSON object")
    seq = raw.get("seq")
    event_type = raw.get("event_type")
    match_id = raw.get("match_id")
    if not isinstance(seq, int) or not isinstance(event_type, str) or not isinstance(match_id, str):
        raise ReferenceClientError(f"malformed frame: {raw!r}")
    return Frame(
        seq=seq,
        event_type=event_type,
        match_id=match_id,
        payload=dict(raw.get("payload") or {}),
        created_at=str(raw.get("created_at", "")),
    )


def is_heartbeat(block: str) -> bool:
    """True for an SSE comment-only block (the server's keepalive ping)."""
    return all(not line or line.startswith(":") for line in block.splitlines())


# --------------------------------------------------------------------------- #
# SSE transport (stdlib urllib)
# --------------------------------------------------------------------------- #


class SseClient:
    """Stream one match's SSE feed, resuming from ``from_seq``."""

    def __init__(self, url: str, *, from_seq: int | None = None, timeout: float = 30.0) -> None:
        self.url = url
        self.from_seq = from_seq
        self.timeout = timeout

    def frames(self) -> Any:
        """Yield :class:`Frame` objects until EOF; heartbeats are skipped."""
        headers: dict[str, str] = {}
        if self.from_seq is not None:
            headers["Last-Event-ID"] = str(self.from_seq)
            sep = "&" if "?" in self.url else "?"
            self.url = f"{self.url}{sep}from_seq={self.from_seq}"
        request = urllib.request.Request(self.url, headers=headers)
        try:
            response: Any = urllib.request.urlopen(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            raise ReferenceClientError(
                f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}"
            ) from exc
        stream: BinaryIO = response
        block = ""
        for raw_line in stream:
            line = raw_line.decode("utf-8", "replace")
            if line == "\n" or line == "\r\n":
                if block and not is_heartbeat(block):
                    yield parse_sse_block(block)
                block = ""
                continue
            block += line
        if block and not is_heartbeat(block):
            yield parse_sse_block(block)


# --------------------------------------------------------------------------- #
# WebSocket transport (minimal RFC 6455 client, stdlib only)
# --------------------------------------------------------------------------- #


def _ws_accept(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + _WS_GUID).encode("ascii")).digest()).decode("ascii")


def _encode_ws_frame(payload: bytes, opcode: int) -> bytes:
    """Encode one client->server frame (always masked, per RFC 6455)."""
    first = 0x80 | opcode  # FIN
    mask_key = os.urandom(4)
    length = len(payload)
    if length < 126:
        header = struct.pack("!BB", first, 0x80 | length)
    elif length < 65536:
        header = struct.pack("!BBH", first, 0x80 | 126, length)
    else:
        header = struct.pack("!BBQ", first, 0x80 | 127, length)
    masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    return header + mask_key + masked


def _decode_ws_frames(buffer: bytearray) -> tuple[list[tuple[int, bytes]], bytearray]:
    """Extract complete server->client frames from ``buffer``.

    Returns ``(frames, remaining)`` where each frame is ``(opcode, payload)``.
    Server frames are never masked; partial frames stay in ``buffer``.
    """
    frames: list[tuple[int, bytes]] = []
    offset = 0
    while True:
        if len(buffer) - offset < 2:
            break
        first, second = buffer[offset], buffer[offset + 1]
        opcode = first & 0x0F
        masked = second & 0x80
        length = second & 0x7F
        index = offset + 2
        if length == 126:
            if len(buffer) - index < 2:
                break
            length = struct.unpack("!H", buffer[index : index + 2])[0]
            index += 2
        elif length == 127:
            if len(buffer) - index < 8:
                break
            length = struct.unpack("!Q", buffer[index : index + 8])[0]
            index += 8
        if masked:
            if len(buffer) - index < 4:
                break
            mask_key = bytes(buffer[index : index + 4])
            index += 4
        if len(buffer) - index < length:
            break
        payload = bytes(buffer[index : index + length])
        if masked:
            payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
        frames.append((opcode, payload))
        offset = index + length
    return frames, bytearray(buffer[offset:])


class WsClient:
    """Minimal RFC 6455 client: connect, resume via ``from_seq``, yield frames."""

    def __init__(self, url: str, *, from_seq: int | None = None, timeout: float = 30.0) -> None:
        self.url = url
        self.from_seq = from_seq
        self.timeout = timeout
        self._sock: Any = None
        self._buffer = bytearray()

    def _connect(self) -> None:
        parts = urllib.parse.urlsplit(self.url)
        if parts.scheme not in ("ws", "wss"):
            raise ReferenceClientError(f"unsupported scheme {parts.scheme!r} (need ws/wss)")
        host = parts.hostname or "localhost"
        port = parts.port or (443 if parts.scheme == "wss" else 80)
        query = parts.query
        if self.from_seq is not None:
            query = f"{query}&from_seq={self.from_seq}" if query else f"from_seq={self.from_seq}"
        path = parts.path or "/"
        if query:
            path = f"{path}?{query}"

        sock = socket.create_connection((host, port), timeout=self.timeout)
        if parts.scheme == "wss":
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
        self._sock = sock

        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request_headers = [
            f"GET {path} HTTP/1.1",
            f"Host: {host}:{port}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {key}",
            "Sec-WebSocket-Version: 13",
            f"Origin: {parts.scheme.replace('ws', 'http')}://{host}:{port}",
            "",
            "",
        ]
        sock.sendall("\r\n".join(request_headers).encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                raise ReferenceClientError("connection closed during handshake")
            response += chunk
        head, _, body = response.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        status = lines[0].split(" ", 2)
        if len(status) < 2 or status[1] != "101":
            raise ReferenceClientError(f"handshake failed: {lines[0]}")
        headers = dict(line.split(": ", 1) for line in lines[1:] if ": " in line)
        if headers.get("Sec-WebSocket-Accept") != _ws_accept(key):
            raise ReferenceClientError("Sec-WebSocket-Accept mismatch")
        if body:
            self._buffer += body

    def _recv(self) -> None:
        assert self._sock is not None
        try:
            chunk = self._sock.recv(65536)
        except TimeoutError as exc:
            raise ReferenceClientError("read timed out") from exc
        if not chunk:
            raise ReferenceClientError("connection closed by server")
        self._buffer += chunk

    def frames(self) -> Any:
        """Yield :class:`Frame` objects; handles ping/pong and close frames."""
        self._connect()
        try:
            while True:
                frames, self._buffer = _decode_ws_frames(self._buffer)
                for opcode, payload in frames:
                    if opcode in (_OP_TEXT, _OP_BINARY):
                        try:
                            yield frame_from_dict(json.loads(payload.decode("utf-8")))
                        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                            raise ReferenceClientError(f"invalid frame payload: {exc}") from exc
                    elif opcode == _OP_PING:
                        self._sock.sendall(_encode_ws_frame(payload, _OP_PONG))
                    elif opcode == _OP_CLOSE:
                        return
                    elif opcode == _OP_CONT:
                        # Reference client: the server sends complete frames
                        # (coalescing happens server-side), so continuation
                        # frames are not expected; ignore defensively.
                        continue
                self._recv()
        finally:
            with contextlib.suppress(Exception):
                self._sock.close()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _print_frame(frame: Frame, pretty: bool) -> None:
    if pretty:
        print(f"[seq {frame.seq}] {frame.event_type} match={frame.match_id}")
        print(json.dumps(frame.payload, indent=2, default=str))
    else:
        print(json.dumps(frame.__dict__, separators=(",", ":"), default=str), flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cricket OS realtime reference client")
    parser.add_argument("--transport", choices=("sse", "ws"), default="sse")
    parser.add_argument(
        "--base-url", default=os.environ.get("CRICKET_BASE_URL", "http://localhost:8000")
    )
    parser.add_argument("--match", required=True, help="match UUID")
    parser.add_argument("--from-seq", type=int, default=None, help="resume position")
    parser.add_argument("--seconds", type=float, default=30.0, help="run for N seconds")
    parser.add_argument("--pretty", action="store_true", help="human-readable output")
    args = parser.parse_args(argv)

    base = args.base_url.rstrip("/")
    if args.transport == "sse":
        url = f"{base}/api/v1/sse/matches/{args.match}/"
        client: Any = SseClient(url, from_seq=args.from_seq, timeout=args.seconds)
    else:
        ws_base = base.replace("http://", "ws://").replace("https://", "wss://")
        url = f"{ws_base}/api/v1/ws/matches/{args.match}/"
        client = WsClient(url, from_seq=args.from_seq, timeout=args.seconds)

    deadline = time.monotonic() + args.seconds
    try:
        for frame in client.frames():
            if frame.event_type == "resync_required":
                print(
                    "RESYNC: the requested position is outside the retained window; "
                    "refetch the match snapshot and resubscribe from its meta.event_seq.",
                    file=sys.stderr,
                )
            _print_frame(frame, args.pretty)
            if time.monotonic() > deadline:
                return 0
    except ReferenceClientError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
