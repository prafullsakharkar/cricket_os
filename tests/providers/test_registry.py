"""The provider adapter registry (DATA_INGESTION §2, providers/registry.py)."""

from __future__ import annotations

import pytest

from apps.providers import registry

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_registry() -> object:
    registry.clear_registry()
    yield
    registry.clear_registry()


def test_register_and_get_adapter() -> None:
    calls: list[int] = []

    def factory():
        calls.append(len(calls))
        return {"code": "cricsheet"}

    registry.register_adapter("cricsheet", factory)
    assert registry.get_adapter("cricsheet") == {"code": "cricsheet"}
    assert registry.get_adapter("cricsheet") == {"code": "cricsheet"}
    assert calls == [0, 1], "each get builds a fresh instance"


def test_get_adapter_unknown_code_raises() -> None:
    with pytest.raises(registry.ProviderNotRegistered):
        registry.get_adapter("nonexistent")


def test_list_providers_sorted() -> None:
    registry.register_adapter("zebra", lambda: None)
    registry.register_adapter("alpha", lambda: None)
    assert registry.list_providers() == ["alpha", "zebra"]


def test_register_replaces_factory() -> None:
    registry.register_adapter("cricsheet", lambda: "v1")
    registry.register_adapter("cricsheet", lambda: "v2")
    assert registry.get_adapter("cricsheet") == "v2"
