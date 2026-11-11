"""Tier-2 fantasy recompute pipeline (Task 4).

Turns a completed match's canonical rows into fantasy points for every
``auto_recompute`` ruleset version:

* **Recompute-from-source, never delta.** Every run rebuilds the snapshot from
  the canonical rows (Task 3) and runs the pure engine (Task 2), so a
  correction or a backfill reproduces byte-identical points and the pipeline
  is order-independent (ledger decisions 3/9/10 precedent).
* **Idempotence by (snapshot digest + version hash).** A run whose latest
  calc-audit row carries the same ``input_digest`` (== snapshot digest),
  ``version_hash`` (== engine content hash of the version) and engine version
  is a no-op — nothing is written, ``changed=False``.
* **Append-only ledger.** Each changed run appends a calc-audit row (with the
  compressed snapshot so the inputs stay reproducible) and one ledger row per
  player; "current" points derive from the latest run (see ``ledger.py``).
* **Serialization.** Each match runs under ``pg_advisory_xact_lock`` keyed
  ``fantasy:{match_id}`` plus a ``SELECT ... FOR UPDATE`` on the match row, so
  concurrent recomputes of the same match serialise.
* **Events.** ``fantasy.computed`` (match_id, calc_run_id) is published after
  commit for cache invalidation and audit. ``match.completed`` /
  ``correction.applied`` trigger recomputes; ``player.merged`` recomputes the
  winner for the union of both players' matches (plan ruling 5).
* **Provisional.** ``provisional=True`` runs are labelled on the audit row and
  never write the final ledger/read model; they are gated behind
  ``FANTASY_PROVISIONAL_ENABLED`` (default off) and per-delivery events never
  trigger them.

The DB -> engine mapping (:func:`ruleset_spec_for`) is the single place that
turns ``FantasyRulesetVersion`` + ``FantasyPointRule`` rows into the engine's
``RulesetSpec``; Task 6 authoring reuses it.
"""

from __future__ import annotations

import hashlib
import itertools
import time
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from apps.core.enums import MatchStatus
from apps.core.events import (
    TOPIC_CORRECTION_APPLIED,
    TOPIC_MATCH_COMPLETED,
    TOPIC_PLAYER_MERGED,
)
from apps.fantasy.engine import (
    FantasyResult,
    PointRuleSpec,
    RulesetPolicies,
    RulesetSpec,
    TierSpec,
    compute,
    content_hash,
    point_rule,
    validate_ruleset,
)
from apps.fantasy.models import (
    FantasyCalcAudit,
    FantasyPointRule,
    FantasyRulesetVersion,
    PlayerMatchPerformance,
)
from apps.fantasy.services import ledger
from apps.fantasy.services.adjustments import adjustment_totals_by_player
from apps.fantasy.services.snapshot import (
    build_snapshot,
    snapshot_from_payload,
    snapshot_payload,
)
from apps.matches.models import Match


class ProvisionalDisabledError(Exception):
    """A provisional recompute was requested while the flag is off."""


@dataclass(frozen=True)
class FantasyCalcRunReport:
    """The outcome of one ``recompute_match`` call (possibly many versions)."""

    match_id: str
    versions_computed: tuple[str, ...] = ()
    changed: bool = False
    calc_run_ids: tuple[str, ...] = ()
    snapshot_digest: str = ""
    player_count: int = 0
    is_partial: bool = False
    unsupported_rules: tuple[str, ...] = ()
    #: Per-player point diff vs the previous run (None when unchanged/provisional).
    diff: dict[str, Decimal] | None = None
    trigger: str = ""
    provisional: bool = False


# --------------------------------------------------------------------------- #
# DB -> engine mapping (single source of truth; Task 6 authoring reuses this)
# --------------------------------------------------------------------------- #


def _point_rule_from_db(rule: FantasyPointRule) -> PointRuleSpec:
    """Map one ``fantasy_point_rule`` row to an engine rule spec."""
    condition = rule.condition or {}
    operator = condition.get("operator")
    if not operator:
        raise ValueError(f"point rule {rule.id} has no 'operator' in its condition JSON")
    return point_rule(
        metric=rule.event_type,
        operator=operator,
        points=rule.points,
        scope=rule.role_scope or "",
        unit=condition.get("unit"),
        min_value=condition.get("min_value"),
        max_value=condition.get("max_value"),
        # ``tiers`` is a JSON list of [min_value, points] pairs; the engine
        # factory normalizes it into ``TierSpec`` objects.
        tiers=cast(
            "Sequence[tuple[int, Decimal | int | str] | TierSpec]",
            condition.get("tiers") or (),
        ),
        tier_mode=condition.get("tier_mode"),
        min_balls=condition.get("min_balls"),
        min_overs=condition.get("min_overs"),
        cap_value=condition.get("cap_value"),
    )


