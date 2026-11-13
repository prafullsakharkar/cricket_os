#!/usr/bin/env python3
"""Game day §10.1: replica loss — reads continue on the primary.

The local stack has **no replica** (plan §9.4: ``docker-compose.yml`` has no
``db-replica`` service and the local Postgres is not configured for streaming
replication). Per the plan's honest fallback this scenario records
``UNVERIFIED`` with the exact missing piece instead of faking a failover, and
verifies the only thing it can: the primary still serves reads (the
read-continuity property a replica loss must not break).

Runbook: `docs/RUNBOOKS.md` → "Backup & restore (replica loss)" (added by this
task — a runbook gap the scenario exposes).

Usage:
    TARGET_ALLOWLIST=localhost:55435 uv run python scripts/game_day/scenario_replica_loss.py
    uv run python scripts/game_day/scenario_replica_loss.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

from scripts.game_day import common  # noqa: E402

SCENARIO = "scenario_replica_loss"


def _has_db_replica_service() -> bool:
    """True when docker-compose declares a db-replica service."""
    try:
        result = subprocess.run(
            ["docker", "compose", "config", "--services"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except OSError, subprocess.TimeoutExpired:
        return False
    return "db-replica" in result.stdout.split()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    common.add_args(parser, scenario=SCENARIO)
    args = parser.parse_args()
    common.guard(
        scenario=SCENARIO,
        target=args.target,
        dry_run=args.dry_run,
        plan_lines=[
            "check for a db-replica service (pg_basebackup + primary_conninfo)",
            "if none: record UNVERIFIED with the exact missing piece (no faked failover)",
            "verify primary reads continue (read-continuity property)",
            "report: verdict vs runbook 'Backup & restore (replica loss)'",
        ],
    )

    import django

    django.setup()

    import time

    from apps.matches.models import Match

    replica_service = _has_db_replica_service()

    # Read-continuity: the primary answers a simple read.
    started = time.monotonic()
    match_count = Match.objects.count()
    read_ms = common.elapsed_ms(started)

    missing = (
        "docker-compose.yml has no 'db-replica' service (pg_basebackup bootstrap "
        "and primary_conninfo absent) and the local Postgres is not configured "
        "for streaming replication (wal_level/max_wal_senders); a replica-loss "
        "failover cannot be attempted here and is not faked"
    )
    report = common.write_report(
        scenario=SCENARIO,
        target=args.target,
        detection_ms=None,
        time_to_mitigate_ms=None,
        runbook_section="Backup & restore (replica loss)",
        verdict="UNVERIFIED",
        runbook_fix=None,
        detail={
            "db_replica_service_present": replica_service,
            "primary_read_continues": True,
            "primary_read_ms": read_ms,
            "matches_visible": match_count,
            "missing_piece": missing,
        },
        note=missing,
    )
    print(report.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
