#!/usr/bin/env python3
"""§12 realtime — SSE connect/reconnect churn (plan Task 5 §9.2).

Connects to ``/api/v1/sse/matches/{id}/``, reads the first bytes of the stream
(one event), disconnects, and reconnects — repeated at ``--rate`` cycles per
second for ``--duration`` seconds. Measures connect→first-event latency and
reconnect p95. Local subscriber counts are in the low hundreds, far below the
§12 1M-connections figure (stated, NOT run locally).

Usage:
    TARGET_ALLOWLIST=localhost:8000 uv run python loadtests/harness_realtime_reconnect.py \\
        --target localhost:8000 --match-id <uuid> --duration 30 --rate 2
    uv run python loadtests/harness_realtime_reconnect.py --target localhost:8000 --dry-run

SLO: event latency < 1 s p95, reconnect < 5 s p95 (UNVERIFIED: local topology).
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loadtests import harness_common as hc  # noqa: E402

NAME = "harness_realtime_reconnect"
SLO_CONNECTIONS = 1_000_000  # the §12 figure (NOT run locally)


def _connect_first_event(url: str, timeout: float) -> tuple[bool, float]:
    """Connect; returns ``(ok, connect_latency_ms)``.

    ``connect_latency`` is the time to receive the HTTP response headers — the
    quantity a reconnect actually measures (the SSE stream then stays open).
    Publish→delivery latency is covered by the in-process realtime suite, not
    this harness, so the connection is closed immediately after the headers.
    """
    started = hc.time_now()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            if resp.status >= 400:
                return False, (hc.time_now() - started) * 1000.0
            return True, (hc.time_now() - started) * 1000.0
    except urllib.error.URLError, TimeoutError, OSError:
        return False, (hc.time_now() - started) * 1000.0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    hc.add_common_args(
        parser,
        default_duration=30.0,
        default_rate=2.0,
        duration_help="reconnect churn duration, seconds",
        rate_help="connect/reconnect cycles per second",
    )
    parser.add_argument("--match-id", default=None, help="the match's SSE feed (required to run)")
    parser.add_argument("--read-timeout", type=float, default=5.0, help="per-connect read timeout")
    args = parser.parse_args()
    hc._positive(args.duration, "duration")
    hc._positive(args.rate, "rate")
    if not args.match_id and not args.dry_run:
        parser.error("--match-id is required to run (omit only for --dry-run)")
    hc.guard(
        name=NAME,
        target=args.target,
        dry_run=args.dry_run,
        plan_lines=[
            f"SSE connect/first-frame/disconnect at {args.rate:g}/s for "
            f"{args.duration:g}s on /api/v1/sse/matches/{args.match_id}/",
            "local subscriber counts are low hundreds; the §12 figure is "
            f"{SLO_CONNECTIONS} connections (stated, NOT run locally)",
            "report: connect->headers p50/p95/p99/error-rate (the reconnect "
            "metric); publish->delivery latency is the in-process realtime suite",
            "SLO status UNVERIFIED (G2 gate fails; local topology only)",
        ],
    )

    url = f"http://{args.target}/api/v1/sse/matches/{args.match_id}/"
    stats = hc.TimingStats()
    for _ in hc.paced_loop(args.duration, args.rate):
        ok, latency = _connect_first_event(url, args.read_timeout)
        stats.record(latency, ok=ok)

    measured = stats.summary()
    status = (
        "demonstrated" if measured["p95_ms"] < 1000 and measured["error_rate"] < 0.001 else "failed"
    )
    report = {
        "name": NAME,
        "target": args.target,
        "dry_run": False,
        "connections_stated_slo": SLO_CONNECTIONS,
        "connections_run_locally": False,
        "env": hc.env_tag(),
        "slo": hc.slo_block(
            name=hc.SLO_REALTIME[0],
            target=hc.SLO_REALTIME[1],
            measured=measured,
            status=status,
            implication=(
                "reconnect p95 >= 5 s or event latency >= 1 s under churn means "
                "the SSE hub/coalescer needs capacity or the CDN edge must "
                "absorb reconnects — re-run on staging at scale."
            ),
        ),
    }
    path = hc.write_report(NAME, report)
    print(hc.json_dumps(report))
    print(f"report: {path}")
    if status != "demonstrated":
        sys.exit(1)


if __name__ == "__main__":
    main()
