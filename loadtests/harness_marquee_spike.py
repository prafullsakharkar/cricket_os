#!/usr/bin/env python3
"""§12 marquee-match spike — ramp to a peak RPS on one match (Task 5 §9.2).

Local: a few hundred RPS peak (default 300) — the §12 50k-RPS figure is **not**
run locally and is stated as such in every report. Ramps 0 → ``--rate`` over
``--ramp`` seconds against one match's scorecard shell, then holds for
``--hold``.

Usage:
    TARGET_ALLOWLIST=localhost:8000 uv run python loadtests/harness_marquee_spike.py \\
        --target localhost:8000 --match-id <uuid> --rate 300 --ramp 30 --hold 30
    uv run python loadtests/harness_marquee_spike.py --target localhost:8000 --dry-run

SLO: no 5xx, p95 < 500 ms; cache hit > 90% is not observable from outside the
origin here (no surrogate-key header is exposed to clients) → not-demonstrated
locally, recorded honestly.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loadtests import harness_common as hc  # noqa: E402

NAME = "harness_marquee_spike"
SLO_PEAK_RPS = 50_000  # the §12 production figure (NOT run locally)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    hc.add_common_args(
        parser,
        default_duration=60.0,
        default_rate=300.0,
        duration_help="total spike duration (ramp + hold), seconds",
        rate_help="peak RPS at the top of the ramp (local: hundreds, not 50k)",
    )
    parser.add_argument("--match-id", default=None, help="the one hot match's id (required to run)")
    parser.add_argument("--ramp", type=float, default=30.0, help="ramp-up seconds")
    parser.add_argument("--hold", type=float, default=30.0, help="hold-at-peak seconds")
    args = parser.parse_args()
    hc._positive(args.duration, "duration")
    hc._positive(args.rate, "rate")
    if args.ramp + args.hold > args.duration + 1e-9:
        parser.error("--ramp + --hold must not exceed --duration")
    if not args.match_id and not args.dry_run:
        parser.error("--match-id is required to run (omit only for --dry-run)")
    hc.guard(
        name=NAME,
        target=args.target,
        dry_run=args.dry_run,
        plan_lines=[
            f"ramp 0 -> {args.rate:g} RPS over {args.ramp:g}s, hold {args.hold:g}s",
            f"one match: /api/v1/matches/{args.match_id}/",
            f"peak is {args.rate:g} RPS locally; the §12 figure is {SLO_PEAK_RPS} RPS "
            "(stated, NOT run locally)",
            "SLO status UNVERIFIED (G2 gate fails; local topology only)",
        ],
    )

    base = f"http://{args.target}"
    url = f"{base}/api/v1/matches/{args.match_id}/"
    stats = hc.TimingStats()
    server_errors = 0
    throttled = 0
    started = hc.time_now()
    deadline = started + args.duration
    step = 0
    while True:
        now = hc.time_now()
        if now >= deadline:
            break
        elapsed = now - started
        rate = args.rate * (elapsed / args.ramp) if elapsed < args.ramp else args.rate
        ok, latency, status, _ = hc.http_get(url)
        stats.record(latency, ok=ok)
        if 500 <= status < 600:
            server_errors += 1
        if status == 429:
            throttled += 1
        step += 1
        # Clamp the sleep so the ramp start (rate ~ 0) does not stall the loop.
        hc.sleep(min(1.0 / rate, 1.0) if rate > 0 else 0.0)

    measured = stats.summary()
    measured["server_5xx"] = float(server_errors)
    measured["throttled_429"] = float(throttled)
    status = "demonstrated" if server_errors == 0 and measured["p95_ms"] < 500 else "failed"
    report = {
        "name": NAME,
        "target": args.target,
        "dry_run": False,
        "peak_rps": args.rate,
        "slo_50k_rps": {"stated": SLO_PEAK_RPS, "run_locally": False},
        "cache_hit_ratio": {
            "measured": None,
            "status": "not-demonstrated",
            "note": "origin cache hit ratio is not observable from a client; "
            "surrogate-key purge is verified by the cache suite instead",
        },
        "env": hc.env_tag(),
        "slo": hc.slo_block(
            name=hc.SLO_MARQUEE[0],
            target=hc.SLO_MARQUEE[1],
            measured=measured,
            status=status,
            implication=(
                "any 5xx or p95 >= 500 ms on one hot match locally means the "
                "cache/CDN tier must absorb the marquee spike — not the app — "
                "re-verify on staging at the real 50k figure before launch."
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
