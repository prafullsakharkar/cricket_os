"""Generic provider-identity resolution (Decision D4).

``resolve`` turns a provider's external identifier into the platform's UUID
for an entity, using a per-entity-type resolver registered in each app's
``ready()``:

1. exact ``ProviderEntityMap`` hit → return its ``internal_id``;
2. ``resolver.match(attributes)`` → ranked candidates;
3. exactly one candidate at or above ``IDENTITY_MATCH_THRESHOLD`` → link:
   a new map row (``verified=False``) pointing at it;
4. otherwise a stub entity is created by ``resolver.create_stub`` (with its
   own map row, ``verified=False``) and returned.

**Never auto-merge**: candidates below the link threshold are only flagged
for steward review. The whole body runs in ``transaction.atomic()``; a lost
race against ``ux_pem`` (concurrent resolve of the same provider entity)
rolls back to a savepoint and re-reads the winning row, so exactly one map
row — and one entity — survives.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction

from apps.core.errors import Unprocessable
from apps.providers.models import DataProvider, ProviderEntityMap


@dataclass(frozen=True, slots=True)
class Candidate:
    """A scored candidate match returned by a resolver's ``match``."""

    internal_id: UUID
    score: float
    breakdown: dict[str, Any] = field(default_factory=dict)


class Resolver(Protocol):
    """Per-entity-type identity resolver.

    ``match`` ranks existing platform entities against inbound attributes;
    ``create_stub`` creates a brand-new entity (plus its map row) when no
    existing entity can be linked.
    """

    def match(self, attributes: Mapping[str, Any]) -> list[Candidate]: ...

    def create_stub(
        self, provider: DataProvider, provider_entity_id: str, attributes: Mapping[str, Any]
    ) -> UUID: ...


_resolvers: dict[str, Resolver] = {}


def register_entity_resolver(entity_type: str, resolver: Resolver) -> None:
    """Register (or replace) the resolver for ``entity_type``."""
    _resolvers[entity_type] = resolver


def get_resolver(entity_type: str) -> Resolver:
    try:
        return _resolvers[entity_type]
    except KeyError as exc:
        raise Unprocessable(
            f"No identity resolver registered for entity type {entity_type!r}"
        ) from exc


def _provider_unique_name(attributes: Mapping[str, Any]) -> str | None:
    for key in ("full_name", "name", "short_name"):
        value = attributes.get(key)
        if value:
            return str(value)
    return None


def _as_confidence(value: float) -> Decimal:
    return Decimal(str(round(value, 3)))


def is_pem_race(exc: IntegrityError) -> bool:
    """True when ``exc`` is the ``ux_pem`` unique violation of a lost race.

    Concurrent resolves/upserts of the same ``(provider, entity_type,
    provider_entity_id)`` are arbitrated by ``ux_pem``; only that violation
    should trigger the "re-read the winner" path. Any other IntegrityError
    (a domain unique/check constraint) is a real data error and must
    propagate.
    """
    cause = getattr(exc, "__cause__", None)
    diag = getattr(cause, "diag", None)
    return bool(diag and diag.constraint_name == "ux_pem")


def _winner_internal_id(provider: DataProvider, entity_type: str, provider_entity_id: str) -> UUID:
    return ProviderEntityMap.objects.get(
        provider=provider,
        entity_type=entity_type,
        provider_entity_id=provider_entity_id,
    ).internal_id


def resolve(
    provider: DataProvider,
    entity_type: str,
    provider_entity_id: str,
    attributes: Mapping[str, Any],
) -> UUID:
    """Resolve a provider's entity to a platform UUID (idempotent, safe).

    Concurrent calls for the same ``(provider, entity_type,
    provider_entity_id)`` collapse onto one map row and one entity: the
    ``ux_pem`` unique constraint arbitrates, and the loser re-reads the
    winner's ``internal_id``.
    """
    resolver = get_resolver(entity_type)
    with transaction.atomic():
        try:
            return ProviderEntityMap.objects.get(
                provider=provider,
                entity_type=entity_type,
                provider_entity_id=provider_entity_id,
            ).internal_id
        except ProviderEntityMap.DoesNotExist:
            pass

        candidates = resolver.match(attributes)
        strong = [
            candidate
            for candidate in candidates
            if candidate.score >= settings.IDENTITY_MATCH_THRESHOLD
        ]
        if len(strong) == 1:
            top = strong[0]
            try:
                with transaction.atomic():
                    ProviderEntityMap.objects.create(
                        provider=provider,
                        entity_type=entity_type,
                        internal_id=top.internal_id,
                        provider_entity_id=provider_entity_id,
                        provider_unique_name=_provider_unique_name(attributes),
                        confidence=_as_confidence(top.score),
                        verified=False,
                    )
            except IntegrityError as exc:
                if not is_pem_race(exc):
                    raise
                return _winner_internal_id(provider, entity_type, provider_entity_id)
            return top.internal_id

        try:
            with transaction.atomic():
                return resolver.create_stub(provider, provider_entity_id, attributes)
        except IntegrityError as exc:
            if not is_pem_race(exc):
                raise
            return _winner_internal_id(provider, entity_type, provider_entity_id)


__all__ = [
    "Candidate",
    "Resolver",
    "get_resolver",
    "is_pem_race",
    "register_entity_resolver",
    "resolve",
]
