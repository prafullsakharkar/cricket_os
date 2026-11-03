"""Fixtures shared by the Phase D suite (build on the Phase C factories)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _ensure_reconciliation_reporter() -> Any:
    """Guarantee the DLQ reporter for reconciliation mismatches is registered.

    ``IngestionConfig.ready`` registers it once during Django setup, but a
    test may clear/replace it; re-registering per test keeps the
    ``reconciliation_mismatch`` IngestionError rows observable and isolated.
    """
    from apps.ingestion.services.jobs import reconciliation_mismatch_reporter
    from apps.synchronization.services.reconciliation import register_mismatch_reporter

    register_mismatch_reporter(reconciliation_mismatch_reporter)


@pytest.fixture
def make_match() -> Callable[..., Any]:
    from tests.phase_c.factories import make_match as factory

    return factory


@pytest.fixture
def make_team() -> Callable[..., Any]:
    from tests.phase_c.factories import make_team as factory

    return factory


@pytest.fixture
def make_player() -> Callable[..., Any]:
    from tests.phase_c.factories import make_player as factory

    return factory


@pytest.fixture
def make_innings() -> Callable[..., Any]:
    from tests.phase_c.factories import make_innings as factory

    return factory


@pytest.fixture
def make_provider() -> Callable[..., Any]:
    from tests.phase_c.factories import make_provider as factory

    return factory
