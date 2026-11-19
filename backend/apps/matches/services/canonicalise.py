"""Cross-provider match canonicalisation (Phase I Task 4, commit 4b).

``docs/MATCH_DEDUPLICATION.md`` is the policy. This module is the **ingest-time
prevention**: fixture sync consults :func:`find_canonical_match` BEFORE creating
a ``Match`` row, so the same fixture arriving from two providers collapses onto
one canonical match (a ``provider_entity_map(entity_type='match')`` row) instead
of creating a duplicate.

Decisions by score band (settings, PROPOSED):

* ``>= MATCH_DEDUP_AUTO_THRESHOLD`` (0.95) — **canonical**: the incoming
  provider's fixture id is mapped onto the existing match; no new ``Match``;
* ``>= MATCH_DEDUP_REVIEW_THRESHOLD`` (0.60) — **candidate**: a
  ``match_duplicate_candidate`` row is queued (via :func:`flag_duplicate_candidate`)
  and the fixture is created as a new stub (never auto-merged below the auto
  threshold);
* below 0.60 — **miss**: the caller creates a new match as today.

The polling hook (:func:`provider_match_id_for`) lets the live write path
resolve a canonical match's id *for the incoming provider* so deliveries append
into the canonical match.
"""

from __future__ import annotations

import datetime
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from apps.matches.models import Match, MatchDuplicateCandidate, MatchTeam
from apps.matches.selectors.reads import TERMINAL_STATUSES

_ENTITY_TYPE = "match"

#: Decision values returned by :func:`find_canonical_match`.
DECISION_CANONICAL = "canonical"
DECISION_CANDIDATE = "candidate"
DECISION_MISS = "miss"

_EPOCH = datetime.datetime(1970, 1, 1, tzinfo=datetime.UTC)


@dataclass(frozen=True)
class Canonicalisation:
    """Result of consulting :func:`find_canonical_match` for one fixture.

    ``decision`` is one of ``DECISION_*``. ``canonical_match`` is the existing
    match for ``canonical`` (map row written) and the plausible canonical for
    ``candidate`` (the pair was queued for steward review); it is ``None`` for
    ``miss``. ``score``/``breakdown`` describe the best candidate and feed
    :func:`flag_duplicate_candidate`.
    """

    decision: str
    canonical_match: Match | None = None
    score: float = 0.0
    breakdown: dict[str, float] = field(default_factory=dict)


