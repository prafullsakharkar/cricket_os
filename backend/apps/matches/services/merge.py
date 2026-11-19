"""Reversible match merge/split service (Phase I Task 4, commit 4c).

``merge_matches`` supersedes a duplicate match row: child rows of the merged
match (``match_team``, ``toss``, ``match_player``, ``match_official``,
``match_result``, ``match_award``, ``innings`` and the ``provider_entity_map``
match rows) move into the canonical match where they fit without conflict, the
merged row is flagged ``merged_into_id`` (invisible to every selector), and a
``match_merge`` row records the exact ``before``/``after`` child-row inventory.
**No data is deleted** — ``split_match`` reverses the inventory exactly.

Unlike player merges, match merges are **reversible by design**
(docs/MATCH_DEDUPLICATION.md §4): a merge is the post-hoc safety net for
matches that slipped past the ingest-time canonicalisation control (4b).

Child re-pointing by policy:

* ``match_team`` — by ``team_number`` (also respects ``ux_match_team``);
* ``toss`` / ``match_result`` — 1:1: moved only when the canonical has none;
* ``match_player`` — by ``player``; ``match_official`` — by ``role``;
  ``match_award`` — by ``award_type``;
* ``innings`` — by ``(innings_number, is_super_over, super_over_number)``;
  **conflicting innings stay on the merged row** and are excluded from every
  selector/statistics read via ``merged_into_id``;
* ``provider_entity_map`` (``entity_type='match'``) — re-pointed to the
  canonical with ``is_primary`` per the source-precedence policy;
* ``player_match_stat`` — Tier-2 **derived** rows (built on
  ``match.completed``): captured verbatim into the ``before`` inventory and
  removed from the live table so the career/team/series aggregates can never
  count the same match twice. The canonical's rows are rebuilt from the
  combined source by :func:`recompute_affected_series`; ``split_match``
  recreates the captured rows (same ids), so nothing is lost.

Every merge/split runs inside one transaction guarded by PostgreSQL advisory
locks on both match ids (a concurrent retry of the same merge collapses onto
one row), bumps both matches' ``revision``, and appends the ``AuditLog`` row
via the ``match.merged`` / ``match.split`` topics.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.db import connection, transaction
from django.utils import timezone

from apps.core.errors import Conflict
from apps.core.events import TOPIC_MATCH_MERGED, TOPIC_MATCH_SPLIT, publish
from apps.innings.models import Innings
from apps.matches.models import (
    Match,
    MatchAward,
    MatchMerge,
    MatchOfficial,
    MatchPlayer,
    MatchResult,
    MatchTeam,
    Toss,
)
from apps.matches.services.canonicalise import _is_live_authoritative
from apps.providers.models import ProviderEntityMap
from apps.statistics.models import PlayerMatchStat

_ENTITY_TYPE = "match"

#: Child tables whose rows are re-pointed on merge/split (json key -> model).
#: Values are Django model classes; ``Any`` keeps the dynamic ``model.objects``
#: lookup typable (the models share the ``match_id`` column, nothing more).
_MODEL_FOR_TABLE: dict[str, Any] = {
    "match_team": MatchTeam,
    "toss": Toss,
    "match_player": MatchPlayer,
    "match_official": MatchOfficial,
    "match_result": MatchResult,
    "match_award": MatchAward,
    "innings": Innings,
}


def _as_uuid(actor: object) -> UUID | None:
    """``actor`` as a UUID when it is one (else ``None`` — store a label only)."""
    if isinstance(actor, UUID):
        return actor
    if isinstance(actor, str):
        try:
            return UUID(actor)
        except ValueError:
            return None
    return None


def _advisory_lock(match_id: UUID) -> None:
    """Serialize concurrent merges/splits touching ``match_id``."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            [f"match_merge:{match_id}"],
        )


def _innings_key(innings: Innings) -> tuple[int, bool, int]:
    return (innings.innings_number, innings.is_super_over, innings.super_over_number or 0)


