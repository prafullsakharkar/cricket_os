"""Phase I Task 9 (§13.7) — launch-package doc-integrity tests.

Cheap structural checks that keep the go/no-go package honest and reviewable:

* the three launch documents exist and are non-trivial;
* `GO_NO_GO.md` carries the blunt "every unproven item" section (the phase
  must not round up) and the NO-GO verdict;
* the feature-flag inventory in `FEATURE_FLAGS.md` names settings that
  actually exist in `config/settings/base.py` (spot-check the launch-gate and
  data-quality flags);
* every `LAUNCH_CRITERIA.md` row has an owner, a status and an evidence
  source (the go/no-go table consumes them).

These are pure file checks (no Django settings import) so they run fast in
`make check`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]  # repo root (docs/ lives here)
BACKEND = Path(__file__).resolve().parents[2]  # backend/ (config/ lives here)
DOCS = REPO_ROOT / "docs"
SETTINGS = BACKEND / "config" / "settings" / "base.py"

LAUNCH_DOCS = ("LAUNCH_PLAN.md", "GO_NO_GO.md", "FEATURE_FLAGS.md")

#: Launch-gate + data-quality flags the inventory must reference by name
#: (the exact `base.py` assignments exist — verified against the settings file
#: in the flag-inventory test).
EXPECTED_FLAGS = (
    "LIVE_INGESTION_ENABLED",
    "PROVIDER_SHADOW_MODE",
    "LIVE_PUSH_RECEIVER_ENABLED",
    "LIVE_PUSH_HMAC_SECRET",
    "ENABLE_METRICS_ENDPOINT",
    "MATCH_DEDUP_START_TOLERANCE_MINUTES",
    "MATCH_DEDUP_AUTO_THRESHOLD",
    "MATCH_DEDUP_REVIEW_THRESHOLD",
    "FANTASY_PUBLIC_API_ENABLED",
    "TARGET_ALLOWLIST",
)


def test_launch_documents_exist_and_are_substantial() -> None:
    for name in LAUNCH_DOCS:
        path = DOCS / name
        assert path.is_file(), f"launch document missing: {name}"
        assert len(path.read_text(encoding="utf-8").strip()) > 500, (
            f"{name} is suspiciously thin for a launch document"
        )


def test_go_no_go_states_every_unproven_item_bluntly() -> None:
    """§13.2/13.5: the verdict must be NO-GO and the unproven items listed."""
    text = (DOCS / "GO_NO_GO.md").read_text(encoding="utf-8")
    assert "NO-GO" in text, "the go/no-go verdict must be stated"
    # The blunt inventory section names the unproven items (not a rounded-up
    # summary) — pinned by heading + at least the first item.
    assert "unproven item" in text.lower(), (
        "GO_NO_GO.md must contain the 'every unproven item' section (§13.2)"
    )
    assert "Real provider behaviour" in text, "item 1 of the blunt list is missing"


def test_launch_plan_has_abort_and_rollback() -> None:
    """§13.1: every stage must have abort criteria and a rollback procedure."""
    text = (DOCS / "LAUNCH_PLAN.md").read_text(encoding="utf-8")
    assert "Abort criteria" in text, "LAUNCH_PLAN.md must define abort criteria"
    assert "Rollback" in text, "LAUNCH_PLAN.md must define rollback procedures"


def test_feature_flag_inventory_names_real_settings() -> None:
    """§13.7: spot-check that the inventory flags exist in settings/base.py.

    Every EXPECTED_FLAGS entry must appear both in FEATURE_FLAGS.md and as a
    `NAME = env("...")`/module-level assignment in settings/base.py — a flag
    documented but not wired is a launch hazard.
    """
    inventory = (DOCS / "FEATURE_FLAGS.md").read_text(encoding="utf-8")
    settings_text = SETTINGS.read_text(encoding="utf-8")
    for flag in EXPECTED_FLAGS:
        assert flag in inventory, f"FEATURE_FLAGS.md does not mention {flag}"
        assert re.search(rf"^{flag}\b", settings_text, re.M), (
            f"{flag} documented in the inventory but not defined in settings/base.py"
        )


def test_launch_criteria_rows_have_owners_status_and_evidence() -> None:
    """§13.7: every LC row must carry an owner, status and evidence source."""
    text = (DOCS / "LAUNCH_CRITERIA.md").read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if line.startswith("| LC-") and "---" not in line]
    assert rows, "no LC-* rows found in LAUNCH_CRITERIA.md"
    for row in rows:
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        # | # | Criterion | Proposed threshold | Owner | Status | Evidence source |
        assert len(cells) >= 6, f"row has fewer than 6 columns: {row}"
        assert cells[3], f"LC row without an owner: {row}"
        assert cells[4], f"LC row without a status: {row}"
        assert cells[5], f"LC row without an evidence source: {row}"