_POLICY_FIELDS = (
    "super_over",
    "abandoned",
    "reduced_overs",
    "retirement_hurt",
    "substitutes",
)


def ruleset_spec_for(version: FantasyRulesetVersion) -> RulesetSpec:
    """The engine ``RulesetSpec`` for a stored version (rules + policies).

    Raises the engine's precise ``RulesetValidationError`` subclasses when the
    stored rules no longer form a valid ruleset (unknown metric, contradictory
    rules, malformed params) — the pipeline fails loudly rather than guessing.
    """
    defaults = RulesetPolicies()
    policy_values = {name: getattr(defaults, name) for name in _POLICY_FIELDS}
    policy_values.update(version.policies or {})
    spec = RulesetSpec(
        ruleset_id=str(version.ruleset_id),
        version=version.version,
        format_scope=version.format_scope,
        rules=tuple(_point_rule_from_db(rule) for rule in version.point_rules.order_by("id")),
        policies=RulesetPolicies(**policy_values),
    )
    validate_ruleset(spec)
    return spec


# --------------------------------------------------------------------------- #
# Locking + version selection
# --------------------------------------------------------------------------- #


def _lock_pair(key: str) -> tuple[int, int]:
    """Two signed-32-bit advisory-lock keys (see statistics recompute)."""
    digest = hashlib.sha256(key.encode("utf-8")).digest()

    def signed(b: bytes) -> int:
        value = int.from_bytes(b, "big")
        return value - (1 << 32) if value >= (1 << 31) else value

    return signed(digest[:4]), signed(digest[4:8])


def _take_advisory_lock(key: str) -> None:
    lock1, lock2 = _lock_pair(key)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s::int, %s::int)", [lock1, lock2])


def _versions_for(match: Match | None, *, version_id: Any = None) -> list[FantasyRulesetVersion]:
    """Versions to compute: the explicit one, or every active auto_recompute one."""
    if version_id is not None:
        return [FantasyRulesetVersion.objects.select_related("ruleset").get(pk=version_id)]
    return list(
        FantasyRulesetVersion.objects.filter(auto_recompute=True, ruleset__is_active=True)
        .select_related("ruleset")
        .order_by("ruleset_id", "version")
    )


# --------------------------------------------------------------------------- #
# One run
# --------------------------------------------------------------------------- #


def _write_audit(
    match: Match,
    version: FantasyRulesetVersion,
    snapshot: Any,
    result: FantasyResult,
    *,
    version_hash: str,
    trigger: str,
    provisional: bool,
    compute_ms: float,
) -> FantasyCalcAudit:
    """Append the calc-audit row (append-only; timings are frozen at write)."""
    return FantasyCalcAudit.objects.create(
        match_id=match.id,
        ruleset_version_id=version.id,
        input_digest=snapshot.digest,
        output_digest=result.digest,
        engine_version=settings.FANTASY_ENGINE_VERSION,
        version_hash=version_hash,
        notes=f"trigger={trigger}; players={len(result.players)}",
        snapshot=snapshot_payload(snapshot),
        snapshot_digest=snapshot.digest,
        trigger=trigger,
        is_provisional=provisional,
        timings={"compute_ms": compute_ms},
    )


