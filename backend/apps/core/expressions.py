"""Reusable raw SQL expressions.

Django's expression machinery sometimes cannot render a reference-DDL
expression exactly (e.g. the immutable day-number subtraction in
``player.name_key``, or the ``'infinity'::date`` literal in the open-membership
unique index). :class:`RawColumnExpression` emits fixed SQL verbatim while
exposing its referenced columns as source expressions, so Django still resolves
them to ``Col`` instances (needed to refresh generated values on save and to
build functional indexes).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Self, cast

from django.db.models import F
from django.db.models.expressions import Expression


class RawColumnExpression(Expression):
    """An expression rendered as fixed SQL that references columns by name.

    ``sql`` is emitted verbatim by ``as_sql`` (no parameters). ``source_refs``
    are model field names; they are exposed as source expressions so Django
    resolves them to ``Col`` instances when the expression is compiled inside a
    query context.
    """

    def __init__(
        self,
        sql: str,
        *,
        output_field: Any,
        source_refs: Sequence[str] = (),
    ) -> None:
        super().__init__(output_field=output_field)
        self.sql = sql
        self.source_refs: tuple[str, ...] = tuple(source_refs)
        # F() is not a BaseExpression subclass in django-stubs even though the
        # runtime protocol accepts it; cast for the type checker.
        self.sources: list[Expression] = [cast(Expression, F(name)) for name in self.source_refs]

    def get_source_expressions(self) -> list[Expression]:
        return self.sources

    def set_source_expressions(self, exprs: Sequence[Any]) -> None:
        self.sources = list(exprs)

    def replace_expressions(self, replacements: Mapping[Any, Any]) -> Self:
        # The SQL is fixed and cannot be parameterized. Returning ``self`` keeps
        # the expression stable inside constraint validation: the *other* side
        # of the comparison (the instance values) is what changes.
        return self

    def __eq__(self, other: Any) -> bool:
        if self.__class__ is not other.__class__:
            return NotImplemented
        # Field instances without a model/name compare by type, mirroring
        # BaseExpression._identity; sources compare element-wise (F by name).
        return (
            self.sql == other.sql
            and type(self.output_field) is type(other.output_field)
            and self.sources == other.sources
        )

    def __hash__(self) -> int:
        return hash(
            (self.__class__, self.sql, type(self.output_field), tuple(map(hash, self.sources)))
        )

    def as_sql(self, compiler: Any, connection: Any) -> tuple[str, tuple[Any, ...]]:
        return self.sql, ()

    def deconstruct(self) -> tuple[str, list[Any], dict[str, Any]]:
        path = f"{self.__class__.__module__}.{self.__class__.__qualname__}"
        return (
            path,
            [self.sql],
            {"output_field": self.output_field, "source_refs": self.source_refs},
        )


__all__ = ["RawColumnExpression"]
