#!/usr/bin/env python3
"""Game day §10.1: a deliberately failing migration halts the deploy.

Runs against a **scratch database** (never the dev DB):

* baseline — the current migration set is applied to the scratch DB
  (proves the shipped migrations are clean);
* failure — a deliberately broken migration (raises at apply time) is run
  against the scratch DB via a ``MIGRATION_MODULES`` override; ``migrate``
  fails, the deploy halts;
* rollback — the scratch DB is dropped, so nothing of the failed migration
  survives (the honest local equivalent of a rollback), and the dev DB is
  verified untouched.

Runbook: `docs/RUNBOOKS.md` → "Failed migration".

Usage:
    TARGET_ALLOWLIST=localhost:55435 uv run python scripts/game_day/scenario_failed_migration.py
    uv run python scripts/game_day/scenario_failed_migration.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

from scripts.game_day import common  # noqa: E402

SCENARIO = "scenario_failed_migration"
BASE_URL = "postgres://postgres:postgres@localhost:55435/postgres"


def _psql(sql: str) -> None:
    subprocess.run(
        ["psql", BASE_URL, "-v", "ON_ERROR_STOP=1", "-tAc", sql],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    common.add_args(parser, scenario=SCENARIO)
    args = parser.parse_args()
    common.guard(
        scenario=SCENARIO,
        target=args.target,
        dry_run=args.dry_run,
        plan_lines=[
            "create a scratch database (cricket_mig_drill_<ts>)",
            "apply the shipped migrations to it (baseline clean)",
            "run a deliberately broken migration via MIGRATION_MODULES override "
            "-> migrate fails, deploy halts",
            "drop the scratch DB (rollback) and verify the dev DB is untouched",
            "report: detection + time-to-halt vs runbook 'Failed migration'",
        ],
    )

    import django

    django.setup()

    import time

    from django.conf import settings
    from django.core.management import call_command
    from django.db import connections

    db_name = f"cricket_mig_drill_{os.getpid()}_{int(time.monotonic() * 1000)}"
    dev_db = settings.DATABASES["default"]["NAME"]

    # ---- create + baseline migrate ---------------------------------------- #
    _psql(f'CREATE DATABASE "{db_name}"')
    detection_started = time.monotonic()
    scratch_conn = settings.DATABASES["default"].copy()
    scratch_conn["NAME"] = db_name
    scratch_conn["USER"] = "postgres"
    scratch_conn["PASSWORD"] = "postgres"
    scratch_conn["HOST"] = "localhost"
    scratch_conn["PORT"] = "55435"
    connections.databases["scratch"] = scratch_conn
    try:
        call_command("migrate", database="scratch", no_input=True)
        baseline_ok = True
    except Exception:
        baseline_ok = False

    # ---- deliberately failing migration ----------------------------------- #
    halt_ms: float | None = None
    halted = False
    if baseline_ok:
        with tempfile.TemporaryDirectory() as tmp:
            mod_dir = Path(tmp) / "drill_broken_migrations"
            mod_dir.mkdir()
            (mod_dir / "__init__.py").write_text("", encoding="utf-8")
            (mod_dir / "0001_broken.py").write_text(
                "from django.db import migrations\n"
                "class Migration(migrations.Migration):\n"
                "    dependencies = []\n"
                "    operations = []\n"
                "    def apply(self, *args, **kwargs):\n"
                "        raise RuntimeError('deliberately failing migration (game-day drill)')\n",
                encoding="utf-8",
            )
            sys.path.insert(0, tmp)
            try:
                settings.MIGRATION_MODULES = {"venues": "drill_broken_migrations"}
                try:
                    call_command("migrate", "venues", database="scratch", no_input=True)
                except Exception:
                    halted = True
                    halt_ms = round((time.monotonic() - detection_started) * 1000.0, 2)
            finally:
                sys.path.remove(tmp)
                connections["scratch"].close()
                del connections.databases["scratch"]
    detection_ms = round((time.monotonic() - detection_started) * 1000.0, 2)

    # ---- rollback: drop the scratch DB ------------------------------------- #
    try:
        _psql(f'DROP DATABASE "{db_name}"')
        dropped = True
    except subprocess.CalledProcessError:
        dropped = False

    dev_intact = _dev_db_intact(dev_db)

    verdict = "sufficient" if baseline_ok and halted and dropped and dev_intact else "gap"
    report = common.write_report(
        scenario=SCENARIO,
        target=args.target,
        detection_ms=detection_ms,
        time_to_mitigate_ms=halt_ms,
        runbook_section="Failed migration",
        verdict=verdict,
        runbook_fix=None,
        detail={
            "baseline_migrations_clean": baseline_ok,
            "failing_migration_halted": halted,
            "scratch_db_dropped": dropped,
            "dev_db_intact": dev_intact,
            "scratch_db": db_name,
            "dev_db": dev_db,
            "flow": "scratch DB -> clean baseline -> broken migration halts -> "
            "scratch dropped, dev untouched",
            "unverified_reason": "gate G2 FAILS: local topology only, not production evidence",
        },
    )
    print(report.read_text(encoding="utf-8"))
    if verdict != "sufficient":
        sys.exit(1)


def _dev_db_intact(dev_db: str) -> bool:
    """True when the dev database still exists and answers a read."""
    try:
        result = subprocess.run(
            [
                "psql",
                "postgres://postgres:postgres@localhost:55435/postgres",
                "-tAc",
                f"select 1 from pg_database where datname = '{dev_db}'",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return "1" in result.stdout
    except OSError:
        return False


if __name__ == "__main__":
    main()
