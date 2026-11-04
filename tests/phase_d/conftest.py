"""Fixtures shared by the Phase D suite (build on the Phase C factories)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _ensure_default_reporters() -> Any:
    """Guarantee the DLQ reporters (reconciliation + shadow verify) are wired.

    ``IngestionConfig.ready`` registers them once during Django setup, but a
    test may clear/replace them; re-registering per test keeps the
    ``IngestionError`` rows the suite asserts on observable and isolated.
    """
    from apps.ingestion.services.reporters import register_default_reporters

    register_default_reporters()


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