def _upsert_performances(
    match: Match,
    version: FantasyRulesetVersion,
    snapshot: Any,
    result: FantasyResult,
    audit: FantasyCalcAudit,
) -> None:
    """Refresh the derived read model (bumped revision; stale rows removed).

    ``total_points`` = engine total + the net manual adjustment for the
    (match, player) pair (Task 5): the adjustment component is a distinct
    ``breakdown`` entry and survives recomputes untouched.
    """
    now = timezone.now()
    playing_xi = {line.player_id: line.is_playing_xi for line in snapshot.player_lines}
    adjustment_totals = adjustment_totals_by_player(match.id)
    for player in result.players:
        lookup: dict[str, Any] = {
            "match_id": match.id,
            "player_id": UUID(player.player_id),
            "ruleset_version_id": version.id,
        }
        existing = PlayerMatchPerformance.objects.filter(**lookup).first()
        adj_sum = adjustment_totals.get(player.player_id, Decimal("0.00"))
        breakdown = {c.metric: str(c.points) for c in player.components}
        if adj_sum:
            breakdown["adjustments"] = str(adj_sum)
        PlayerMatchPerformance.objects.update_or_create(
            **lookup,
            defaults={
                "total_points": player.total + adj_sum,
                "breakdown": breakdown,
                "is_playing_xi": playing_xi.get(player.player_id, True),
                "revision": (existing.revision + 1) if existing else 1,
                "computed_at": now,
                "calc_run": audit,
                "unsupported_rules": sorted(result.unsupported_rules),
                "is_partial": result.is_partial,
            },
        )
    present = [UUID(player.player_id) for player in result.players]
    PlayerMatchPerformance.objects.filter(match_id=match.id, ruleset_version_id=version.id).exclude(
        player_id__in=present
    ).delete()


def _point_diff(previous: dict[str, Decimal], result: FantasyResult) -> dict[str, Decimal]:
    new = {player.player_id: player.total for player in result.players}
    return {
        player_id: new.get(player_id, Decimal("0.00")) - previous.get(player_id, Decimal("0.00"))
        for player_id in set(new) | set(previous)
    }


def _noop_report(
    match: Match, version: FantasyRulesetVersion, snapshot: Any, *, trigger: str, provisional: bool
) -> FantasyCalcRunReport:
    return FantasyCalcRunReport(
        match_id=str(match.id),
        versions_computed=(str(version.id),),
        changed=False,
        snapshot_digest=snapshot.digest,
        trigger=trigger,
        provisional=provisional,
    )


def _compute_one(
    match: Match,
    version: FantasyRulesetVersion,
    snapshot: Any,
    *,
    provisional: bool,
    trigger: str,
) -> FantasyCalcRunReport:
    """Compute one (match, version) and persist audit + ledger + read model."""
    spec = ruleset_spec_for(version)
    version_hash = content_hash(spec)
    latest = ledger.latest_calc_run(match.id, version.id, provisional=provisional)
    if (
        latest is not None
        and latest.input_digest == snapshot.digest
        and latest.version_hash == version_hash
        and latest.engine_version == settings.FANTASY_ENGINE_VERSION
    ):
        return _noop_report(match, version, snapshot, trigger=trigger, provisional=provisional)

    started = time.perf_counter()
    result = compute(spec, snapshot)
    compute_ms = round((time.perf_counter() - started) * 1000, 3)
    previous = ledger.current_totals(match.id, version.id)

    audit = _write_audit(
        match,
        version,
        snapshot,
        result,
        version_hash=version_hash,
        trigger=trigger,
        provisional=provisional,
        compute_ms=compute_ms,
    )

    if not provisional:
        ledger.append_run(audit, result)
        _upsert_performances(match, version, snapshot, result, audit)

    _publish_computed(match.id, audit.id)

    return FantasyCalcRunReport(
        match_id=str(match.id),
        versions_computed=(str(version.id),),
        changed=True,
        calc_run_ids=(str(audit.id),),
        snapshot_digest=snapshot.digest,
        player_count=len(result.players),
        is_partial=result.is_partial,
        unsupported_rules=result.unsupported_rules,
        diff=None if provisional else _point_diff(previous, result),
        trigger=trigger,
        provisional=provisional,
    )


def _publish_computed(match_id: Any, calc_run_id: Any) -> None:
    from apps.core import events as core_events

    core_events.publish(
        core_events.TOPIC_FANTASY_COMPUTED,
        {"match_id": str(match_id), "calc_run_id": str(calc_run_id)},
    )


