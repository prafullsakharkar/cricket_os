"""Dated shadow-comparison report writer (Phase I Task 2).

Writes ``reports/shadow/<YYYY-MM-DD>.md`` (+ a machine-readable JSON copy) with:
mismatch rate by category, ingestion-latency distribution, correction frequency
+ lag, coverage gaps, schema-drift events, unresolved identity rate, and the
mandatory environment banner (the environment gate fails, so every number is
UNVERIFIED against a staging/production environment and names the gate).

The report directory defaults to ``reports/shadow`` (repo root) and can be
overridden for tests via ``override_settings(SHADOW_REPORT_DIR=...)``.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from django.conf import settings

from apps.shadow.compare import CATEGORIES, ComparisonReport

#: The banner every dated report carries (brief §Constraints: label honestly).
_ENV_BANNER = (
    "UNVERIFIED: environment gate (docs/STAGING_ACCESS.md) is NOT met — these "
    "numbers were measured on the local production-topology stack against the "
    "ReplayAdapter (provider gate also NOT met). Do not treat them as "
    "production or staging evidence."
)


def report_dir() -> Path:
    return Path(getattr(settings, "SHADOW_REPORT_DIR", "reports/shadow"))


def write_report(
    day: date,
    reports: list[ComparisonReport],
    *,
    latency: dict[str, float] | None = None,
    corrections: dict[str, Any] | None = None,
    coverage_gaps: int = 0,
    schema_drift: int = 0,
    unresolved_identity: int = 0,
) -> Path:
    """Write the dated markdown + JSON report; returns the markdown path."""
    directory = report_dir()
    directory.mkdir(parents=True, exist_ok=True)

    mismatch_rate = _mismatch_rate(reports)
    data = {
        "day": day.isoformat(),
        "environment": _ENV_BANNER,
        "matches_compared": len(reports),
        "mismatch_rate_by_category": mismatch_rate,
        "ingest_latency_distribution": latency or {},
        "corrections": corrections or {},
        "coverage_gaps": coverage_gaps,
        "schema_drift_events": schema_drift,
        "unresolved_identity": unresolved_identity,
        "findings": [f.as_dict() for report in reports for f in report.findings],
    }

    md_path = directory / f"{day.isoformat()}.md"
    md_path.write_text(_render_markdown(data), encoding="utf-8")
    json_path = directory / f"{day.isoformat()}.json"
    json_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return md_path


def _mismatch_rate(reports: list[ComparisonReport]) -> dict[str, float]:
    totals = dict.fromkeys(CATEGORIES, 0)
    mismatches = dict.fromkeys(CATEGORIES, 0)
    for report in reports:
        by_category = report.by_category
        for category in CATEGORIES:
            totals[category] += 1
            mismatches[category] += by_category[category]
    return {
        category: (mismatches[category] / totals[category] if totals[category] else 0.0)
        for category in CATEGORIES
    }


def _render_markdown(data: dict[str, Any]) -> str:
    lines = [
        f"# Shadow comparison report — {data['day']}",
        "",
        f"> {data['environment']}",
        "",
        f"- Matches compared: **{data['matches_compared']}**",
        "",
        "## Mismatch rate by category",
        "",
        "| Category | Rate |",
        "|---|---|",
    ]
    for category, rate in data["mismatch_rate_by_category"].items():
        lines.append(f"| {category} | {rate:.4f} |")

    lines += ["", "## Ingestion latency distribution (provider event → event log, s)", ""]
    for key, value in data["ingest_latency_distribution"].items():
        lines.append(f"- {key}: {value}")
    if not data["ingest_latency_distribution"]:
        lines.append("- (none measured this day)")

    lines += ["", "## Corrections", ""]
    correction_data = data["corrections"]
    if correction_data:
        for key, value in correction_data.items():
            lines.append(f"- {key}: {value}")
    else:
        lines.append("- (none)")

    lines += [
        "",
        f"- Coverage gaps: **{data['coverage_gaps']}**",
        f"- Schema-drift events: **{data['schema_drift_events']}**",
        f"- Unresolved identity: **{data['unresolved_identity']}**",
        "",
        f"## Findings ({len(data['findings'])})",
        "",
    ]
    if not data["findings"]:
        lines.append("No mismatches (replay corpus is internally consistent).")
    for finding in data["findings"]:
        lines.append(
            f"- `{finding['category']}` {finding.get('field', '')} "
            f"inn={finding.get('innings_number')} key={finding.get('entity_key')} "
            f"ours={finding['ours']} provider={finding['provider']} "
            f"second={finding['second']} severity={finding['severity']}"
        )
    lines.append("")
    return "\n".join(lines)


__all__ = ["report_dir", "write_report"]
