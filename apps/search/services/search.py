"""Cross-entity search over the domain tables (Phase F Group 6).

Search is **index-backed trigram matching** (the GIN ``%`` operator on
``ix_player_name_trgm`` / ``ix_team_name_trgm`` / the name trigram indexes),
never a scan: the EXPLAIN gate in the test suite forces ``enable_seqscan=off``
and asserts the plan references the trigram index.

Safety is layered:

* **Normalisation** — lowercase, collapse whitespace.
* **Length bounds** — ``MIN_QUERY_LENGTH``/``MAX_QUERY_LENGTH``; anything else
  is a ``ValueError`` the API maps to ``invalid_query`` (no unbounded LIKE).
* **Statement timeout** — every search runs inside one atomic block with
  ``SET LOCAL statement_timeout`` (default 200 ms), so one pathological query
  cannot stall the pool.
* **Result cap** — per-type candidate fetch capped and the merged result set
  truncated to ``limit``.

Ranking is **weighted**: ``score = trigram_similarity * type_weight`` plus an
exact-name boost (``+0.25``) and a prefix boost (``+0.1``), so within a type
similarity dominates while across types a strong team match can outrank a weak
player match. Ties break by (type order, name).
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from django.contrib.postgres.search import TrigramSimilarity
from django.db import OperationalError, connection, transaction
from django.db.models import QuerySet

from apps.core import metrics

#: Annotation name carrying the per-row trigram similarity.
SIM_ATTR = "_sim"

#: Safety bounds.
MIN_QUERY_LENGTH = 2
MAX_QUERY_LENGTH = 100
DEFAULT_LIMIT = 20
MAX_LIMIT = 100
DEFAULT_TIMEOUT_MS = 200
#: Candidate cap per entity type before the merged ranking (the result cap).
_PER_TYPE_CANDIDATES = 50

#: Supported entity types, in canonical tie-break order.
ENTITY_TYPES = ("player", "team", "series", "competition")
_TYPE_ORDER = {t: i for i, t in enumerate(ENTITY_TYPES)}

#: Cross-type weights (a strong team match outranks a weak player match).
_TYPE_WEIGHTS = {
    "player": 1.0,
    "team": 0.95,
    "series": 0.9,
    "competition": 0.85,
}

#: Relevance boosts on top of similarity * weight.
_EXACT_BOOST = 0.25
_PREFIX_BOOST = 0.1


def _entity_spec(entity_type: str) -> tuple[Any, str, float]:
    """(model, name field, weight) for one entity type."""
    from apps.competitions.models import Competition, Series
    from apps.players.models import Player
    from apps.teams.models import Team

    return {
        "player": (Player, "full_name", _TYPE_WEIGHTS["player"]),
        "team": (Team, "name", _TYPE_WEIGHTS["team"]),
        "series": (Series, "name", _TYPE_WEIGHTS["series"]),
        "competition": (Competition, "name", _TYPE_WEIGHTS["competition"]),
    }[entity_type]


def _base_queryset(entity_type: str, q: str) -> QuerySet[Any]:
    """The index-backed trigram queryset for one entity type (``%`` operator)."""
    model, field, _weight = _entity_spec(entity_type)
    qs: QuerySet[Any] = model.objects
    if hasattr(model, "is_active") and hasattr(model, "deleted_at"):
        qs = qs.filter(is_active=True, deleted_at__isnull=True)
    # The `_sim` annotation is dynamic; django-stubs cannot resolve it.
    return qs.annotate(_sim=TrigramSimilarity(field, q)).filter(  # type: ignore[no-any-return]
        **{f"{field}__trigram_similar": q}
    )


def normalize_query(q: str) -> str:
    """Lowercase and collapse runs of whitespace."""
    return re.sub(r"\s+", " ", q.strip().lower())


def validate_query(q: str) -> str:
    """Normalise and enforce the length bounds (raise ``ValueError``)."""
    if not isinstance(q, str):
        raise ValueError("query must be a string")
    normalized = normalize_query(q)
    if len(normalized) < MIN_QUERY_LENGTH:
        raise ValueError(f"query must be at least {MIN_QUERY_LENGTH} characters")
    if len(normalized) > MAX_QUERY_LENGTH:
        raise ValueError(f"query must be at most {MAX_QUERY_LENGTH} characters")
    return normalized


def _boost(name: str, q: str) -> float:
    lowered = name.lower()
    if lowered == q:
        return _EXACT_BOOST
    if lowered.startswith(q):
        return _PREFIX_BOOST
    return 0.0


def _search_entity(entity_type: str, q: str) -> list[dict[str, Any]]:
    _model, field, weight = _entity_spec(entity_type)
    results: list[dict[str, Any]] = []
    for obj in _base_queryset(entity_type, q)[:_PER_TYPE_CANDIDATES]:
        name = str(getattr(obj, field))
        similarity = float(getattr(obj, SIM_ATTR) or 0.0)
        score = similarity * weight + _boost(name, q)
        results.append(
            {
                "type": entity_type,
                "id": str(obj.pk),
                "name": name,
                "score": round(score, 6),
            }
        )
    return results


def _sort_key(item: dict[str, Any]) -> tuple[float, int, str]:
    return (-item["score"], _TYPE_ORDER[item["type"]], item["name"].lower())


@contextmanager
def _statement_timeout(timeout_ms: int | None) -> Iterator[None]:
    """Bound every statement in the block with ``SET LOCAL`` (auto-restores)."""
    if timeout_ms is None:
        yield
        return
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = %s", [timeout_ms])
        yield


def cross_entity_search(
    q: str,
    *,
    types: Sequence[str] | None = None,
    limit: int = DEFAULT_LIMIT,
    timeout_ms: int | None = DEFAULT_TIMEOUT_MS,
) -> list[dict[str, Any]]:
    """Search players/teams/series/competitions; ranked, capped, safe.

    Raises ``ValueError`` for an empty/too-long query or an unknown type.
    ``timeout_ms=None`` disables the per-statement timeout (tests/perf).
    """
    query = validate_query(q)
    if types is None:
        types = ENTITY_TYPES
    unknown = set(types) - set(ENTITY_TYPES)
    if unknown:
        raise ValueError(f"unknown search type: {sorted(unknown)[0]!r}")
    limit = min(max(int(limit), 1), MAX_LIMIT)
    types_key = ",".join(types)

    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    try:
        with _statement_timeout(timeout_ms):
            for entity_type in types:
                results.extend(_search_entity(entity_type, query))
    except OperationalError:
        # A pathological query hit the statement timeout and was cancelled by
        # Postgres; count it so the SearchTimeoutHigh alert can fire, then let
        # the error surface as the 5xx the caller decides on.
        metrics.SEARCH_TIMEOUTS.inc()
        raise
    finally:
        metrics.SEARCH_LATENCY_SECONDS.observe(time.perf_counter() - started)
    results.sort(key=_sort_key)
    metrics.SEARCH_QUERIES.labels(types=types_key).inc()
    return results[:limit]


def explain_search(q: str, *, types: Sequence[str] | None = None) -> str:
    """EXPLAIN plan(s) for the search query (diagnostic + gate).

    Runs with ``enable_seqscan=off`` so the planner *must* use an index for the
    ``%`` operator; the gate asserts the trigram index appears in the plan.
    """
    query = validate_query(q)
    if types is None:
        types = ("player",)
    parts: list[str] = []
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL enable_seqscan = off")
        for entity_type in types:
            plan = _base_queryset(entity_type, query).explain()
            parts.append(f"== {entity_type} ==\n{plan}")
    return "\n".join(parts)


__all__ = [
    "DEFAULT_LIMIT",
    "DEFAULT_TIMEOUT_MS",
    "ENTITY_TYPES",
    "MAX_LIMIT",
    "MAX_QUERY_LENGTH",
    "MIN_QUERY_LENGTH",
    "cross_entity_search",
    "explain_search",
    "normalize_query",
    "validate_query",
]
