"""Phase I Task 8 §12.4 — security-gate tests (``make test-security``).

CI coverage of the Task 8 security gates:

* **secret scan** (``scripts/security/scan_secrets.py``) runs clean on the
  tree and detects real-credential patterns (negative test on a planted
  token) without flagging the documented local dev URLs;
* **DAST script** (``scripts/security/dast_scan.py``) refuses a
  non-allow-listed target (gate G3) and its ``--dry-run`` prints the plan
  with no side effects.

The existing security-behaviour tests (metrics endpoint gating, push-receiver
HMAC/replay/flag, realtime origin checks + connection caps + throttling) live
in their phase suites and are wired into the ``make test-security`` target
(``tests/phase_d/test_observability.py``,
``tests/phase_d/test_ingestion_framework.py``,
``tests/phase_e/test_realtime_transport.py``,
``tests/phase_e/test_realtime_websocket.py``, ``tests/phase_e/test_throttling.py``).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from scripts.security import scan_secrets

ROOT = Path(__file__).resolve().parents[2]
SECRET_SCAN = ROOT / "scripts" / "security" / "scan_secrets.py"
DAST_SCAN = ROOT / "scripts" / "security" / "dast_scan.py"

_DEV_ENV = {**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings.dev"}


def _run(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
        cwd=ROOT,
        env=_DEV_ENV,
    )


def _fake_aws_key() -> str:
    """A credential-shaped token built at runtime so the pattern literal never
    appears in the repo (the scan would otherwise flag this very file)."""
    return "AKIA" + "1234567890" + "ABCDEF"


class TestSecretScan:
    def test_runs_clean_on_the_tree(self) -> None:
        result = _run(SECRET_SCAN)
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
        assert "clean" in result.stdout

    def test_detects_planted_credential(self, tmp_path: Path) -> None:
        (tmp_path / "creds.txt").write_text(
            f"aws_access_key_id={_fake_aws_key()}\n", encoding="utf-8"
        )
        findings = scan_secrets.scan(tmp_path)
        assert any(kind == "aws_access_key" for _, kind, _ in findings), findings

    def test_ignores_documented_local_db_url(self, tmp_path: Path) -> None:
        (tmp_path / "compose.yml").write_text(
            "DATABASE_URL: postgres://postgres:postgres@localhost:5432/cricket\n",
            encoding="utf-8",
        )
        findings = scan_secrets.scan(tmp_path)
        assert findings == [], f"local dev URL flagged: {findings}"

    def test_ignores_env_files(self, tmp_path: Path) -> None:
        (tmp_path / ".env").write_text(f"DJANGO_SECRET_KEY={_fake_aws_key()}\n")
        assert scan_secrets.scan(tmp_path) == []


class TestDastScan:
    def test_dry_run_prints_plan_no_side_effects(self) -> None:
        result = _run(DAST_SCAN, "--target", "localhost:8000", "--dry-run")
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
        assert "[dry-run] OK" in result.stdout
        assert "pass B: schemathesis" in result.stdout

    def test_refuses_non_allow_listed_target(self) -> None:
        result = _run(DAST_SCAN, "--target", "localhost:9999")
        assert result.returncode != 0
        assert "TARGET_ALLOWLIST" in result.stderr + result.stdout
