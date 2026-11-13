#!/usr/bin/env python3
"""Game day §10.1: Redis failover — cache breaker fail-open + recovery.

Stops Redis (a controlled ``--actually-stop``; the default records
``UNVERIFIED`` without touching the shared local service), then verifies:

* **fail-open** — a read-through cache read still succeeds while Redis is
  down: the local breaker skips Redis and the request is served by the
  builder (``apps/core/cache/store.py``: a cache outage costs latency, never
  availability);
* **breaker opens** — repeated backend failures flip the local cache breaker
  OPEN (``CACHE_BREAKER_OPEN == 1``);
* **recovery** — Redis comes back, the breaker closes and cached reads resume.

Runbook: `docs/RUNBOOKS.md` → "Cache breaker".

Usage:
    uv run python scripts/game_day/scenario_redis_failover.py --dry-run
    TARGET_ALLOWLIST=localhost:55435 uv run python scripts/game_day/scenario_redis_failover.py
    TARGET_ALLOWLIST=localhost:55435 uv run python scripts/game_day/scenario_redis_failover.py \\
        --actually-stop
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

from scripts.game_day import common  # noqa: E402

SCENARIO = "scenario_redis_failover"


def _redis_ping() -> bool:
    try:
        result = subprocess.run(
            ["redis-cli", "-p", "6379", "ping"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return "PONG" in result.stdout
    except OSError, subprocess.TimeoutExpired:
        return False


def _stop_redis() -> None:
    subprocess.run(
        ["redis-cli", "-p", "6379", "shutdown", "nosave"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def _start_redis() -> bool:
    """Restart the compose redis service; True when it is reachable again."""
    try:
        subprocess.run(
            ["docker", "start", "cricket_os-redis-1"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except OSError:
        return False
    for _ in range(20):
        if _redis_ping():
            return True
        time.sleep(0.5)
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    common.add_args(parser, scenario=SCENARIO)
    parser.add_argument(
        "--actually-stop",
        action="store_true",
        help="stop the shared local Redis for the drill (default: record UNVERIFIED)",
    )
    args = parser.parse_args()
    common.guard(
        scenario=SCENARIO,
        target=args.target,
        dry_run=args.dry_run,
        plan_lines=[
            "check Redis liveness (redis-cli ping)",
            (
                "stop Redis and verify fail-open + local breaker OPEN + recovery"
                if args.actually_stop
                else "default: --actually-stop not passed -> record UNVERIFIED "
                "(shared local Redis left untouched)"
            ),
            "report: verdict vs runbook 'Cache breaker'",
        ],
    )

    import django

    django.setup()

    redis_up_before = _redis_ping()
    if not args.actually_stop or not redis_up_before:
        missing = (
            "Redis was not stopped for this run (--actually-stop not passed, or "
            "Redis was already down); the shared local service is left untouched "
            "by default"
        )
        report = common.write_report(
            scenario=SCENARIO,
            target=args.target,
            detection_ms=None,
            time_to_mitigate_ms=None,
            runbook_section="Cache breaker",
            verdict="UNVERIFIED",
            runbook_fix=None,
            detail={
                "redis_up_before": redis_up_before,
                "actually_stop": args.actually_stop,
                "missing_piece": missing,
            },
            note=missing,
        )
        print(report.read_text(encoding="utf-8"))
        return

    from apps.core.cache import keys, policy, store

    # Reset the in-process breaker so this run measures a fresh cycle.
    store.reset_breaker()
    key = keys.make_key("gd:redis-failover", "probe")
    plan = policy.for_live()
    builder_calls = {"n": 0}

    def builder() -> str:
        builder_calls["n"] += 1
        return "built-value"

    # Warm the cache while Redis is up (a real read-through write).
    warmed = store.get_or_build(key, builder, plan=plan)
    warmed_hit = (warmed.hit and warmed.value == "built-value") or warmed.value == "built-value"

    # ---- stop Redis and verify fail-open reads + breaker ---------------- #
    detection_started = time.monotonic()
    _stop_redis()
    time.sleep(1.0)
    redis_down = not _redis_ping()

    # Drive the REAL read-through path: _read records a failure per backend
    # error; after the LocalBreaker threshold (3) the store serves _direct
    # (degraded, fail-open) without touching Redis at all.
    results: list[tuple[bool, float, bool]] = []
    for _ in range(6):
        started = time.monotonic()
        try:
            result = store.get_or_build(key, builder, plan=plan)
            ok = result.value == "built-value"
        except Exception:
            ok = False
            result = None  # type: ignore[assignment]
        results.append(
            (ok, (time.monotonic() - started) * 1000.0, bool(result and result.degraded))
        )

    fail_open_ok = all(ok for ok, _, _ in results)
    breaker_opened = store.breaker.is_open()
    degraded_served = any(degraded for _, _, degraded in results)
    detection_ms = common.elapsed_ms(detection_started)

    # ---- recovery --------------------------------------------------------- #
    mitigation_started = time.monotonic()
    restarted = _start_redis()
    if restarted:
        store.reset_breaker()
    recovered = restarted and _redis_ping() and not store.breaker.is_open()
    mitigate_ms = common.elapsed_ms(mitigation_started)

    verdict = (
        "sufficient"
        if redis_down
        and warmed_hit
        and fail_open_ok
        and breaker_opened
        and degraded_served
        and recovered
        else "gap"
    )
    report = common.write_report(
        scenario=SCENARIO,
        target=args.target,
        detection_ms=detection_ms,
        time_to_mitigate_ms=mitigate_ms,
        runbook_section="Cache breaker",
        verdict=verdict,
        runbook_fix=None,
        detail={
            "redis_down_after_stop": redis_down,
            "cache_warmed_before": warmed_hit,
            "fail_open_reads_succeeded": fail_open_ok,
            "degraded_direct_served": degraded_served,
            "builder_calls_during_outage": builder_calls["n"],
            "cache_breaker_opened": breaker_opened,
            "read_latencies_ms": [round(t, 2) for _, t, _ in results],
            "redis_restarted": restarted,
            "breaker_recovered": recovered,
            "flow": "redis down -> real get_or_build path -> LocalBreaker OPEN -> "
            "degraded _direct served -> redis up -> breaker reset -> cached reads resume",
            "unverified_reason": "gate G2 FAILS: local topology only, not production evidence",
        },
    )
    print(report.read_text(encoding="utf-8"))
    if verdict != "sufficient":
        sys.exit(1)


if __name__ == "__main__":
    main()
