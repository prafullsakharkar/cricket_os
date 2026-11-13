#!/usr/bin/env python3
"""Game day §10.3: restore drill — measured RTO / RPO (local).

``pg_dump`` the local database to a file and ``pg_restore`` it into a scratch
database, measuring:

* **RTO** — restore wall time (dump → restore into a fresh database);
* **RPO** — data loss window. With no writes during the dump the window is
  ~0 s; recorded honestly with the timestamps that bound it.

Compares against the §6 planning targets (`docs/CRICKET_SECURITY_AND_RELIABILITY.md`
§6): RTO ≤ 4 h, RPO ≤ 15 min. The local single-node restore will beat the
target; the note records that the topology is smaller and the planning numbers
remain UNVERIFIED at production scale.

Usage:
    TARGET_ALLOWLIST=localhost:55435 uv run python scripts/game_day/drill_restore.py
    uv run python scripts/game_day/drill_restore.py --dry-run
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

SCENARIO = "drill_restore"
SRC_URL = "postgres://postgres:postgres@localhost:55435/cricket"
ADMIN_URL = "postgres://postgres:postgres@localhost:55435/postgres"
RTO_TARGET_SECONDS = 4 * 3600  # RTO <= 4 h (planning target)
RPO_TARGET_SECONDS = 15 * 60  # RPO <= 15 min (planning target)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    common.add_args(parser, scenario=SCENARIO)
    args = parser.parse_args()
    common.guard(
        scenario=SCENARIO,
        target=args.target,
        dry_run=args.dry_run,
        plan_lines=[
            "pg_dump the local 'cricket' database to a temp file",
            "create a scratch database and pg_restore into it",
            "measure RTO (restore wall time) and RPO (data loss window)",
            f"compare vs planning targets RTO <= {RTO_TARGET_SECONDS // 3600}h, "
            f"RPO <= {RPO_TARGET_SECONDS // 60}min",
        ],
    )

    started = time.monotonic()
    scratch = f"cricket_restore_drill_{os.getpid()}_{int(time.monotonic() * 1000)}"
    dump_file = f"/tmp/{scratch}.dump"

    # ---- dump -------------------------------------------------------------- #
    # Use the postgres container's client tools: the host pg_dump (13.x) is
    # older than the server (17.x) and refuses with a version mismatch.
    dump_started = time.monotonic()
    dump_result = subprocess.run(
        [
            "docker",
            "exec",
            "cricket_os-postgres-1",
            "pg_dump",
            "-U",
            "postgres",
            "cricket",
            "-Fc",
            "-f",
            dump_file,
        ],
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
    )
    dump_ok = dump_result.returncode == 0
    dump_ms = round((time.monotonic() - dump_started) * 1000.0, 2)

    # ---- create scratch + restore ------------------------------------------ #
    restore_ok = False
    restore_ms: float | None = None
    if dump_ok:
        create = subprocess.run(
            ["psql", ADMIN_URL, "-tAc", f'CREATE DATABASE "{scratch}"'],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if create.returncode == 0:
            restore_started = time.monotonic()
            restore = subprocess.run(
                [
                    "docker",
                    "exec",
                    "cricket_os-postgres-1",
                    "pg_restore",
                    "-U",
                    "postgres",
                    "-d",
                    scratch,
                    "--no-owner",
                    "--no-privileges",
                    dump_file,
                ],
                capture_output=True,
                text=True,
                timeout=1800,
                check=False,
            )
            restore_ms = round((time.monotonic() - restore_started) * 1000.0, 2)
            restore_ok = restore.returncode == 0
        subprocess.run(
            ["psql", ADMIN_URL, "-tAc", f'DROP DATABASE IF EXISTS "{scratch}"'],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    subprocess.run(
        ["docker", "exec", "cricket_os-postgres-1", "rm", "-f", dump_file],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    total_ms = round((time.monotonic() - started) * 1000.0, 2)
    rto_seconds = restore_ms / 1000.0 if restore_ms is not None else None
    rpo_seconds = dump_ms / 1000.0  # no writes during the dump -> loss window ~ dump time
    rto_demonstrated = rto_seconds is not None and rto_seconds <= RTO_TARGET_SECONDS
    rpo_demonstrated = rpo_seconds <= RPO_TARGET_SECONDS
    verdict = (
        "sufficient" if dump_ok and restore_ok and rto_demonstrated and rpo_demonstrated else "gap"
    )
    report = common.write_report(
        scenario=SCENARIO,
        target=args.target,
        detection_ms=None,
        time_to_mitigate_ms=restore_ms,
        runbook_section="Backup & restore (replica loss)",
        verdict=verdict,
        runbook_fix=None,
        detail={
            "dump_ok": dump_ok,
            "restore_ok": restore_ok,
            "dump_ms": dump_ms,
            "restore_ms": restore_ms,
            "total_ms": total_ms,
            "rto_seconds_measured": rto_seconds,
            "rto_target_seconds": RTO_TARGET_SECONDS,
            "rto_demonstrated": rto_demonstrated,
            "rpo_seconds_measured": rpo_seconds,
            "rpo_target_seconds": RPO_TARGET_SECONDS,
            "rpo_demonstrated": rpo_demonstrated,
            "note": "local single-node restore beats the planning target; the "
            "production RTO/RPO remain UNVERIFIED at scale (G2)",
        },
        note=(
            "RPO is ~ dump wall time here (no writes during the drill); "
            "production-scale RPO depends on WAL archiving, not measured locally"
        ),
    )
    print(report.read_text(encoding="utf-8"))
    if verdict != "sufficient":
        sys.exit(1)


if __name__ == "__main__":
    main()
