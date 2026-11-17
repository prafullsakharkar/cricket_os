"""Phase I gate-outcome markers (brief §Gates, plan Task 0).

The two Phase I gates — the provider gate (`docs/PROVIDER_DECISION.md`) and the
environment gate (`docs/STAGING_ACCESS.md`) — both FAIL as of 2026-11-12. Their
documents are gate-placeholders: each carries the literal marker "Gate NOT met"
so the outcome is visible in every review. The tests below pin the marker so a
future PR that *satisfies* a gate must deliberately edit the document and flip
the assertion — which is exactly the review signal the phase needs.

When a gate is genuinely met, the document's author removes the marker line and
updates these tests to assert the *contents* of the real decision instead of
the placeholder.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]  # repo root (docs/ lives here)

GATE_MARKER = "Gate NOT met"

GATE_DOCS = {
    "provider_gate": REPO_ROOT / "docs" / "PROVIDER_DECISION.md",
    "environment_gate": REPO_ROOT / "docs" / "STAGING_ACCESS.md",
}


def test_gate_documents_exist() -> None:
    """The gate documents must exist so the gate outcome is reviewable."""
    for name, path in GATE_DOCS.items():
        assert path.is_file(), f"{name} gate doc missing: {path}"


def test_gate_documents_declare_the_gate_unmet() -> None:
    """While the gates fail, each document must carry the explicit marker."""
    for name, path in GATE_DOCS.items():
        text = path.read_text(encoding="utf-8")
        assert GATE_MARKER in text, (
            f"{name} gate doc no longer declares 'Gate NOT met' — "
            f"if the gate was satisfied, update the document AND these tests "
            f"to assert the real decision contents."
        )


def test_gate_documents_are_not_blank_templates() -> None:
    """Each placeholder must state the consequences, not just the marker."""
    for name, path in GATE_DOCS.items():
        text = path.read_text(encoding="utf-8")
        assert len(text.strip()) > 500, f"{name} gate doc is suspiciously thin"
        assert "template" in text.lower(), (
            f"{name} gate doc should describe what a real decision must contain"
        )
