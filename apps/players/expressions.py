"""Custom SQL expressions for the players app.

``NameKeyExpression`` renders the reference DDL's ``player.name_key`` generated
column expression **byte-for-byte** (DATABASE_SCHEMA §3.3)::

    lower(full_name) || '|' || coalesce((date_of_birth - DATE '0001-01-01')::text, '')

Django's expression machinery cannot express the integer day-number
subtraction without rewriting it as an interval
(``interval '1 day' * (date_of_birth - %s)``), which changes the stored value
and is not the reference semantics. Emitting the SQL directly keeps the
generated column identical to the DDL. Every function involved is IMMUTABLE,
so the value is independent of DateStyle (the date is encoded as an integer
day-number, never ``date::text``).
"""

from __future__ import annotations

from typing import Any

from django.db import models

from apps.core.expressions import RawColumnExpression

NAME_KEY_SQL = "lower(full_name) || '|' || coalesce((date_of_birth - DATE '0001-01-01')::text, '')"


class NameKeyExpression(RawColumnExpression):
    """The IMMUTABLE ``player.name_key`` generated-column expression.

    Exposes ``full_name`` and ``date_of_birth`` as source expressions so Django
    can resolve them to ``Col`` instances (used to refresh the generated value
    on save via RETURNING); ``as_sql`` emits the exact DDL text.
    """

    def __init__(self) -> None:
        super().__init__(
            NAME_KEY_SQL,
            output_field=models.TextField(),
            source_refs=["full_name", "date_of_birth"],
        )

    def deconstruct(self) -> tuple[str, list[Any], dict[str, Any]]:
        return f"{self.__class__.__module__}.{self.__class__.__qualname__}", [], {}


__all__ = ["NAME_KEY_SQL", "NameKeyExpression"]
