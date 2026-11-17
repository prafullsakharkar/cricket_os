"""Local alert sink (plan §10.2).

No STAGING_ACCESS on-call channel exists (gate G2), so alert routing is
delivered to a local file sink that the game-day scenarios and tests can
verify end-to-end. The real-channel step is documented as blocked/UNVERIFIED
(needs `docs/STAGING_ACCESS.md`).

``route()`` appends one JSON line per alert to ``reports/game_day/alerts.log``
with the alert name, severity, a resolved runbook link (the same
``docs/RUNBOOKS.md`` anchor the alert YAML's ``runbook`` label points to) and
the detail. The runbook link is validated to exist before it is written, so a
sink entry always resolves.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[3]  # repo root (infrastructure/ops/alerts/ -> parents[3])
LOG_PATH = ROOT / "reports" / "game_day" / "alerts.log"
RUNBOOKS = ROOT / "docs" / "RUNBOOKS.md"


def runbook_link(section: str) -> str:
    """A runbook anchor URL for ``section`` (e.g. ``#provider-outage``)."""
    slug = section.strip().lower().replace(" ", "-").replace("&", "and")
    return f"docs/RUNBOOKS.md#{quote(slug)}"


def assert_runbook_section(section: str) -> None:
    """Raise when ``docs/RUNBOOKS.md`` has no matching section heading."""
    text = RUNBOOKS.read_text(encoding="utf-8")
    if f"## {section}" not in text:
        raise LookupError(f"runbook section {section!r} not found in {RUNBOOKS}")


def route(
    alert_name: str,
    severity: str,
    runbook_section: str,
    detail: dict[str, Any],
    *,
    log_path: Path | None = None,
) -> Path:
    """Route one alert to the local sink; returns the log path.

    Validates the runbook section exists before writing (a sink entry must
    always resolve). ``log_path`` is overridable for tests.
    """
    assert_runbook_section(runbook_section)
    path = log_path or LOG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "alert": alert_name,
        "severity": severity,
        "runbook": runbook_link(runbook_section),
        "detail": detail,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
    return path


__all__ = ["LOG_PATH", "assert_runbook_section", "route", "runbook_link"]
