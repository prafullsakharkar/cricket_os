"""Thin access to the Phase E cache alias (never the session/default cache).

Imported lazily so importing the cache package does not require configured
Django settings (the pure scoring test suite imports modules without Django).
"""

from __future__ import annotations

from typing import Any


def cache_backend() -> Any:
    """Return the ``cache`` alias (tests swap it for LocMem)."""
    from django.core.cache import caches

    return caches["cache"]


__all__ = ["cache_backend"]
