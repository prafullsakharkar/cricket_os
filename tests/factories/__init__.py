"""factory_boy wiring.

Concrete factories arrive with their domain models (Phase B+). This module
exposes the shared base so later test modules have one place to extend.
"""

from __future__ import annotations

import factory

__all__ = ["BaseFactory", "factory"]


class BaseFactory(factory.Factory):
    """Common base for all Cricket OS factories."""

    class Meta:
        abstract = True
