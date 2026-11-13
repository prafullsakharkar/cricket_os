#!/usr/bin/env python3
"""§12 deliveries feed — cursor pagination under load (plan Task 5 §9.2).

Walks ``/api/v1/matches/{id}/deliveries?limit=50`` following the next-page
cursor for ``--duration`` seconds, at ``--rate`` pages-per-second, measuring
per-page latency. One page per tick (the cursor walk is paced by the request
rate, not a free-fall).

Usage:
    TARGET_ALLOWLIST=localhost:8000 uv run python loadtests/harness_deliveries_feed.py \\
        --target localhost:8000 --match-id <uuid> --duration 30 --rate 10
    uv run python loadtests/harness_deliveries_feed.py --target localhost:8000 --dry-run

SLO: cursor pagination under spike p95 < 300 ms (UNVERIFIED: local topology).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loadtests import harness_common as hc  # noqa: E402

NAME = "harness_deliveries_feed"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    hc.add_common_args(
        parser,
        default_duration=30.0,
        default_rate=2.0,
        duration_help="pagination run duration, seconds",
        rate_help="pages per second (the deliveries scope is throttled at 2/s, "
        "API_SPEC §7; above that the throttle itself caps the load)",
    )
    parser.add_argument(
        "--match-id", default=None, help="the match whose deliveries are walked (required to run)"
    )
    parser.add_argument("--innings", type=int, default=1, help="innings number to walk")
    parser.add_argument("--limit", type=int, default=50, help="page size")
    args = parser.parse_args()
    hc._positive(args.duration, "duration")
    hc._positive(args.rate, "rate")
    if args.limit <= 0:
        parser.error("--limit must be positive")
    if not args.match_id and not args.dry_run:
        parser.error("--match-id is required to run (omit only for --dry-run)")
    hc.guard(
        name=NAME,
        target=args.target,
        dry_run=args.dry_run,
        plan_lines=[
            f"cursor-walk /api/v1/matches/{args.match_id}/innings/{args.innings}/"
            f"deliveries?limit={args.limit}",
            f"at {args.rate:g} pages/s for {args.duration:g}s",
            "report: page p50/p95/p99/error-rate vs p95<300ms",
            "SLO status UNVERIFIED (G2 gate fails; local topology only)",
        ],
    )

    base = f"http://{args.target}"
    stats = hc.TimingStats()
    first_url = (
        f"{base}/api/v1/matches/{args.match_id}/innings/{args.innings}/deliveries?"
        f"{urlencode({'limit': args.limit})}"
    )
    for _ in hc.paced_loop(args.duration, args.rate):
        ok, latency, status, body = hc.http_get(first_url)
        stats.record(latency, ok=ok)
        # Follow at most one cursor hop per tick so pacing stays meaningful.
        if ok and status == 200 and body:
            try:
                payload = json.loads(body)
                meta = payload.get("meta") or {}
                next_cursor = (
                    meta.get("next_cursor")
                    or (payload.get("data") or {}).get("next_cursor")
                    or payload.get("next_cursor")
                )
            except ValueError, AttributeError:
                next_cursor = None
            if next_cursor:
                _, hop_latency, hop_status, _ = hc.http_get(
                    f"{base}/api/v1/matches/{args.match_id}/innings/{args.innings}/"
                    f"deliveries?{urlencode({'limit': args.limit, 'cursor': next_cursor})}"
                )
                stats.record(hop_latency, ok=hop_status < 400)

    measured = stats.summary()
    status = (
        "demonstrated" if measured["p95_ms"] < 300 and measured["error_rate"] < 0.001 else "failed"
    )
    report = {
        "name": NAME,
        "target": args.target,
        "dry_run": False,
        "page_size": args.limit,
        "env": hc.env_tag(),
        "slo": hc.slo_block(
            name=hc.SLO_DELIVERIES[0],
            target=hc.SLO_DELIVERIES[1],
            measured=measured,
            status=status,
            implication=(
                "deliveries pagination p95 >= 300 ms under load means the cursor "
                "query plan needs an index check on the partition key — re-run on "
                "staging with a production-sized deliveries table."
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
