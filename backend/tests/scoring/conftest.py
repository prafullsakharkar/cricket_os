"""Scoring-suite fixtures: Hypothesis profile and no Django requirement."""

from __future__ import annotations

from hypothesis import HealthCheck, settings

settings.register_profile(
    "ci",
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
# Fast profile for mutation testing: mutmut passes ``--hypothesis-profile=mutmut``
# so each mutant only runs a reduced number of examples.
settings.register_profile("mutmut", max_examples=20, deadline=None)
settings.load_profile("ci")

# pytest 9 removed the private ``_nodeid`` attribute that pytest-benchmark
# 5.3.0 still reads; expose it as an alias so the benchmark fixture works.
import pytest as _pytest  # noqa: E402

if not hasattr(_pytest.Function, "_nodeid"):  # type: ignore[attr-defined]

    def _nodeid_alias(self: object) -> str:  # pragma: no cover - shim
        return str(self.nodeid)  # type: ignore[attr-defined]

    _pytest.Function._nodeid = property(_nodeid_alias)  # type: ignore[attr-defined]
