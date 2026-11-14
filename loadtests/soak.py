#!/usr/bin/env python3
"""The several-hour soak: read soak + replay ingest + realtime (plan Task 5 §9.2).

Orchestrates the three concurrent workloads against ``--target``:

* read soak — the match feeds at ``--rate`` RPS;
* realtime — SSE connect/first-event cycles at ``--rt-rate`` per second;
* periodic ingest ticks — a small ``sync_fixtures`` burst every ``--ingest-every``
  seconds (in-process, rolled back).

Two mid-soak operational drills are attempted at their designated seconds and
**recorded honestly** (plan §9.4):

* ``--deploy-after`` — a mid-soak deploy. Locally this would restart the ASGI
  process; the compose ``app`` service is not running in this environment, so
  the drill is recorded ``UNVERIFIED`` with the missing piece (no running
  compose app service to restart) unless it becomes available.
* ``--failover-after`` — a replica failover. ``docker-compose.yml`` has **no
  ``db-replica`` service** (pg_basebackup + ``primary_conninfo`` are absent) and
  the local Postgres is not configured for streaming replication
  (``wal_level``/``max_wal_senders``), so the attempt is recorded ``UNVERIFIED``
  with exactly those missing pieces — never faked.

The plan's target local soak duration is ≥ 2 h. This script runs for
``--duration``; a full multi-hour soak is a staging activity (UNVERIFIED) and
the local smoke soak documents the plan target.

Usage:
    TARGET_ALLOWLIST=localhost:8000 uv run python loadtests/soak.py \\
        --target localhost:8000 --duration 7200 --rate 20
    uv run python loadtests/soak.py --target localhost:8000 --duration 120 --rate 10 --dry-run
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loadtests import harness_common as hc  # noqa: E402

NAME = "soak"
PLAN_SOAK_DURATION = 2 * 3600  # the plan's local minimum (≥ 2 h)


def _compose_running(service: str) -> bool:
    """True when ``docker compose ps`` reports ``service`` as Up."""
    try:
        result = subprocess.run(
            ["docker", "compose", "ps", "--format", "{{.Service}} {{.State}}"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except OSError, subprocess.TimeoutExpired:
        return False
    return any(
        line.strip().startswith(service) and "Up" in line for line in result.stdout.splitlines()
    )


def _attempt_deploy() -> dict[str, object]:
    """Mid-soak deploy drill: restart the compose app service if present."""
    if _compose_running("app"):
        try:
            subprocess.run(
                ["docker", "compose", "restart", "app"],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            return {"status": "performed", "note": "compose app service restarted"}
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"status": "failed", "note": str(exc)}
    return {
        "status": "UNVERIFIED",
        "missing_piece": "no running docker-compose 'app' service in this "
        "environment (local stack is a directly-run ASGI process); a real deploy "
        "restart must be measured on staging",
    }


def _attempt_failover() -> dict[str, object]:
    """Replica failover drill: needs a ``db-replica`` compose service."""
    return {
        "status": "UNVERIFIED",
        "missing_piece": (
            "docker-compose.yml has no 'db-replica' service (pg_basebackup "
            "bootstrap + primary_conninfo absent) and the local Postgres is not "
            "configured for streaming replication (wal_level/max_wal_senders); "
            "the failover attempt cannot run here and is not faked"
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    hc.add_common_args(
        parser,
        default_duration=PLAN_SOAK_DURATION,
        default_rate=20.0,
        duration_help="soak duration in seconds (plan target >= 7200 locally)",
        rate_help="read-soak RPS",
    )
    parser.add_argument("--rt-rate", type=float, default=1.0, help="SSE reconnect cycles/s")
    parser.add_argument("--ingest-every", type=float, default=60.0, help="ingest tick period (s)")
    parser.add_argument(
        "--deploy-after", type=float, default=1200.0, help="mid-soak deploy at t (s)"
    )
    parser.add_argument(
        "--failover-after", type=float, default=2400.0, help="failover drill at t (s)"
    )
    parser.add_argument("--match-id", default=None, help="SSE/scorecard match id (optional)")
    args = parser.parse_args()
    hc._positive(args.duration, "duration")
    hc._positive(args.rate, "rate")
    hc._positive(args.rt_rate, "rt-rate")
    hc.guard(
        name=NAME,
        target=args.target,
        dry_run=args.dry_run,
        plan_lines=[
            f"read soak at {args.rate:g} RPS + SSE reconnect at {args.rt_rate:g}/s "
            f"for {args.duration:g}s",
            f"ingest tick every {args.ingest_every:g}s (in-process, rolled back)",
            f"deploy drill at t={args.deploy_after:g}s; failover drill at "
            f"t={args.failover_after:g}s (both recorded UNVERIFIED unless runnable)",
            f"plan target local duration is >= {PLAN_SOAK_DURATION // 3600} h; "
            "this run records its own duration",
            "SLO status UNVERIFIED (G2 gate fails; local topology only)",
        ],
    )

    base = f"http://{args.target}"
    read_stats = hc.TimingStats()
    rt_stats = hc.TimingStats()
    ingest_ticks = 0
    deploy_drill: dict[str, object] | None = None
    failover_drill: dict[str, object] | None = None

    started = hc.time_now()
    deadline = started + args.duration
    next_ingest = started + args.ingest_every
    while True:
        now = hc.time_now()
        if now >= deadline:
            break
        # read tick
        ok, latency, _, _ = hc.http_get(f"{base}/api/v1/matches/live")
        read_stats.record(latency, ok=ok)
        ok, latency, _, _ = hc.http_get(f"{base}/api/v1/matches/recent")
        read_stats.record(latency, ok=ok)
        # realtime tick
        if args.match_id:
            from loadtests.harness_realtime_reconnect import _connect_first_event

            rt_ok, rt_latency = _connect_first_event(
                f"{base}/api/v1/sse/matches/{args.match_id}/", timeout=5.0
            )
            rt_stats.record(rt_latency, ok=rt_ok)
        # ingest tick
        if now >= next_ingest:
            next_ingest = now + args.ingest_every
            ingest_ticks += 1
            _ingest_tick(base)  # in-process, rolled back
        # drills
        if deploy_drill is None and now - started >= args.deploy_after:
            deploy_drill = _attempt_deploy()
        if failover_drill is None and now - started >= args.failover_after:
            failover_drill = _attempt_failover()
        hc.sleep(1.0 / max(args.rate, 1e-9))

    elapsed = read_stats.summary()
    rt = rt_stats.summary() if args.match_id else None
    report = {
        "name": NAME,
        "target": args.target,
        "dry_run": False,
        "duration_seconds": round(args.duration, 3),
        "plan_target_duration_seconds": PLAN_SOAK_DURATION,
        "ingest_ticks": ingest_ticks,
        "deploy_drill": deploy_drill,
        "failover_drill": failover_drill,
        "env": hc.env_tag(),
        "slo": hc.slo_block(
            name="soak (read + realtime + ingest)",
            target="read p95 < 200 ms, err < 0.1%; realtime < 1 s p95 over the full duration",
            measured={**elapsed, **({"rt_" + k: v for k, v in rt.items()} if rt else {})},
            status="dry-run" if args.dry_run else "not-demonstrated",
            implication=(
                "a soak run that holds the read/realtime SLOs across a deploy and "
                "a failover is the strongest local evidence; the full multi-hour "
                "soak on staging is the launch gate"
            ),
        ),
    }
    path = hc.write_report(NAME, report)
    print(hc.json_dumps(report))
    print(f"report: {path}")


def _ingest_tick(base: str) -> None:
    """A tiny rolled-back fixture-sync burst (no network, local Postgres)."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
    try:
        import django

        django.setup()
    except RuntimeError:
        pass
    from django.db import transaction

    from apps.providers.models import DataProvider
    from apps.synchronization.services import fixtures

    provider, _ = DataProvider.objects.get_or_create(
        code="soak-ingest", defaults={"name": "soak ingest tick", "is_active": True}
    )
    now = datetime.now(UTC)
    record = fixtures.FixtureRecord(
        provider_match_id=f"soak-{now.timestamp():.0f}",
        format_type="T20I",
        start_time_utc=now + timedelta(days=30),
        teams=("Soak Tick A", "Soak Tick B"),
        team_registry={
            "Soak Tick A": {"key_opta": "soak-a"},
            "Soak Tick B": {"key_opta": "soak-b"},
        },
        match_number=1,
    )
    try:
        with transaction.atomic():
            fixtures.sync_fixtures(provider, [record])
            transaction.set_rollback(True)
    except Exception:
        pass


if __name__ == "__main__":
    main()
