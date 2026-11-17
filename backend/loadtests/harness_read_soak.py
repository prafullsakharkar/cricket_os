#!/usr/bin/env python3
"""§12 read soak — 1× Growth RPS against the match feeds (plan Task 5 §9.2).

Local: scaled-down RPS (default 20 RPS ≈ 0.013× of the ~1,500 RPS Growth
figure — ``CRICKET_PERFORMANCE_AND_SCALING.md`` §1); the scale factor is
recorded in the report. Cycles ``/api/v1/matches/live``, ``/upcoming``,
``/recent`` and one scorecard shell for ``--duration`` seconds at ``--rate`` RPS.

Usage:
    TARGET_ALLOWLIST=localhost:8000 uv run python loadtests/harness_read_soak.py \\
        --target localhost:8000 --duration 60 --rate 20
    uv run python loadtests/harness_read_soak.py --target localhost:8000 --dry-run

SLO: read p95 < 200 ms cached, error < 0.1% (UNVERIFIED: local topology only).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loadtests import harness_common as hc  # noqa: E402

GROWTH_RPS = 1_500.0  # §12 "1x Growth RPS" = the Growth-stage ~1,500 peak read RPS
# (CRICKET_PERFORMANCE_AND_SCALING.md §1). The Initial-stage figure is ~150.
NAME = "harness_read_soak"
PATHS = ("/api/v1/matches/live", "/api/v1/matches/upcoming", "/api/v1/matches/recent")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    hc.add_common_args(
        parser,
        default_duration=60.0,
        default_rate=20.0,
        duration_help="soak duration in seconds (local: short; the full soak is soak.py)",
        rate_help="sustained RPS (local: scaled down; 150 = 1x Growth)",
    )
    parser.add_argument("--match-id", default=None, help="scorecard shell to also hit")
    args = parser.parse_args()
    hc._positive(args.duration, "duration")
    hc._positive(args.rate, "rate")
    scale = args.rate / GROWTH_RPS
    hc.guard(
        name=NAME,
        target=args.target,
        dry_run=args.dry_run,
        plan_lines=[
            f"rate {args.rate} RPS for {args.duration}s = {scale:.2f}x Growth RPS",
            f"paths: {', '.join(PATHS)}"
            + (f" + /api/v1/matches/{args.match_id}/" if args.match_id else ""),
            "report: read p50/p95/p99/error-rate vs p95<200ms, err<0.1%",
            "SLO status UNVERIFIED (G2 gate fails; local topology only)",
        ],
    )

    base = f"http://{args.target}"
    stats = hc.TimingStats()
    paths = list(PATHS)
    if args.match_id:
        paths.append(f"/api/v1/matches/{args.match_id}/")
    for _ in hc.paced_loop(args.duration, args.rate):
        path = paths[stats.requests % len(paths)]
        ok, latency, status, _ = hc.http_get(base + path)
        stats.record(latency, ok=ok)

    measured = stats.summary()
    status = (
        "demonstrated" if measured["p95_ms"] < 200 and measured["error_rate"] < 0.001 else "failed"
    )
    report = {
        "name": NAME,
        "target": args.target,
        "dry_run": False,
        "scale_factor_growth_rps": round(scale, 3),
        "env": hc.env_tag(),
        "slo": hc.slo_block(
            name=hc.SLO_READ_SOAK[0],
            target=hc.SLO_READ_SOAK[1],
            measured=measured,
            status=status,
            implication=(
                "read p95 > 200 ms or error rate >= 0.1% locally means the "
                "CDN/replica tiers in the capacity plan are not optional — "
                "re-run on staging before launch."
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