def _merge_reports(
    match: Match, reports: list[FantasyCalcRunReport], *, trigger: str, provisional: bool
) -> FantasyCalcRunReport:
    if not reports:
        return FantasyCalcRunReport(
            match_id=str(match.id), snapshot_digest="", trigger=trigger, provisional=provisional
        )
    return FantasyCalcRunReport(
        match_id=str(match.id),
        versions_computed=tuple(
            itertools.chain.from_iterable(r.versions_computed for r in reports)
        ),
        changed=any(r.changed for r in reports),
        calc_run_ids=tuple(itertools.chain.from_iterable(r.calc_run_ids for r in reports)),
        snapshot_digest=reports[0].snapshot_digest,
        player_count=max((r.player_count for r in reports), default=0),
        is_partial=any(r.is_partial for r in reports),
        unsupported_rules=tuple(
            sorted(set(itertools.chain.from_iterable(r.unsupported_rules for r in reports)))
        ),
        diff=next((r.diff for r in reports if r.diff is not None), None),
        trigger=trigger,
        provisional=provisional,
    )


def recompute_match(
    match: Match,
    *,
    version_id: Any = None,
    provisional: bool = False,
    trigger: str = "match.completed",
) -> FantasyCalcRunReport:
    """Recompute every applicable version for one match (idempotent).

    Runs under an advisory xact lock + ``SELECT ... FOR UPDATE`` on the match
    row. Returns a :class:`FantasyCalcRunReport`; a run whose inputs are
    unchanged is a no-op (``changed=False``).
    """
    if provisional and not settings.FANTASY_PROVISIONAL_ENABLED:
        raise ProvisionalDisabledError(
            "provisional recomputes are disabled (FANTASY_PROVISIONAL_ENABLED=false)"
        )
    with transaction.atomic():
        _take_advisory_lock(f"fantasy:{match.id}")
        locked = Match.objects.select_for_update().get(pk=match.id)
        snapshot = build_snapshot(locked)
        versions = _versions_for(locked, version_id=version_id)
        reports = [
            _compute_one(locked, version, snapshot, provisional=provisional, trigger=trigger)
            for version in versions
        ]
    return _merge_reports(locked, reports, trigger=trigger, provisional=provisional)


# --------------------------------------------------------------------------- #
# Sweep + backfill + verify
# --------------------------------------------------------------------------- #


def _matches_in_scope(
    scope: str, ids: list[str] | None = None, since: str | None = None
) -> list[Match]:
    qs = Match.objects.filter(status=MatchStatus.COMPLETED)
    if scope == "match":
        if not ids:
            raise ValueError("scope 'match' requires --ids")
        qs = qs.filter(id__in=ids)
    elif scope == "player":
        if not ids:
            raise ValueError("scope 'player' requires --ids")
        qs = qs.filter(player_stats__player_id__in=ids)
    elif scope == "series":
        if not ids:
            raise ValueError("scope 'series' requires --ids")
        qs = qs.filter(series_id__in=ids)
    elif scope != "all":
        raise ValueError(f"unknown scope: {scope!r}")
    if since:
        qs = qs.filter(source_local_date__gte=since)
    return list(qs.distinct().order_by("start_time_utc", "id"))


def sweep_stale_fantasy() -> int:
    """Recompute every completed match whose latest calc run is missing/stale.

    Staleness = the rebuilt snapshot digest or the version content hash no
    longer matches the latest audit (a source change that landed behind the
    pipeline's back, or a crashed run). Returns the number of runs healed.
    """
    versions = _versions_for(None)
    if not versions:
        return 0
    version_hashes = {version.id: content_hash(ruleset_spec_for(version)) for version in versions}
    matches = list(
        Match.objects.filter(status=MatchStatus.COMPLETED, player_stats__isnull=False)
        .distinct()
        .order_by("start_time_utc", "id")
    )
    healed = 0
    for match in matches:
        snapshot = build_snapshot(match)
        for version in versions:
            latest = ledger.latest_calc_run(match.id, version.id)
            if (
                latest is None
                or latest.input_digest != snapshot.digest
                or latest.version_hash != version_hashes[version.id]
                or latest.engine_version != settings.FANTASY_ENGINE_VERSION
            ):
                recompute_match(match, version_id=version.id, trigger="sweep")
                healed += 1
    return healed


def backfill_matches(
    scope: str = "all",
    *,
    dry_run: bool = False,
    ids: list[str] | None = None,
    since: str | None = None,
    version_id: Any = None,
) -> int:
    """Recompute every completed match in scope (dry-run reports only)."""
    matches = _matches_in_scope(scope, ids, since)
    count = 0
    for match in matches:
        count += 1
        if not dry_run:
            recompute_match(match, version_id=version_id, trigger="backfill")
    return count


