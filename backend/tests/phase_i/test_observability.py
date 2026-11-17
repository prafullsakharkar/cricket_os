"""Phase I Task 2: shadow alert rules → metrics + runbook contract.

Mirrors the Phase D/E/H pattern (tests/phase_d/test_observability.py): the
shadow alert file parses as Prometheus rules, every rule's expression
references a registered ``shadow_*`` metric, and every ``runbook`` label
matches a ``## Shadow mode`` heading in ``docs/RUNBOOKS.md``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from apps.core import metrics

REPO_ROOT = Path(__file__).resolve().parents[3]  # repo root (alerts/, docs/ live here)

pytestmark = pytest.mark.django_db

SHADOW_ALERT_FILE = REPO_ROOT / "infrastructure" / "ops" / "alerts" / "shadow_alerts.yml"

#: Exact rule set (protects against accidental rule edits).
_SHADOW_ALERTS = {
    "ShadowMismatchRateHigh",
    "ShadowUnresolvedIdentityHigh",
    "ShadowCorrectionLagHigh",
    "ShadowSchemaDrift",
    "ShadowCoverageGapsHigh",
}


def _load_alerts() -> list[dict[str, object]]:
    with Path(SHADOW_ALERT_FILE).open(encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    return [rule for group in document["groups"] for rule in group["rules"]]


def _registered_metric_names() -> set[str]:
    rendered = metrics.render()
    names = set()
    for line in rendered.splitlines():
        if line.startswith("# TYPE "):
            names.add(line.split()[2])
    return names


def test_shadow_alert_file_parses_with_expected_rule_set() -> None:
    rules = _load_alerts()
    assert rules
    assert {rule["alert"] for rule in rules} == _SHADOW_ALERTS


def test_shadow_alert_exprs_reference_registered_metrics() -> None:
    registered = _registered_metric_names()
    labels = {
        "by",
        "max",
        "sum",
        "rate",
        "increase",
        "histogram_quantile",
        "clamp_min",
        "le",
        "value",
    }
    for rule in _load_alerts():
        expr = rule["expr"]
        for candidate in re.findall(r"\b[a-z_][a-z0-9_]+(?:_bucket|_count|_sum)?\b", expr):
            if candidate in labels:
                continue
            if candidate in registered:
                continue
            base = (
                candidate.rsplit("_", 1)[0]
                if candidate.endswith(("_bucket", "_count", "_sum"))
                else candidate
            )
            assert base in registered, (
                f"alert {rule['alert']} references unknown metric {candidate}"
            )


def test_shadow_alert_runbook_labels_match_runbook_headings() -> None:
    runbook_names = {rule["labels"]["runbook"] for rule in _load_alerts()}
    with (REPO_ROOT / "docs" / "RUNBOOKS.md").open(encoding="utf-8") as handle:
        runbook_doc = handle.read().lower()
    for name in runbook_names:
        assert f"## {name}" in runbook_doc, f"no runbook section for {name!r}"


def test_shadow_alert_rules_carry_runbook_and_severity() -> None:
    for rule in _load_alerts():
        assert rule["labels"]["runbook"]
        assert rule["labels"]["severity"] in {"warning", "critical"}
        assert rule["for"], "alert needs a for duration"
        assert rule["annotations"]["summary"]
