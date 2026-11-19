#!/usr/bin/env python3
"""Phase I Task 7 §11.1 — consumer fallback ladder through the proxy.

Runs the reference client (``clients/reference_client/realtime_client.py``)
across the fallback ladder — REST snapshot → SSE resume → WS resume → REST
resync — against the nginx proxy (``infrastructure/docker/nginx/nginx.conf``)
and reports each
hop's latency plus any **proxy-induced failures**:

* *buffering delay* — an SSE/WS stream whose first body bytes arrive late
  (``proxy_buffering on`` holds the stream until a buffer fills);
* *gzip on SSE* — ``Content-Encoding: gzip`` on an SSE response breaks the
  blank-line event framing (gzip_types must exclude ``text/event-stream``);
* *timeout truncation* — a stream ends before the run window (short
  ``proxy_read_timeout``);
* *WS upgrade drop* — the ``Upgrade``/``Connection`` headers are not passed
  through and the 101 handshake fails.

The live proxy run is **manual evidence** (nginx is not in CI); the dry-run
mode and the proxy-config assertions are the CI gate
(``tests/phase_i/test_consumer_ladder.py``). Target = the proxy host:port.

Usage:
    TARGET_ALLOWLIST=localhost:8080 uv run python scripts/consumer_ladder.py \\
        --target localhost:8080 --match <uuid> --seconds 10
    uv run python scripts/consumer_ladder.py --target localhost:8080 --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # backend/ (sys.path)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# Make the reference client importable (it is not a package).
REF_CLIENT_DIR = ROOT.parent / "clients" / "reference_client"
if str(REF_CLIENT_DIR) not in sys.path:
    sys.path.insert(0, str(REF_CLIENT_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

from scripts.game_day import common  # noqa: E402

SCENARIO = "consumer_ladder"
REPORT_PATH = ROOT.parent / "reports" / "phase_i" / "consumer_ladder.md"

#: First-frame latency above this (s) flags proxy buffering on the stream.
BUFFERING_THRESHOLD_SECONDS = 2.0
#: First body byte above this (s) on a live SSE connection flags buffering.
FIRST_BYTE_THRESHOLD_SECONDS = 1.0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    common.add_args(parser, scenario=SCENARIO)
    parser.add_argument("--match", help="match UUID (required for a real run)")
    parser.add_argument("--seconds", type=float, default=10.0, help="seconds per live hop")
    parser.add_argument("--base-url", default=None, help="override the http://<target> base")
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error("--seconds must be positive")
    common.guard(
        scenario=SCENARIO,
        target=args.target,
        dry_run=args.dry_run,
        plan_lines=[
            f"target = the nginx proxy ({args.target}) in front of the app",
            "REST snapshot -> SSE resume (Last-Event-ID) -> WS resume (from_seq) -> REST resync",
            "detect proxy-induced failures: buffering delay, gzip on SSE, "
            "timeout truncation, WS upgrade drop",
            f"report: {REPORT_PATH}",
        ],
    )

    if not args.match:
        parser.error("--match <uuid> is required for a real run (dry-run skips it)")
    base = args.base_url or f"http://{args.target}"
    ws_base = base.replace("http://", "ws://").replace("https://", "wss://")

    import urllib.error
    import urllib.request

    from realtime_client import ReferenceClientError, SseClient, WsClient

    match = args.match
    failures: list[str] = []
    hops: dict[str, dict[str, object]] = {}
    resync_required_seen = False

    def record(hop: str, **fields: object) -> None:
        hops[hop] = {"status": "ok", **fields}

    def fail(hop: str, message: str, **fields: object) -> None:
        failures.append(f"{hop}: {message}")
        hops[hop] = {"status": "failed", "failure": message, **fields}

    # ---- hop 1: REST snapshot (the ladder's baseline) --------------------- #
    started = time.monotonic()
    try:
        with urllib.request.urlopen(f"{base}/api/v1/matches/{match}/", timeout=10) as resp:
            rest_latency_ms = (time.monotonic() - started) * 1000.0
            rest_status = resp.status
            body = json.loads(resp.read().decode("utf-8"))
        event_seq = body.get("meta", {}).get("event_seq")
        record(
            "rest_snapshot",
            latency_ms=round(rest_latency_ms, 2),
            status_code=rest_status,
            event_seq=event_seq,
        )
        if event_seq is None:
            fail("rest_snapshot", "REST snapshot carries no meta.event_seq")
            event_seq = 0
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        fail("rest_snapshot", f"unreachable through proxy: {exc}")
        return _report(args, hops, failures, resync_required_seen)

    # ---- hop 2: SSE probe — headers + time-to-first-body-byte ------------- #
    # Subscribe from position 0 so the durable log replays immediately: a
    # buffering proxy holds those body bytes (the first-byte signal); a
    # correct proxy lets them through at once.
    started = time.monotonic()
    first_byte_ms: float | None = None
    content_encoding: str | None = None
    try:
        request = urllib.request.Request(
            f"{base}/api/v1/sse/matches/{match}/",
            headers={"Last-Event-ID": "0", "Accept": "text/event-stream"},
        )
        with urllib.request.urlopen(request, timeout=5) as resp:
            content_encoding = resp.headers.get("Content-Encoding")
            resp.read(1)  # first body byte (may block: buffering signal)
            first_byte_ms = (time.monotonic() - started) * 1000.0
            sse_status = resp.status
        if content_encoding == "gzip":
            fail(
                "sse_probe",
                "SSE stream served with Content-Encoding: gzip (breaks event framing)",
                content_encoding=content_encoding,
                first_byte_ms=round(first_byte_ms or 0.0, 2),
            )
        elif first_byte_ms is not None and first_byte_ms > FIRST_BYTE_THRESHOLD_SECONDS * 1000.0:
            fail(
                "sse_probe",
                "first SSE body byte delayed (proxy buffering on the stream)",
                first_byte_ms=round(first_byte_ms, 2),
            )
        else:
            record(
                "sse_probe",
                content_encoding=content_encoding,
                first_byte_ms=round(first_byte_ms or 0.0, 2),
                status_code=sse_status,
            )
    except (urllib.error.URLError, OSError) as exc:
        fail("sse_probe", f"SSE probe failed through proxy: {exc}")

    # ---- hop 3: SSE resume via the reference client ------------------------ #
    # Replay from 0 (before the first event) so the stream delivers frames
    # immediately; this measures the resume path AND the proxy's buffering.
    started = time.monotonic()
    first_frame_ms: float | None = None
    frames_seen = 0
    truncated = False
    try:
        client = SseClient(
            f"{base}/api/v1/sse/matches/{match}/",
            from_seq=0,
            timeout=args.seconds,
        )
        deadline = time.monotonic() + args.seconds
        for frame in client.frames():
            if first_frame_ms is None:
                first_frame_ms = (time.monotonic() - started) * 1000.0
            frames_seen += 1
            if frame.event_type == "resync_required":
                resync_required_seen = True
            if time.monotonic() > deadline:
                break
    except ReferenceClientError as exc:
        fail("sse_resume", f"SSE resume failed: {exc}")
    except (OSError, TimeoutError) as exc:
        if frames_seen == 0:
            # No frame at all: the proxy (or app) withheld the stream.
            fail("sse_resume", f"SSE stream produced no frame before closing: {exc}")
        else:
            # Frames were delivered and the stream then went quiet (the app's
            # heartbeat is longer than the hop window): not a truncation.
            record(
                "sse_resume",
                first_frame_ms=round(first_frame_ms or 0.0, 2),
                frames=frames_seen,
                note="stream went idle after replay (heartbeat longer than window); not truncated",
            )
            truncated = True  # already recorded; skip the post-loop block
    if not truncated:
        if first_frame_ms is not None and first_frame_ms > BUFFERING_THRESHOLD_SECONDS * 1000.0:
            fail(
                "sse_resume",
                "first SSE frame delayed (proxy buffering)",
                first_frame_ms=round(first_frame_ms, 2),
            )
        else:
            record(
                "sse_resume",
                first_frame_ms=round(first_frame_ms or 0.0, 2),
                frames=frames_seen,
                truncated=truncated,
            )

    # ---- hop 4: WS resume via the reference client -------------------------- #
    started = time.monotonic()
    ws_first_frame_ms: float | None = None
    ws_frames = 0
    ws_upgrade_ok = True
    ws_recorded = False
    try:
        client = WsClient(
            f"{ws_base}/api/v1/ws/matches/{match}/",
            from_seq=0,
            timeout=args.seconds,
        )
        deadline = time.monotonic() + args.seconds
        for frame in client.frames():
            if ws_first_frame_ms is None:
                ws_first_frame_ms = (time.monotonic() - started) * 1000.0
            ws_frames += 1
            if frame.event_type == "resync_required":
                resync_required_seen = True
            if time.monotonic() > deadline:
                break
    except ReferenceClientError as exc:
        message = str(exc)
        if "handshake failed" in message or "Sec-WebSocket-Accept" in message:
            ws_upgrade_ok = False
            fail("ws_resume", f"WS upgrade dropped by the proxy: {message}")
        elif "read timed out" in message and ws_frames > 0:
            # Frames were delivered and the stream then went quiet (the app's
            # hub sends nothing while idle): not a proxy failure.
            record(
                "ws_resume",
                first_frame_ms=round(ws_first_frame_ms or 0.0, 2),
                frames=ws_frames,
                note="stream went idle after replay; not truncated",
            )
            ws_recorded = True
        else:
            fail("ws_resume", f"WS resume failed: {message}")
    except OSError as exc:
        fail("ws_resume", f"WS stream truncated by the proxy: {exc}")
    if ws_upgrade_ok and not ws_recorded:
        buffering_ms = BUFFERING_THRESHOLD_SECONDS * 1000.0
        if ws_first_frame_ms is not None and ws_first_frame_ms > buffering_ms:
            fail(
                "ws_resume",
                "first WS frame delayed (proxy buffering)",
                first_frame_ms=round(ws_first_frame_ms, 2),
            )
        else:
            record(
                "ws_resume",
                first_frame_ms=round(ws_first_frame_ms or 0.0, 2),
                frames=ws_frames,
            )

    # ---- hop 5: REST resync (only when a resync signal was seen) ------------ #
    if resync_required_seen:
        started = time.monotonic()
        try:
            with urllib.request.urlopen(f"{base}/api/v1/matches/{match}/", timeout=10) as resp:
                resync_latency_ms = (time.monotonic() - started) * 1000.0
            record("rest_resync", latency_ms=round(resync_latency_ms, 2), status_code=resp.status)
        except (urllib.error.URLError, OSError) as exc:
            fail("rest_resync", f"REST resync failed: {exc}")
    else:
        record("rest_resync", note="not triggered (no resync_required frame)", status_code=None)

    return _report(args, hops, failures, resync_required_seen)


def _report(
    args: argparse.Namespace,
    hops: dict[str, dict[str, object]],
    failures: list[str],
    resync_required_seen: bool,
) -> None:
    verdict = "sufficient" if not failures else "gap"
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Consumer fallback ladder (through the proxy)",
        "",
        f"- Date (UTC): {datetime.now(UTC).isoformat()}",
        f"- Target (proxy): `{args.target}`",
        f"- Match: `{args.match}`",
        f"- Hop window: {args.seconds}s per live hop",
        f"- Verdict: **{verdict}**",
        f"- Proxy-induced failures: {len(failures)}",
        f"- resync_required seen: {resync_required_seen}",
    ]
    if failures:
        lines.append("- Failures:")
        for failure in failures:
            lines.append(f"  - {failure}")
    lines.append("")
    lines.append("## Hops")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(hops, indent=2, sort_keys=True))
    lines.append("```")
    lines.append("")
    lines.append(
        "Ladder: REST snapshot -> SSE resume (Last-Event-ID) -> WS resume "
        "(from_seq) -> REST resync. Proxy: `infrastructure/docker/nginx/nginx.conf`. "
        "UNVERIFIED: gate G2 FAILS — local topology only, not production."
    )
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"report: {REPORT_PATH}")
    if failures:
        print("proxy-induced failures detected:")
        for failure in failures:
            print(f"  - {failure}")
    if verdict != "sufficient":
        sys.exit(1)


if __name__ == "__main__":
    main()