def _move_children(merged: Match, canonical: Match) -> dict[str, list[dict[str, Any]]]:
    """Move the merged match's child rows into the canonical where they fit.

    Returns the ``before`` inventory of every row that moved (per table: row id
    + prior match id), which is exactly what :func:`split_match` reverses.
    Rows that cannot move without violating a unique constraint stay on the
    merged row (invisible via ``merged_into_id``).
    """
    inventory: dict[str, list[dict[str, Any]]] = {}

    # 1. match_team — by team_number (also respect ux_match_team on team_id).
    existing_numbers = set(
        MatchTeam.objects.filter(match_id=canonical.pk).values_list("team_number", flat=True)
    )
    existing_teams = set(
        MatchTeam.objects.filter(match_id=canonical.pk).values_list("team_id", flat=True)
    )
    for team_row in MatchTeam.objects.filter(match_id=merged.pk).order_by("team_number"):
        if team_row.team_number in existing_numbers or team_row.team_id in existing_teams:
            continue
        inventory.setdefault("match_team", []).append(
            {
                "id": str(team_row.id),
                "prior_match_id": str(merged.pk),
                "team_number": team_row.team_number,
            }
        )
        team_row.match_id = canonical.pk
        team_row.save(update_fields=["match_id"])
        existing_numbers.add(team_row.team_number)
        existing_teams.add(team_row.team_id)

    # 2. toss — 1:1; moved only when the canonical has none.
    toss = Toss.objects.filter(match_id=merged.pk).first()
    if toss is not None and not Toss.objects.filter(match_id=canonical.pk).exists():
        inventory.setdefault("toss", []).append(
            {"id": str(toss.id), "prior_match_id": str(merged.pk)}
        )
        toss.match_id = canonical.pk
        toss.save(update_fields=["match_id"])

    # 3. match_player — by player.
    existing_players = set(
        MatchPlayer.objects.filter(match_id=canonical.pk).values_list("player_id", flat=True)
    )
    for player_row in MatchPlayer.objects.filter(match_id=merged.pk):
        if player_row.player_id in existing_players:
            continue
        inventory.setdefault("match_player", []).append(
            {
                "id": str(player_row.id),
                "prior_match_id": str(merged.pk),
                "player_id": str(player_row.player_id),
            }
        )
        player_row.match_id = canonical.pk
        player_row.save(update_fields=["match_id"])
        existing_players.add(player_row.player_id)

    # 4. match_official — by role.
    existing_roles = set(
        MatchOfficial.objects.filter(match_id=canonical.pk).values_list("role", flat=True)
    )
    for official_row in MatchOfficial.objects.filter(match_id=merged.pk):
        if official_row.role in existing_roles:
            continue
        inventory.setdefault("match_official", []).append(
            {
                "id": str(official_row.id),
                "prior_match_id": str(merged.pk),
                "role": official_row.role,
            }
        )
        official_row.match_id = canonical.pk
        official_row.save(update_fields=["match_id"])
        existing_roles.add(official_row.role)

    # 5. match_result — 1:1; moved only when the canonical has none.
    result = MatchResult.objects.filter(match_id=merged.pk).first()
    if result is not None and not MatchResult.objects.filter(match_id=canonical.pk).exists():
        inventory.setdefault("match_result", []).append(
            {"id": str(result.id), "prior_match_id": str(merged.pk)}
        )
        result.match_id = canonical.pk
        result.save(update_fields=["match_id"])

    # 6. match_award — by award_type.
    existing_awards = set(
        MatchAward.objects.filter(match_id=canonical.pk).values_list("award_type", flat=True)
    )
    for award_row in MatchAward.objects.filter(match_id=merged.pk):
        if award_row.award_type in existing_awards:
            continue
        inventory.setdefault("match_award", []).append(
            {
                "id": str(award_row.id),
                "prior_match_id": str(merged.pk),
                "award_type": award_row.award_type,
            }
        )
        award_row.match_id = canonical.pk
        award_row.save(update_fields=["match_id"])
        existing_awards.add(award_row.award_type)

    # 7. innings — by (innings_number, is_super_over, super_over_number).
    existing_innings = {_innings_key(i) for i in Innings.objects.filter(match_id=canonical.pk)}
    for innings_row in Innings.objects.filter(match_id=merged.pk).order_by("innings_number"):
        key = _innings_key(innings_row)
        if key in existing_innings:
            continue
        inventory.setdefault("innings", []).append(
            {
                "id": str(innings_row.id),
                "prior_match_id": str(merged.pk),
                "innings_number": innings_row.innings_number,
                "is_super_over": innings_row.is_super_over,
                "super_over_number": innings_row.super_over_number,
            }
        )
        innings_row.match_id = canonical.pk
        innings_row.save(update_fields=["match_id"])
        existing_innings.add(key)

    # 8. provider_entity_map (entity_type='match') — re-point with precedence.
    #    ``is_primary`` in the inventory entry is the **post-merge** value so
    #    the ``after`` ledger mirrors reality; split restores ``prior_is_primary``.
    map_is_primary = _is_live_authoritative(canonical)
    for map_row in ProviderEntityMap.objects.filter(
        entity_type=_ENTITY_TYPE, internal_id=merged.pk
    ):
        inventory.setdefault("provider_entity_map", []).append(
            {
                "id": str(map_row.id),
                "prior_internal_id": str(merged.pk),
                "prior_is_primary": map_row.is_primary,
                "is_primary": map_is_primary,
            }
        )
        map_row.internal_id = canonical.pk
        map_row.is_primary = map_is_primary
        map_row.save(update_fields=["internal_id", "is_primary"])

    # 9. player_match_stat — Tier-2 derived rows (built on match.completed).
    #    Captured verbatim so split can recreate them; removed from the live
    #    table so the aggregates never count the same match twice. The
    #    canonical's own rows are rebuilt from the combined source by
    #    recompute_affected_series (post-commit).
    stats_rows = list(PlayerMatchStat.objects.filter(match_id=merged.pk))
    if stats_rows:
        inventory["player_match_stat"] = [
            {
                "id": str(row.id),
                "player_id": str(row.player_id),
                "team_id": str(row.team_id) if row.team_id else None,
                "innings_id": str(row.innings_id) if row.innings_id else None,
                "did_bat": row.did_bat,
                "did_bowl": row.did_bowl,
                "did_field": row.did_field,
                "is_playing_xi": row.is_playing_xi,
                "batting": row.batting,
                "bowling": row.bowling,
                "fielding": row.fielding,
                "source_provider_id": (
                    str(row.source_provider_id) if row.source_provider_id else None
                ),
                "computed_at": row.computed_at.isoformat(),
                "is_partial": row.is_partial,
                "revision": row.revision,
            }
            for row in stats_rows
        ]
        PlayerMatchStat.objects.filter(match_id=merged.pk).delete()

    return inventory