def canonical_key(
    *,
    series_or_competition_id: UUID | None,
    team_ids: frozenset[UUID],
    start_time_utc: datetime.datetime,
    venue_id: UUID | None = None,
    match_number: int | None = None,
    match_desc: str | None = None,
) -> dict[str, Any]:
    """The matching predicate: a normalised signature for one fixture.

    The UTC start time is floored onto the ``MATCH_DEDUP_START_TOLERANCE_MINUTES``
    grid so two fixtures whose starts differ by less than a tolerance share a
    bucket (the caller queries the bucket and its two neighbours, then
    :func:`score_candidate` applies the exact tolerance). ``match_number`` /
    ``match_desc`` are included only when present — they act as tie-breakers,
    never as hard requirements.
    """
    tolerance_minutes = int(getattr(settings, "MATCH_DEDUP_START_TOLERANCE_MINUTES", 180))
    if start_time_utc.tzinfo is None:
        start_time_utc = timezone.make_aware(start_time_utc, datetime.UTC)
    elapsed = (start_time_utc - _EPOCH).total_seconds()
    bucket = int(elapsed // (tolerance_minutes * 60))
    return {
        "series_or_competition_id": series_or_competition_id,
        "team_ids": frozenset(team_ids),
        "start_time_utc": start_time_utc,
        "start_bucket": bucket,
        "venue_id": venue_id,
        "match_number": match_number,
        "match_desc": match_desc,
    }


def _match_team_ids(match_id: UUID) -> frozenset[UUID]:
    return frozenset(MatchTeam.objects.filter(match_id=match_id).values_list("team_id", flat=True))


def _as_score(value: float) -> Decimal:
    """Round a float score to the Decimal(4,3) column's precision."""
    return Decimal(str(round(value, 3)))


#: Partial-credit factors (docs/MATCH_DEDUPLICATION.md §2): a signal that is
#: present on both sides but disagrees drops the score below the auto
#: threshold, into the steward review band.
_SCORE_VENUE_MISSING = 0.90
_SCORE_NUMBER_MISMATCH = 0.85
_SCORE_DESC_MISMATCH = 0.85


def score_candidate(existing: Match, key: dict[str, Any]) -> tuple[float, dict[str, float]]:
    """Score ``existing`` against the incoming fixture's signature -> [0, 1].

    1.0 when every present signal agrees; 0.0 when the teams or the
    series/competition differ, or the start is outside the tolerance (hard
    gates); partial credit when the venue is missing on one side (0.90) or
    ``match_number``/``match_desc`` differ (0.85) — see
    docs/MATCH_DEDUPLICATION.md §2.
    """
    breakdown: dict[str, float] = {}
    incoming_teams = frozenset(key.get("team_ids") or ())
    incoming_series = key.get("series_or_competition_id")
    incoming_start = key.get("start_time_utc")
    venue_id = key.get("venue_id")
    match_number = key.get("match_number")
    match_desc = key.get("match_desc")

    # Hard gate: the team pair (order-insensitive).
    existing_teams = _match_team_ids(existing.pk)
    if incoming_teams and existing_teams and incoming_teams != existing_teams:
        breakdown["teams"] = 0.0
        return 0.0, breakdown
    breakdown["teams"] = 1.0

    # Hard gate: series/competition — both must have one to compare.
    existing_series = existing.series_id or existing.competition_id
    if (
        incoming_series is not None
        and existing_series is not None
        and incoming_series != existing_series
    ):
        breakdown["series"] = 0.0
        return 0.0, breakdown
    breakdown["series"] = 1.0

    # Hard gate: start time within the configured tolerance (the bucket is a
    # pre-filter; the exact comparison lives here).
    if incoming_start is not None:
        tolerance_minutes = int(getattr(settings, "MATCH_DEDUP_START_TOLERANCE_MINUTES", 180))
        if abs((existing.start_time_utc - incoming_start).total_seconds()) > tolerance_minutes * 60:
            breakdown["start_time"] = 0.0
            return 0.0, breakdown
        breakdown["start_time"] = 1.0

    # Venue: only when at least one side has one. A missing venue on one side
    # is partial credit (no evidence either way, but not full agreement); a
    # disagreement when both sides have one is a hard miss.
    if venue_id is not None or existing.venue_id is not None:
        if venue_id is None or existing.venue_id is None:
            breakdown["venue"] = _SCORE_VENUE_MISSING
            return _SCORE_VENUE_MISSING, breakdown
        if venue_id != existing.venue_id:
            breakdown["venue"] = 0.0
            return 0.0, breakdown
        breakdown["venue"] = 1.0
    else:
        # Neither side has a venue: no evidence either way.
        breakdown["venue"] = 1.0

    # Tie-breakers (partial credit when present on both sides and different).
    if match_number is not None and existing.match_number is not None:
        if match_number != existing.match_number:
            breakdown["match_number"] = _SCORE_NUMBER_MISMATCH
            return _SCORE_NUMBER_MISMATCH, breakdown
        breakdown["match_number"] = 1.0
    if match_desc is not None and existing.match_desc is not None:
        if match_desc != existing.match_desc:
            breakdown["match_desc"] = _SCORE_DESC_MISMATCH
            return _SCORE_DESC_MISMATCH, breakdown
        breakdown["match_desc"] = 1.0

    return 1.0, breakdown


def _candidate_matches(key: dict[str, Any], provider_id: UUID | None) -> list[Match]:
    """Plausible existing matches: same series/competition or a shared team,
    start time within the tolerance bucket ± 1 (the exact comparison is in
    :func:`score_candidate`).

    The incoming provider's **own** matches are excluded: same-provider
    duplicates are already impossible via the ``(source_provider_id,
    provider_match_id)`` unique constraint, and this service prevents
    cross-provider duplicates only.
    """
    tolerance_minutes = int(getattr(settings, "MATCH_DEDUP_START_TOLERANCE_MINUTES", 180))
    window = datetime.timedelta(minutes=tolerance_minutes)
    qs = Match.objects.exclude_merged().filter(
        start_time_utc__gte=key["start_time_utc"] - window,
        start_time_utc__lte=key["start_time_utc"] + window,
    )
    if provider_id is not None:
        qs = qs.exclude(source_provider_id=provider_id)
    series = key["series_or_competition_id"]
    if series is not None:
        qs = qs.filter(Q(series_id=series) | Q(competition_id=series))
    team_ids = key["team_ids"]
    if team_ids:
        qs = qs.filter(teams__team_id__in=team_ids)
    return list(qs.distinct())


def _is_live_authoritative(canonical: Match) -> bool:
    """True when the incoming (live) provider's card is primary per policy:
    the live provider is authoritative while a match is not yet terminal
    (docs/MATCH_DEDUPLICATION.md §1). Completed-card precedence is managed by
    the reversible merge service (4c)."""
    return canonical.status not in TERMINAL_STATUSES


def _upsert_match_map(
    provider: Any,
    canonical: Match,
    provider_match_id: str,
) -> None:
    from apps.providers.models import ProviderEntityMap

    ProviderEntityMap.objects.update_or_create(
        provider=provider,
        entity_type=_ENTITY_TYPE,
        provider_entity_id=provider_match_id,
        defaults={
            "internal_id": canonical.pk,
            "provider_unique_name": provider_match_id,
            "is_primary": _is_live_authoritative(canonical),
            "verified": False,
        },
    )


def find_canonical_match(provider: Any, fixture_attrs: Mapping[str, Any]) -> Canonicalisation:
    """Consult before creating a ``Match``: is this fixture already known?

    ``fixture_attrs`` carries ``provider_match_id`` plus the canonical-key
    signals (``team_ids``, ``start_time_utc``, ``series_or_competition_id``,
    ``venue_id``, ``match_number``, ``match_desc``).
    """
    key = canonical_key(
        series_or_competition_id=fixture_attrs.get("series_or_competition_id"),
        team_ids=frozenset(fixture_attrs.get("team_ids") or ()),
        start_time_utc=fixture_attrs["start_time_utc"],
        venue_id=fixture_attrs.get("venue_id"),
        match_number=fixture_attrs.get("match_number"),
        match_desc=fixture_attrs.get("match_desc"),
    )
    best: Match | None = None
    best_score = 0.0
    best_breakdown: dict[str, float] = {}
    provider_id = getattr(provider, "pk", None)
    for existing in _candidate_matches(key, provider_id):
        score, breakdown = score_candidate(existing, key)
        if score > best_score:
            best, best_score, best_breakdown = existing, score, breakdown

    auto_threshold = float(getattr(settings, "MATCH_DEDUP_AUTO_THRESHOLD", 0.95))
    review_threshold = float(getattr(settings, "MATCH_DEDUP_REVIEW_THRESHOLD", 0.60))
    if best is None or best_score == 0.0:
        return Canonicalisation(decision=DECISION_MISS, score=best_score, breakdown=best_breakdown)
    if best_score >= auto_threshold:
        _upsert_match_map(
            provider,
            best,
            str(fixture_attrs.get("provider_match_id")),
        )
        return Canonicalisation(
            decision=DECISION_CANONICAL,
            canonical_match=best,
            score=best_score,
            breakdown=best_breakdown,
        )
    if best_score >= review_threshold:
        return Canonicalisation(
            decision=DECISION_CANDIDATE,
            canonical_match=best,
            score=best_score,
            breakdown=best_breakdown,
        )
    return Canonicalisation(decision=DECISION_MISS, score=best_score, breakdown=best_breakdown)


def flag_duplicate_candidate(
    candidate_match: Match,
    canonical_match: Match,
    *,
    score: float,
    breakdown: dict[str, float] | None = None,
) -> MatchDuplicateCandidate:
    """Queue a (candidate, plausible-canonical) match pair for steward review.

    ``get_or_create`` on the unique pair; an existing open row is left as-is.
    """
    row, _ = MatchDuplicateCandidate.objects.get_or_create(
        candidate_match=candidate_match,
        canonical_match=canonical_match,
        defaults={
            "score": _as_score(score),
            "breakdown": breakdown,
            "status": "open",
        },
    )
    return row


def provider_match_id_for(match: Match, provider_code: str | None) -> str:
    """The provider's own fixture id for ``match`` (the polling hook).

    After canonicalisation the canonical match's ``provider_match_id`` belongs
    to the match's original owner; the incoming provider's id lives in its map
    row. Falls back to the match's own id when no map row exists.
    """
    if provider_code:
        from apps.providers.models import ProviderEntityMap

        row = (
            ProviderEntityMap.objects.filter(
                provider__code=provider_code,
                entity_type=_ENTITY_TYPE,
                internal_id=match.pk,
            )
            .values_list("provider_entity_id", flat=True)
            .first()
        )
        if row is not None:
            return str(row)
    if match.provider_match_id is None:
        raise ValueError(f"match {match.pk} has no provider_match_id to poll with")
    return match.provider_match_id


__all__ = [
    "DECISION_CANDIDATE",
    "DECISION_CANONICAL",
    "DECISION_MISS",
    "Canonicalisation",
    "canonical_key",
    "find_canonical_match",
    "flag_duplicate_candidate",
    "provider_match_id_for",
    "score_candidate",
]
