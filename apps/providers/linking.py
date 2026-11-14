"""Batch identity linking (Phase I Task 3).

``link_entity`` resolves one provider entity (player/team/venue/series) onto a
platform entity via the existing :func:`apps.providers.services.resolve` and
classifies the outcome:

* ``already_linked`` — a ``provider_entity_map`` row already exists;
* ``auto_linked`` — exactly one candidate at/above ``IDENTITY_MATCH_THRESHOLD``
  (0.95): a map row is created pointing at the existing entity;
* ``candidate_pending`` — a stub was created and flagged for steward review:
  players with a ``PlayerDuplicateCandidate`` in ``[0.60, 0.95)``; teams/
  venues/series stubs count as pending (their ``verified=False`` map row is
  the review signal — those entity types have no duplicate-candidate queue);
* ``stub_created`` — a player stub with no candidate in the review band.

The batch (``link_all``) runs each entity inside its own transaction (the
per-entity atomicity of ``resolve``) and reports counts by type/outcome plus
the steward workload estimate (candidates × review minutes).

**Never auto-merges** — linking creates a map row or a stub; it never merges
existing entities. **Never auto-verifies** — every created map row is
``verified=False`` so a steward explicitly confirms the link (IDENTITY_RESOLUTION
§2: linking two identities is a data-steward decision).

``dry_run=True`` computes the outcome without writing anything.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import transaction

from apps.providers.models import DataProvider, ProviderEntityMap
from apps.providers.services import get_resolver, resolve

#: Entity types the batch linking job understands (player/team/venue/series).
LINKABLE_ENTITY_TYPES = ("player", "team", "venue", "series")

OUTCOME_ALREADY_LINKED = "already_linked"
OUTCOME_AUTO_LINKED = "auto_linked"
OUTCOME_CANDIDATE_PENDING = "candidate_pending"
OUTCOME_STUB_CREATED = "stub_created"


@dataclass(frozen=True)
class LinkOutcome:
    """The classification of one ``link_entity`` call."""

    entity_type: str
    provider_entity_id: str
    outcome: str
    internal_id: UUID | None = None
    score: float | None = None
    breakdown: dict[str, Any] = field(default_factory=dict)
    dry_run: bool = False


@dataclass
class LinkSummary:
    """Aggregate of a batch linking run."""

    provider_code: str
    dry_run: bool
    entity_types: tuple[str, ...] = field(default_factory=tuple)
    by_type: dict[str, dict[str, int]] = field(default_factory=dict)
    totals: dict[str, int] = field(default_factory=dict)
    candidate_ids: list[str] = field(default_factory=list)
    stub_ids: list[str] = field(default_factory=list)
    residual_duplicates: int = 0

    @property
    def total_linked(self) -> int:
        return self.totals.get(OUTCOME_AUTO_LINKED, 0) + self.totals.get(OUTCOME_ALREADY_LINKED, 0)

    def steward_workload_minutes(self, minutes_per_review: int = 2) -> int:
        """Estimated steward time: pending candidates × minutes-per-review (ASSUMED).

        Uses the pending *total* (not ``candidate_ids``) so a dry-run, which
        computes outcomes without writing ids, still reports the workload.
        """
        count = self.totals.get(OUTCOME_CANDIDATE_PENDING, 0)
        return count * minutes_per_review


def link_entity(
    provider: DataProvider,
    entity_type: str,
    provider_entity_id: str,
    attributes: Mapping[str, Any],
    *,
    dry_run: bool = False,
) -> LinkOutcome:
    """Resolve one provider entity and classify the outcome (see module doc)."""
    if entity_type not in LINKABLE_ENTITY_TYPES:
        raise ValueError(
            f"unsupported entity type {entity_type!r}; expected one of {LINKABLE_ENTITY_TYPES}"
        )
    resolver = get_resolver(entity_type)

    existing = ProviderEntityMap.objects.filter(
        provider=provider,
        entity_type=entity_type,
        provider_entity_id=provider_entity_id,
    ).first()
    if existing is not None:
        return LinkOutcome(
            entity_type=entity_type,
            provider_entity_id=provider_entity_id,
            outcome=OUTCOME_ALREADY_LINKED,
            internal_id=existing.internal_id,
            dry_run=dry_run,
        )

    candidates = resolver.match(attributes)
    strong = [
        candidate
        for candidate in candidates
        if candidate.score >= settings.IDENTITY_MATCH_THRESHOLD
    ]
    if len(strong) == 1:
        top = strong[0]
        if not dry_run:
            internal_id = resolve(provider, entity_type, provider_entity_id, attributes)
        else:
            internal_id = top.internal_id
        return LinkOutcome(
            entity_type=entity_type,
            provider_entity_id=provider_entity_id,
            outcome=OUTCOME_AUTO_LINKED,
            internal_id=internal_id,
            score=top.score,
            breakdown=top.breakdown,
            dry_run=dry_run,
        )

    # Stub path: resolve creates a stub (map row verified=False). Classification:
    #   player -> candidate_pending iff any candidate in the review band
    #            [DUPLICATE_CANDIDATE_THRESHOLD, IDENTITY_MATCH_THRESHOLD);
    #   team/venue/series -> candidate_pending (verified=False row is the signal).
    if dry_run:
        pending_band = any(
            candidate.score >= settings.DUPLICATE_CANDIDATE_THRESHOLD for candidate in candidates
        )
        outcome = (
            OUTCOME_CANDIDATE_PENDING
            if (entity_type == "player" and pending_band) or entity_type != "player"
            else OUTCOME_STUB_CREATED
        )
        return LinkOutcome(
            entity_type=entity_type,
            provider_entity_id=provider_entity_id,
            outcome=outcome,
            dry_run=True,
        )

    internal_id = resolve(provider, entity_type, provider_entity_id, attributes)
    if entity_type == "player":
        from apps.players.models import PlayerDuplicateCandidate

        flagged = PlayerDuplicateCandidate.objects.filter(
            candidate_player_id=internal_id, status="open"
        ).exists()
        outcome = OUTCOME_CANDIDATE_PENDING if flagged else OUTCOME_STUB_CREATED
    else:
        outcome = OUTCOME_CANDIDATE_PENDING
    return LinkOutcome(
        entity_type=entity_type,
        provider_entity_id=provider_entity_id,
        outcome=outcome,
        internal_id=internal_id,
        dry_run=dry_run,
    )


def link_all(
    provider: DataProvider,
    entries: Iterable[tuple[str, str, Mapping[str, Any]]],
    *,
    dry_run: bool = False,
) -> LinkSummary:
    """Run ``link_entity`` over ``(entity_type, provider_entity_id, attributes)``.

    Each entity is resolved inside its own transaction (``resolve`` is
    per-call atomic), so one failure does not roll back the rest of the batch.
    """
    summary = LinkSummary(provider_code=provider.code, dry_run=dry_run)
    for entity_type, provider_entity_id, attributes in entries:
        with transaction.atomic():
            outcome = link_entity(
                provider,
                entity_type,
                provider_entity_id,
                attributes,
                dry_run=dry_run,
            )
        _record(summary, entity_type, outcome)
    return summary


def _record(summary: LinkSummary, entity_type: str, outcome: LinkOutcome) -> None:
    if entity_type not in summary.by_type:
        summary.by_type[entity_type] = dict.fromkeys(_OUTCOMES, 0)
    summary.by_type[entity_type][outcome.outcome] += 1
    summary.totals[outcome.outcome] = summary.totals.get(outcome.outcome, 0) + 1
    if outcome.outcome == OUTCOME_CANDIDATE_PENDING and outcome.internal_id is not None:
        summary.candidate_ids.append(str(outcome.internal_id))
    if outcome.outcome == OUTCOME_STUB_CREATED and outcome.internal_id is not None:
        summary.stub_ids.append(str(outcome.internal_id))


_OUTCOMES = (
    OUTCOME_ALREADY_LINKED,
    OUTCOME_AUTO_LINKED,
    OUTCOME_CANDIDATE_PENDING,
    OUTCOME_STUB_CREATED,
)

__all__ = [
    "LINKABLE_ENTITY_TYPES",
    "OUTCOME_ALREADY_LINKED",
    "OUTCOME_AUTO_LINKED",
    "OUTCOME_CANDIDATE_PENDING",
    "OUTCOME_STUB_CREATED",
    "LinkOutcome",
    "LinkSummary",
    "link_all",
    "link_entity",
]
