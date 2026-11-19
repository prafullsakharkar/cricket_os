"""Phase I Task 6 (§10.1/§10.2): game-day scenario dry-run smokes + alert sink.

The scenario scripts are manual/long (they mutate a scratch DB, stop Redis,
etc.), so CI runs only:

* **dry-run smokes** — each script validates its target via the allow-list
  gate and prints a plan with **no side effects** (no report written, exit 0);
* **G3 gate** — each script refuses a non-allow-listed target;
* **alert sink contract** — ``infrastructure/ops/alerts/local_sink.py`` routes a
  raised alert condition to ``reports/game_day/alerts.log`` and its ``runbook``
  link resolves to a ``## `` heading in ``docs/RUNBOOKS.md``.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]  # backend/ (scripts + manage.py live here)
REPO_ROOT = Path(__file__).resolve().parents[3]  # repo root (reports/, docs/ live here)
GAME_DAY_DIR = ROOT / "scripts" / "game_day"
REPORTS_DIR = REPO_ROOT / "reports" / "game_day"


def _local_sink():
    """Load infrastructure/ops/alerts/local_sink.py (moved off the Python path)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "local_sink", REPO_ROOT / "infrastructure" / "ops" / "alerts" / "local_sink.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["local_sink"] = module
    spec.loader.exec_module(module)
    return module


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
    sink = _local_sink()

    log_path = tmp_path / "alerts.log"
    returned = sink.route(
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
    assert link == sink.runbook_link("Provider outage")
    section = link.split("#", 1)[1].replace("and", "&")
    runbooks = (REPO_ROOT / "docs" / "RUNBOOKS.md").read_text(encoding="utf-8")
    assert re.search(rf"^## .*{section.replace('-', ' ')}", runbooks, re.IGNORECASE | re.MULTILINE)


def test_alert_sink_rejects_unknown_runbook_section(tmp_path: Path) -> None:
    sink = _local_sink()

    with pytest.raises(LookupError, match="not found"):
        sink.route("SomeAlert", "critical", "No such section", {}, log_path=tmp_path / "a.log")
