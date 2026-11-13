"""Shared plumbing for the Phase I Task 6 game-day scenario scripts.

Each scenario script:

1. parses ``--target`` (the stack to run against) and ``--dry-run``;
2. in real mode calls :func:`loadtests.safety.assert_target_allowed` first
   (gate G3 — same allow-list as the load harnesses);
3. runs against the local stack and records a markdown report in
   ``reports/game_day/<scenario>-<timestamp>.md`` containing the detection
   time, time-to-mitigate, runbook section used, a ``sufficient | gap``
   verdict and any runbook fix needed (plan §10.1).

``--dry-run`` prints the plan and exits 0 without side effects, and never
needs an allow-list entry.
"""

from __future__ import annotations

import argparse
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loadtests.safety import assert_target_allowed

ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = ROOT / "reports" / "game_day"


def add_args(parser: argparse.ArgumentParser, *, scenario: str) -> None:
    """The standard game-day argument group."""
    parser.add_argument(
        "--target",
        default="localhost:8000",
        help="host:port of the stack to run against (must be allow-listed unless --dry-run).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate inputs and print the plan without running (no side effects, "
        "no allow-list entry needed).",
    )
    parser.add_argument(
        "--scenario", default=scenario, help=f"scenario name (default {scenario!r})"
    )


def guard(*, scenario: str, target: str, dry_run: bool, plan_lines: list[str]) -> None:
    """Dry-run plan, or the G3 allow-list gate before anything else."""
    if dry_run:
        print(f"[dry-run] {scenario}")
        print(f"[dry-run] target: {target}")
        for line in plan_lines:
            print(f"[dry-run]   {line}")
        print(f"[dry-run] no side effects; report would land in {REPORTS_DIR}/")
        print("[dry-run] OK")
        raise SystemExit(0)
    assert_target_allowed(target)


def now_utc() -> str:
    return datetime.now(UTC).isoformat()


def elapsed_ms(started: float) -> float:
    return round((time.monotonic() - started) * 1000.0, 2)


def write_report(
    *,
    scenario: str,
    target: str,
    detection_ms: float | None,
    time_to_mitigate_ms: float | None,
    runbook_section: str,
    verdict: str,  # sufficient | gap | UNVERIFIED
    runbook_fix: str | None,
    detail: dict[str, Any],
    note: str | None = None,
) -> Path:
    """Write ``reports/game_day/<scenario>-<timestamp>.md`` and return it.

    Required fields (plan §10.1): detection time, time-to-mitigate, runbook
    section used, ``sufficient | gap`` verdict, any runbook fix needed.
    """
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = REPORTS_DIR / f"{scenario}-{timestamp}.md"
    lines = [
        f"# Game day: {scenario}",
        "",
        f"- Date (UTC): {now_utc()}",
        f"- Target: `{target}`",
        f"- Detection time: {detection_ms if detection_ms is not None else 'n/a'} ms",
        "- Time to mitigate: "
        f"{time_to_mitigate_ms if time_to_mitigate_ms is not None else 'n/a'} ms",
        f"- Runbook section used: `{runbook_section}`",
        f"- Verdict: **{verdict}**",
    ]
    if runbook_fix:
        lines.append(f"- Runbook fix needed: {runbook_fix}")
    if note:
        lines.append(f"- Note: {note}")
    lines.append("")
    lines.append("## Detail")
    lines.append("")
    lines.append("```json")
    import json

    lines.append(json.dumps(detail, indent=2, sort_keys=True))
    lines.append("```")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"report: {path}")
    return path


__all__ = ["REPORTS_DIR", "add_args", "elapsed_ms", "guard", "now_utc", "write_report"]
