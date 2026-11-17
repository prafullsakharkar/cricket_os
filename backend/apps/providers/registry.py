"""Adapter registry stub (DATA_INGESTION §2).

No real adapters exist yet — Phase B only needs the registration machinery so
Phase C/D can add ``cricsheet``, ``replay`` and licensed-provider adapters
without touching callers.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from apps.core.errors import CricketError

type AdapterFactory = Callable[[], Any]


class ProviderNotRegistered(CricketError):
    """Raised when ``get_adapter`` cannot find an adapter for a provider code."""

    code = "unprocessable"
    status_code = 422


_registry: dict[str, AdapterFactory] = {}


def register_adapter(provider_code: str, factory: AdapterFactory) -> None:
    """Register (or replace) the adapter factory for ``provider_code``."""
    _registry[provider_code] = factory


def get_adapter(provider_code: str) -> Any:
    """Return an adapter instance for ``provider_code``.

    Raises :class:`ProviderNotRegistered` for unknown codes. Each call builds a
    fresh instance so adapters can be stateful per use.
    """
    try:
        factory = _registry[provider_code]
    except KeyError as exc:
        raise ProviderNotRegistered(
            f"No adapter registered for provider {provider_code!r}"
        ) from exc
    return factory()


def list_providers() -> list[str]:
    """Return the sorted provider codes with a registered adapter."""
    return sorted(_registry)


def clear_registry() -> None:
    """Drop all adapters (test isolation)."""
    _registry.clear()


__all__ = [
    "ProviderNotRegistered",
    "clear_registry",
    "get_adapter",
    "list_providers",
    "register_adapter",
]