def _after_inventory(
    before: dict[str, list[dict[str, Any]]], canonical_id: UUID
) -> dict[str, list[dict[str, Any]]]:
    """Post-merge inventory mirror of ``before`` (for verification/split)."""
    after: dict[str, list[dict[str, Any]]] = {}
    for table, entries in before.items():
        after[table] = []
        if table == "player_match_stat":
            # Captured rows are no longer live; the canonical's own rows were
            # rebuilt in place (not moved), so the ``after`` mirror is empty.
            continue
        for entry in entries:
            if table == "provider_entity_map":
                after[table].append(
                    {
                        "id": entry["id"],
                        "internal_id": str(canonical_id),
                        "is_primary": entry["is_primary"],
                    }
                )
            else:
                after[table].append({"id": entry["id"], "match_id": str(canonical_id)})
    return after


def merge_matches(
    merged: Match,
    canonical: Match,
    *,
    actor: object,
    reason: str | None = None,
) -> MatchMerge:
    """Merge ``merged`` into ``canonical``; returns the ``MatchMerge`` row.

    - Redirect chains resolve first; ``merged == canonical`` and cycles raise
      :class:`Conflict`.
    - Retrying an already-merged match is a no-op returning the existing row.
    - The whole merge (child moves + inventory + redirect + audit) is one
      atomic block guarded by advisory locks on both ids.
    - No data is deleted; ``split_match`` reverses the ``before`` inventory.
    """
    merged_id = merged.pk
    # Idempotent: the merged row already has a merge record -> no-op. Checked
    # *before* resolving redirects so a retry of an already-merged match
    # returns its existing row instead of tripping the self-merge guard.
    existing = MatchMerge.objects.filter(merged_match_id=merged_id).first()
    if existing is not None:
        return existing

    canonical = canonical.resolve_canonical()
    merged = merged.resolve_canonical()
    if canonical.pk == merged.pk:
        raise Conflict("Cannot merge a match into itself or its merge target")

    with transaction.atomic():
        _advisory_lock(canonical.pk)
        _advisory_lock(merged.pk)
        existing = MatchMerge.objects.filter(merged_match_id=merged_id).first()
        if existing is not None:
            return existing

        before = _move_children(merged, canonical)
        after = _after_inventory(before, canonical.pk)

        merged.merged_into_id = canonical.pk
        merged.revision = (merged.revision or 0) + 1
        merged.save(update_fields=["merged_into_id", "revision"])
        canonical.revision = (canonical.revision or 0) + 1
        canonical.save(update_fields=["revision"])

        merge_row = MatchMerge.objects.create(
            merged_match=merged,
            canonical_match=canonical,
            before=before,
            after=after,
            status="merged",
            created_by=_as_uuid(actor),
        )
        publish(
            TOPIC_MATCH_MERGED,
            {
                "merged_match_id": str(merged.pk),
                "canonical_match_id": str(canonical.pk),
                "actor": str(actor),
            },
            on_commit=False,
        )

    # Stats recompute runs after the merge commits: a Tier-2 failure must not
    # roll back the merge (the statistics sweep is the safety net for a stale
    # watermark).
    recompute_affected_series(canonical, merged)
    return merge_row


