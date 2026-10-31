"""Read-optimised provider-domain queries (no service imports)."""

from __future__ import annotations

from apps.providers.selectors.providers import get_or_create_provider, get_provider

__all__ = ["get_or_create_provider", "get_provider"]
