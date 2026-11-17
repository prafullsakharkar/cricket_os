"""Shared read-API plumbing: sparse fields and ordering validation.

Kept free of first-party imports (import-linter: ``core`` is a leaf). The
``?fields=`` convention and the per-endpoint sort allow-lists are defined in
CRICKET_API_SPECIFICATION §1 / §5; anything outside them is a 400
``invalid_query``.
"""

from __future__ import annotations

from typing import Any, cast

from rest_framework import serializers
from rest_framework.response import Response

from apps.core.errors import InvalidQuery


class SparseFieldsMixin:
    """``?fields=`` sparse-field support for a read-only serializer.

    The comma-separated list is validated against :attr:`allowed_fields`
    (unknown names → 400 ``invalid_query``) and ``get_fields`` is then
    restricted to the requested subset, so rendering is cheaper as well.
    """

    #: Serializer context (DRF injects ``{"request": ...}`` into read views).
    context: dict[str, Any]
    #: Every field the serializer can return (documentation + validation).
    allowed_fields: tuple[str, ...] = ()

    def get_fields(self) -> dict[str, serializers.Field[Any, Any, Any, Any]]:
        fields = cast(dict[str, Any], super().get_fields())  # type: ignore[misc]
        request = self.context.get("request")
        raw = None if request is None else request.query_params.get("fields")
        if not raw:
            return fields
        names = [part.strip() for part in raw.split(",") if part.strip()]
        allowed = self.allowed_fields or tuple(fields)
        unknown = [name for name in names if name not in allowed]
        if unknown:
            raise InvalidQuery("Unknown fields", details={"fields": unknown})
        return {name: field for name, field in fields.items() if name in names}


def validate_ordering(request: Any, allowed: frozenset[str], default: str) -> str:
    """Resolve ``?sort=`` against an allow-list, else ``default``.

    A comma list is allowed but only the first (validated) item is used; any
    item outside ``allowed`` is a 400 ``invalid_query``.
    """
    raw = request.query_params.get("sort")
    if raw is None:
        return default
    names = [part.strip() for part in raw.split(",") if part.strip()]
    unknown = [name for name in names if name not in allowed]
    if unknown:
        raise InvalidQuery("Invalid sort field", details={"sort": unknown})
    return names[0] if names else default


class EnvelopeMixin:
    """Wrap single-item responses in the standard ``{"data": ...}`` envelope.

    Collections are already enveloped by the pagination classes; this makes
    the detail endpoints match (CRICKET_API_SPECIFICATION §1). Mix with a
    ``RetrieveAPIView`` (which provides ``get_object`` / ``get_serializer``).
    """

    def retrieve(self, request: Any, *args: Any, **kwargs: Any) -> Response:
        instance = self.get_object()  # type: ignore[attr-defined]
        serializer = self.get_serializer(instance)  # type: ignore[attr-defined]
        return Response({"data": serializer.data})


__all__ = ["EnvelopeMixin", "SparseFieldsMixin", "validate_ordering"]