def verify_fantasy(scope: str = "all", *, ids: list[str] | None = None) -> list[str]:
    """Audit the calc trail: every audit must reproduce from its stored
    snapshot, and every non-provisional run must agree with the current ledger.

    Returns a list of human-readable findings (empty = clean).
    """
    mismatches: list[str] = []
    for match in _matches_in_scope(scope, ids):
        audits = FantasyCalcAudit.objects.filter(match_id=match.id).order_by("-computed_at", "-id")
        for audit in audits:
            version = FantasyRulesetVersion.objects.filter(id=audit.ruleset_version_id).first()
            if version is None:
                mismatches.append(
                    f"match {match.id}: audit {audit.id} references missing "
                    f"version {audit.ruleset_version_id}"
                )
                continue
            try:
                spec = ruleset_spec_for(version)
            except Exception as exc:
                mismatches.append(f"match {match.id}: version {version.id} invalid: {exc}")
                continue
            if audit.version_hash and audit.version_hash != content_hash(spec):
                mismatches.append(
                    f"match {match.id}: version {version.id} changed since run {audit.id}"
                )
            if not audit.snapshot:
                continue  # provisional/no-snapshot runs have nothing to replay
            try:
                snapshot = snapshot_from_payload(bytes(audit.snapshot))
                result = compute(spec, snapshot)
            except Exception as exc:
                mismatches.append(f"match {match.id}: audit {audit.id} replay failed: {exc}")
                continue
            if snapshot.digest != audit.snapshot_digest:
                mismatches.append(f"match {match.id}: audit {audit.id} snapshot digest mismatch")
            if result.digest != audit.output_digest:
                mismatches.append(f"match {match.id}: audit {audit.id} output digest mismatch")
            if not audit.is_provisional:
                for player in result.players:
                    total = ledger.sum_current(match.id, player.player_id, version.id)
                    if total != player.total:
                        mismatches.append(
                            f"match {match.id}: ledger for player {player.player_id} = "
                            f"{total}, expected {player.total}"
                        )
    return mismatches


# --------------------------------------------------------------------------- #
# Event wiring
# --------------------------------------------------------------------------- #


def _recompute_from_event(payload: Any, *, correction: bool) -> None:
    match_id = payload.get("match_id")
    if not match_id:
        return
    match = Match.objects.filter(id=match_id).first()
    if match is None or match.status != MatchStatus.COMPLETED:
        return
    recompute_match(match, trigger="correction.applied" if correction else "match.completed")


def _on_player_merged(payload: Any) -> None:
    """Recompute the winner for the union of both players' matches.

    Runs synchronously inside the merge transaction (after the statistics
    subscriber has re-pointed the source rows), so the rebuilt snapshots
    attribute the loser's lines to the winner. The loser's append-only ledger
    history survives; their read-model rows are removed.
    """
    winner_id = payload.get("canonical_player_id")
    loser_id = payload.get("merged_player_id")
    if not winner_id or not loser_id:
        return
    match_ids = set(
        PlayerMatchPerformance.objects.filter(player_id=loser_id).values_list("match_id", flat=True)
    )
    match_ids |= set(
        PlayerMatchPerformance.objects.filter(player_id=winner_id).values_list(
            "match_id", flat=True
        )
    )
    for match_id in sorted(match_ids, key=str):
        match = Match.objects.filter(id=match_id).first()
        if match is not None and match.status == MatchStatus.COMPLETED:
            recompute_match(match, trigger="merge")
    PlayerMatchPerformance.objects.filter(player_id=loser_id).delete()


def register() -> None:
    """Wire the Tier-2 fantasy pipeline to the match lifecycle + merge events."""
    from apps.core import events as core_events

    core_events.subscribe(
        TOPIC_MATCH_COMPLETED, lambda payload: _recompute_from_event(payload, correction=False)
    )
    core_events.subscribe(
        TOPIC_CORRECTION_APPLIED, lambda payload: _recompute_from_event(payload, correction=True)
    )
    core_events.subscribe(TOPIC_PLAYER_MERGED, _on_player_merged)


__all__ = [
    "FantasyCalcRunReport",
    "ProvisionalDisabledError",
    "backfill_matches",
    "recompute_match",
    "register",
    "ruleset_spec_for",
    "sweep_stale_fantasy",
    "verify_fantasy",
]
