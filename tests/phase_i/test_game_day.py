"""Phase I Task 6 (§10.1/§10.2): game-day scenario dry-run smokes + alert sink.

The scenario scripts are manual/long (they mutate a scratch DB, stop Redis,
etc.), so CI runs only:

* **dry-run smokes** — each script validates its target via the allow-list
  gate and prints a plan with **no side effects** (no report written, exit 0);
* **G3 gate** — each script refuses a non-allow-listed target;
* **alert sink contract** — ``ops/alerts/local_sink.py`` routes a raised alert
  condition to ``reports/game_day/alerts.log`` and its ``runbook`` link
  resolves to a ``## `` heading in ``docs/RUNBOOKS.md``.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GAME_DAY_DIR = ROOT / "scripts" / "game_day"
REPORTS_DIR = ROOT / "reports" / "game_day"

#: Every manual scenario + drill script (plan §10.1/§10.3).
SCRIPTS = [
    "scenario_provider_outage.py",
    "scenario_schema_drift.py",
    "scenario_wrong_score.py",
    "scenario_replica_loss.py",
    "scenario_redis_failover.py",
    "scenario_failed_migration.py",
    "scenario_reconnect_storm.py",
    "scenario_expired_credential.py",
    "drill_restore.py",
    "drill_rebuild_derived.py",
]


def _run_dry_run(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GAME_DAY_DIR / script), "--dry-run"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        cwd=ROOT,
        env={**__import__("os").environ, "DJANGO_SETTINGS_MODULE": "config.settings.dev"},
    )


@pytest.mark.parametrize("script", SCRIPTS)
def test_game_day_script_dry_run_prints_plan_no_side_effects(tmp_path: Path, script: str) -> None:
    """Each script dry-runs: exit 0, a plan is printed, no report is written."""
    before = set(REPORTS_DIR.glob("*.md")) if REPORTS_DIR.exists() else set()
    result = _run_dry_run(script)
    assert result.returncode == 0, f"{script} dry-run failed:\n{result.stdout}\n{result.stderr}"
    assert "[dry-run]" in result.stdout, f"{script} did not print a plan"
    assert "OK" in result.stdout
    after = set(REPORTS_DIR.glob("*.md")) if REPORTS_DIR.exists() else set()
    assert after == before, f"{script} dry-run wrote a report (side effect)"


@pytest.mark.parametrize("script", SCRIPTS)
def test_game_day_script_refuses_non_allow_listed_target(script: str) -> None:
    """Gate G3: a real run against a non-allow-listed target is refused."""
    result = subprocess.run(
        [sys.executable, str(GAME_DAY_DIR / script), "--target", "localhost:8000"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        cwd=ROOT,
        env={**__import__("os").environ, "DJANGO_SETTINGS_MODULE": "config.settings.dev"},
    )
    assert result.returncode != 0
    assert "TARGET_ALLOWLIST" in result.stderr + result.stdout
    assert "refusing to run" in result.stderr + result.stdout


# --------------------------------------------------------------------------- #
# §10.2 alert sink contract
# --------------------------------------------------------------------------- #


def test_alert_routes_to_local_sink_with_resolvable_runbook(tmp_path: Path) -> None:
    from ops.alerts.local_sink import route, runbook_link

    log_path = tmp_path / "alerts.log"
    returned = route(
        "LiveMatchStale",
        "warning",
        "Provider outage",
        {"match_id": "m-1"},
        log_path=log_path,
    )
    assert returned == log_path
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    entry = json.loads(lines[-1])
    assert entry["alert"] == "LiveMatchStale"
    assert entry["severity"] == "warning"
    link = entry["runbook"]
    assert link == runbook_link("Provider outage")
    section = link.split("#", 1)[1].replace("and", "&")
    runbooks = (ROOT / "docs" / "RUNBOOKS.md").read_text(encoding="utf-8")
    assert re.search(rf"^## .*{section.replace('-', ' ')}", runbooks, re.IGNORECASE | re.MULTILINE)


def test_alert_sink_rejects_unknown_runbook_section(tmp_path: Path) -> None:
    from ops.alerts.local_sink import route

    with pytest.raises(LookupError, match="not found"):
        route("SomeAlert", "critical", "No such section", {}, log_path=tmp_path / "a.log")
