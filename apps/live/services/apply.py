"""Live delivery write path (ROADMAP Phase D task 4).

``apply_batch`` is the single entry point for live provider data. In **one**
transaction, under the per-match advisory lock, it:

1. applies any lifecycle transition (via ``matches.services.state``);
2. appends/revises deliveries through the Phase C write path
   (``deliveries.services.write.append_or_revise``);
3. rebuilds the affected innings (Tier 1 only) from stored current deliveries;
4. appends tiny ``delivery.appended`` / ``delivery.corrected`` /
   ``innings.updated`` rows to the durable per-match event log;
5. stamps ``match.last_ingested_at`` (freshness) and, on commit, advances the
   checkpoint and publishes events.

Ordering is irrelevant: batches are sorted by ``delivery_seq`` and the rebuild
reads the stored current rows ordered by that key. Duplicates are no-ops.
A live delivery never touches a Tier-2 table (career/team/standings/fantasy);
statistics only rebuild on the post-commit ``match.completed`` /
``correction.applied`` events.
"""

from __future__ import annotations

import datetime
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.core import metrics
from apps.core.enums import InningsStatus
from apps.core.errors import Unprocessable
from apps.deliveries.services.validation import DeliveryDraft
from apps.deliveries.services.write import append_or_revise
from apps.innings.models import Innings
from apps.live.services import verify as shadow
from apps.matches import event_schemas
from apps.matches.models import Match, MatchTeam
from apps.matches.services import event_log
from apps.matches.services import state as lifecycle
from apps.scorecards.services.rebuild import rebuild_innings
from apps.synchronization.services.sync import advance_checkpoint


@dataclass(frozen=True)
class LiveInningsBatch:
    """Deliveries for one innings in a live batch."""

    innings_number: int
    drafts: tuple[DeliveryDraft, ...]
    is_super_over: bool = False
    super_over_number: int | None = None
    batting_team_id: Any = None
    bowling_team_id: Any = None
    target: int | None = None
    target_balls: int | None = None
    dls_target: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "drafts", tuple(self.drafts))


@dataclass(frozen=True)
class LiveState:
    """A lifecycle change carried by a live payload."""

    status: str
    state_title: str | None = None
    status_detail: str | None = None
    extra_fields: dict[str, Any] = field(default_factory=dict)
    reopen: bool = False


@dataclass(frozen=True)
class ApplyReport:
    """What one live batch did (post-commit publication has been scheduled)."""

    match_id: str
    appended: int
    corrected: int
    noop: int
    innings: tuple[str, ...]
    changed: bool
    digests: dict[str, str]
    state_changed: bool
    checkpoint_seq: int | None = None


def _team_ids(match: Match) -> list[Any]:
    ids = list(
        MatchTeam.objects.filter(match=match)
        .order_by("team_number")
        .values_list("team_id", flat=True)
    )
    if len(ids) != 2:
        raise Unprocessable(
            f"match {match.id} must have exactly two teams before live ingestion",
            details={"match_id": str(match.id)},
        )
    return ids


def _resolve_teams(match: Match, batch: LiveInningsBatch, team_ids: list[Any]) -> tuple[Any, Any]:
    if batch.batting_team_id is not None and batch.bowling_team_id is not None:
        return batch.batting_team_id, batch.bowling_team_id
    if batch.is_super_over:
        return team_ids[0], team_ids[1]
    if batch.innings_number % 2 == 1:
        return team_ids[0], team_ids[1]
    return team_ids[1], team_ids[0]


def _get_or_create_innings(match: Match, batch: LiveInningsBatch) -> Innings:
    team_ids = _team_ids(match)
    batting_id, bowling_id = _resolve_teams(match, batch, team_ids)
    innings, _ = Innings.objects.get_or_create(
        match=match,
        innings_number=batch.innings_number,
        is_super_over=batch.is_super_over,
        super_over_number=batch.super_over_number,
        defaults={
            "batting_team_id": batting_id,
            "bowling_team_id": bowling_id,
            "status": InningsStatus.IN_PROGRESS,
            "target": batch.target,
            "target_balls": batch.target_balls,
            "dls_target": batch.dls_target,
        },
    )
    started = False
    if innings.status == InningsStatus.NOT_STARTED:
        innings.status = InningsStatus.IN_PROGRESS
        started = True
    if batch.target is not None and innings.target != batch.target:
        innings.target = batch.target
        started = True
    if batch.target_balls is not None and innings.target_balls != batch.target_balls:
        innings.target_balls = batch.target_balls
        started = True
    if batch.dls_target is not None and innings.dls_target != batch.dls_target:
        innings.dls_target = batch.dls_target
        started = True
    if started:
        innings.save(update_fields=["status", "target", "target_balls", "dls_target"])
    return innings


