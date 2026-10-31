"""Provider-registry selectors."""

from __future__ import annotations

from typing import Any

from apps.core.errors import NotFound
from apps.providers.models import DataProvider


def get_provider(code: str) -> DataProvider:
    """An active provider by code or :class:`NotFound`."""
    try:
        return DataProvider.objects.get(code=code, is_active=True)
    except DataProvider.DoesNotExist as exc:
        raise NotFound(f"Provider {code!r} not found") from exc


def get_or_create_provider(code: str, **defaults: Any) -> DataProvider:
    """Return the provider row for ``code``, creating it if absent.

    Idempotent: concurrent callers collapse onto the ``ux_data_provider_code``
    unique constraint, re-reading the winner.
    """
    provider, _ = DataProvider.objects.get_or_create(code=code, defaults=defaults)
    return provider


__all__ = ["get_or_create_provider", "get_provider"]
