"""Custom SQL expressions for the fantasy app.

``ConditionHashExpression`` renders the reference DDL's
``fantasy_point_rule.condition_hash`` generated-column expression
**byte-for-byte** (DATABASE_SCHEMA §3.8)::

    md5(coalesce(condition::text, ''))

Django's expression machinery cannot render ``md5`` (no built-in Func for it),
and the cast spelling must stay ``condition::text`` so the stored value is
identical to the DDL semantics. Emitting the SQL directly (the players-app
``NameKeyExpression`` precedent) keeps the generated column identical. ``md5``
is IMMUTABLE, so the value is independent of locale/DateStyle.
"""

from __future__ import annotations

from typing import Any

from django.db import models

from apps.core.expressions import RawColumnExpression

CONDITION_HASH_SQL = "md5(coalesce(condition::text, ''))"


class ConditionHashExpression(RawColumnExpression):
    """The IMMUTABLE ``condition_hash`` generated-column expression.

    Exposes ``condition`` as a source expression so Django can resolve it to a
    ``Col`` instance (used to refresh the generated value on save via
    RETURNING); ``as_sql`` emits the exact DDL text.
    """

    def __init__(self) -> None:
        super().__init__(
            CONDITION_HASH_SQL,
            output_field=models.TextField(),
            source_refs=["condition"],
        )

    def deconstruct(self) -> tuple[str, list[Any], dict[str, Any]]:
        return f"{self.__class__.__module__}.{self.__class__.__qualname__}", [], {}


__all__ = ["CONDITION_HASH_SQL", "ConditionHashExpression"]