def _as_batches(records: Iterable[Any]) -> list[LiveInningsBatch]:
    batches = list(records)
    if batches and isinstance(batches[0], DeliveryDraft):
        raise Unprocessable(
            "apply_batch expects LiveInningsBatch records (a DeliveryDraft alone "
            "does not identify its innings)"
        )
    return [record for record in batches if isinstance(record, LiveInningsBatch)]


def apply_batch(
    match: Match,
    records: Iterable[LiveInningsBatch],
    *,
    state: LiveState | None = None,
    source: Any = None,
    provider: Any = None,
    checkpoint_scope: str | None = None,
    ingested_at: datetime.datetime | None = None,
    verify_every: int | None = None,
) -> ApplyReport:
    """Apply one provider batch atomically (idempotent, order-independent)."""
    batches = _as_batches(records)
    observed = ingested_at or timezone.now()
    every = (
        verify_every
        if verify_every is not None
        else int(getattr(settings, "LIVE_SHADOW_VERIFY_EVERY", 10))
    )
    appended = corrected = noop = 0
    innings_ids: list[str] = []
    digests: dict[str, str] = {}
    changed = False
    state_changed = False
    checkpoint_seq: int | None = None

    with transaction.atomic():
        event_log.lock_match(match.id)
        match.refresh_from_db()

        if state is not None:
            transition = lifecycle.transition(
                match,
                state.status,
                state_title=state.state_title,
                status_detail=state.status_detail,
                extra_fields=dict(state.extra_fields),
                reopen=state.reopen,
                source=source,
            )
            state_changed = transition.changed
            changed = changed or transition.changed
            match.refresh_from_db()

        for batch in batches:
            innings = _get_or_create_innings(match, batch)
            innings_ids.append(str(innings.id))
            report = append_or_revise(
                innings,
                batch.drafts,
                source=source,
                suppress_events=True,
            )
            appended += len(report["appended"])
            corrected += len(report["corrected"])
            noop += len(report["noop"])
            if report["appended"]:
                changed = True
                event_log.append_event(
                    match.id,
                    event_schemas.DELIVERY_APPENDED,
                    {
                        "innings_id": str(innings.id),
                        "delivery_seqs": report["appended"],
                        "revision": max(report["revisions"].values(), default=1),
                    },
                )
            if report["corrected"]:
                changed = True
                event_log.append_event(
                    match.id,
                    event_schemas.DELIVERY_CORRECTED,
                    {
                        "innings_id": str(innings.id),
                        "delivery_seqs": report["corrected"],
                        "revision": max(report["revisions"].values(), default=1),
                    },
                )

            rebuild = rebuild_innings(innings, source=source)
            digests[str(innings.id)] = rebuild.digest
            if rebuild.changed:
                changed = True
                summary = innings.summary
                event_log.append_event(
                    match.id,
                    event_schemas.INNINGS_UPDATED,
                    {
                        "innings_id": str(innings.id),
                        "runs": summary.runs if summary else 0,
                        "wickets": summary.wickets if summary else 0,
                        "overs_display": summary.overs_display if summary else None,
                        "digest": rebuild.digest,
                    },
                )
            if shadow.should_verify(match.id, every=every):
                shadow.verify(innings)

            if report["appended"] or report["corrected"]:
                seqs = [d.delivery_seq for d in batch.drafts]
                if seqs:
                    candidate = max(seqs)
                    checkpoint_seq = max(checkpoint_seq or 0, candidate)

        # Freshness is only re-stamped when a poll actually changed something;
        # an empty poll must not mask provider lag (``meta.stale``).
        updates: dict[str, Any] = {"ingested_at": observed, "provider_updated_at": observed}
        if changed:
            updates["last_ingested_at"] = observed
        Match.objects.filter(id=match.id).update(**updates)
        metrics.DUPLICATE_SUPPRESSED.inc(noop)
        if provider is not None and checkpoint_scope:
            transaction.on_commit(
                lambda: advance_checkpoint(
                    provider,
                    checkpoint_scope,
                    last_delivery_seq=checkpoint_seq,
                    last_provider_updated_at=observed,
                )
            )

    return ApplyReport(
        match_id=str(match.id),
        appended=appended,
        corrected=corrected,
        noop=noop,
        innings=tuple(innings_ids),
        changed=changed,
        digests=digests,
        state_changed=state_changed,
        checkpoint_seq=checkpoint_seq,
    )


__all__ = ["ApplyReport", "LiveInningsBatch", "LiveState", "apply_batch"]
