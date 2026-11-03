"""Fixtures shared by the Phase D suite (build on the Phase C factories)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest


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