def split_match(merge_row: MatchMerge, *, actor: object) -> MatchMerge:
    """Reverse ``merge_row`` exactly; returns the row with ``status='split'``.

    Only ``status='merged'`` rows split; splitting twice (or a split row) is a
    :class:`Conflict`, not a silent no-op.
    """
    if merge_row.status != "merged":
        raise Conflict(f"MatchMerge {merge_row.pk} is already {merge_row.status}")
    with transaction.atomic():
        _advisory_lock(merge_row.merged_match_id)
        _advisory_lock(merge_row.canonical_match_id)
        merge_row.refresh_from_db()
        if merge_row.status != "merged":
            raise Conflict(f"MatchMerge {merge_row.pk} is already {merge_row.status}")

        merged = Match.objects.get(pk=merge_row.merged_match_id)
        canonical = Match.objects.get(pk=merge_row.canonical_match_id)
        before = merge_row.before

        for table, model in _MODEL_FOR_TABLE.items():
            for entry in before.get(table, []):
                row = model.objects.get(pk=entry["id"])
                row.match_id = UUID(entry["prior_match_id"])
                row.save(update_fields=["match_id"])

        for entry in before.get("provider_entity_map", []):
            row = ProviderEntityMap.objects.get(pk=entry["id"])
            row.internal_id = merged.pk
            row.is_primary = bool(entry.get("prior_is_primary", False))
            row.save(update_fields=["internal_id", "is_primary"])

        # Recreate the merged match's Tier-2 derived rows captured at merge
        # time (same ids, exact payloads).
        for entry in before.get("player_match_stat", []):
            PlayerMatchStat.objects.create(
                id=entry["id"],
                match_id=merged.pk,
                player_id=entry["player_id"],
                team_id=entry.get("team_id"),
                innings_id=entry.get("innings_id"),
                did_bat=entry["did_bat"],
                did_bowl=entry["did_bowl"],
                did_field=entry["did_field"],
                is_playing_xi=entry["is_playing_xi"],
                batting=entry["batting"],
                bowling=entry["bowling"],
                fielding=entry["fielding"],
                source_provider_id=entry.get("source_provider_id"),
                computed_at=entry["computed_at"],
                is_partial=entry.get("is_partial", False),
                revision=entry.get("revision", 1),
            )

        merged.merged_into_id = None
        merged.revision = (merged.revision or 0) + 1
        merged.save(update_fields=["merged_into_id", "revision"])
        canonical.revision = (canonical.revision or 0) + 1
        canonical.save(update_fields=["revision"])

        merge_row.status = "split"
        merge_row.split_by = _as_uuid(actor)
        merge_row.split_at = timezone.now()
        merge_row.save(update_fields=["status", "split_by", "split_at"])
        publish(
            TOPIC_MATCH_SPLIT,
            {
                "merge_id": str(merge_row.pk),
                "merged_match_id": str(merged.pk),
                "canonical_match_id": str(canonical.pk),
                "actor": str(actor),
            },
            on_commit=False,
        )

    recompute_affected_series(canonical, merged)
    return merge_row


def recompute_affected_series(canonical: Match, merged: Match) -> list[str]:
    """Re-run the Tier-2 pipeline for the two affected matches (post merge/split).

    For a terminal canonical the per-match ``player_match_stat`` rows are
    rebuilt from the *combined* source (the merged innings now live under the
    canonical), so the aggregates include each match exactly once; then both
    matches' dependency keys are recomputed. Running twice is a no-op (same
    source digests, no revision bump on the aggregates). A Tier-2 failure here
    never rolls back the merge — it is called outside the merge transaction
    and the statistics sweep is the safety net.
    """
    from apps.matches.selectors.reads import LIVE_LIST_STATUSES
    from apps.statistics.services import stats as stats_service
    from apps.statistics.services.recompute import recompute_match

    if canonical.status not in LIVE_LIST_STATUSES and canonical.innings.exists():
        # Not in play and has innings: rebuild the canonical's per-match rows
        # from the combined source (the merged innings now live under it), the
        # same way ``match.completed`` does. A live canonical has no stats rows
        # yet (they are built on completion) and must not get partial ones
        # mid-play; a match with no innings (summary-only/synthetic) keeps its
        # existing rows untouched.
        stats_service.build_match_stats(canonical, is_partial=stats_service._is_partial(canonical))

    affected: set[str] = set()
    for match in (canonical, merged):
        affected.update(recompute_match(match, correction=True))
    return sorted(affected)


__all__ = ["merge_matches", "recompute_affected_series", "split_match"]
