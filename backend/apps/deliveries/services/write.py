"""Idempotent delivery write path.

:func:`append_or_revise` is the only way canonical ``delivery`` rows enter the
system. It is:

* **order-independent** — batches are sorted by ``delivery_seq`` before
  validation and application, so a shuffled file or shuffled deliveries inside
  a file produce the same final state;
* **idempotent** — re-importing the same ``(match_date, innings_id,
  delivery_seq, revision)`` is a no-op; content that changed is superseded
  (``is_current=false`` + ``superseded_at``) and a new row with the next
  revision is inserted, all in one transaction (the partial unique
  ``ux_delivery_current`` guarantees exactly one current row per ball);
* **partition-safe** — ``match_date`` is derived from the match START date
  (never trusted from the caller) and ``ensure_partition`` is called before
  inserts so the yearly partition exists;
* **validated at the boundary** — :func:`validate_drafts` (DATA_INGESTION §6)
  raises typed :class:`DeliveryValidationError`s that the pipeline turns into
  DLQ rows; nothing is coerced.

Bulk fast path: when the innings has no existing rows, every draft is inserted
with a single batched ``bulk_create`` (no per-row round trips; on the order of
10k+ deliveries/second locally). The careful slow path handles corrections one
ball at a time. ``suppress_events=True`` is used for bulk historical loads
where the ``delivery.appended`` fan-out would rebuild the same innings dozens
of times; the pipeline calls ``rebuild_innings`` once afterwards instead.

Cross-module note: supersedes write a ``DataCorrection`` audit row through
``synchronization.services.corrections`` so the audit row commits in the same
transaction (documented in Phase C decisions).
"""

from __future__ import annotations

import datetime
from collections.abc import Iterable
from datetime import date
from typing import Any
from uuid import UUID, uuid4

from django.db import transaction
from django.utils import timezone

from apps.core import events as core_events
from apps.deliveries.models import Delivery
from apps.deliveries.services import partitions
from apps.deliveries.services.validation import DeliveryDraft, validate_drafts
from apps.synchronization.services.corrections import record_correction


def match_date_for(innings: Any) -> date:
    """The denormalised partition key: the match START date.

    ``delivery.match_date`` is always the match's start date (DATABASE_SCHEMA
    §3.6 note). It is derived here from ``innings.match.start_time_utc`` and
    never accepted from the caller, so partition pruning is consistent.
    """
    start = innings.match.start_time_utc
    return (
        start.date()
        if isinstance(start, datetime.datetime)
        else date(start.year, start.month, start.day)
    )


def _build_delivery(
    draft: DeliveryDraft,
    *,
    match_date: date,
    innings: Any,
    source: Any,
    revision: int,
) -> Delivery:
    return Delivery(
        id=uuid4(),
        match_date=match_date,
        match_id=innings.match_id,
        innings_id=innings.id,
        over_number=draft.over_number,
        ball_in_over=draft.ball_in_over,
        delivery_seq=draft.delivery_seq,
        bowler_id=draft.bowler_id,
        batter_id=draft.batter_id,
        non_striker_id=draft.non_striker_id,
        runs_off_bat=draft.runs_off_bat,
        wides=draft.wides,
        noballs=draft.noballs,
        byes=draft.byes,
        leg_byes=draft.leg_byes,
        penalty_runs=draft.penalty_runs,
        is_wicket=draft.is_wicket,
        dismissal_type=draft.dismissal_type,
        dismissed_player_id=draft.dismissed_player_id,
        bowler_credited_wicket=draft.bowler_credited_wicket,
        fielder1_id=draft.fielder1_id,
        fielder2_id=draft.fielder2_id,
        fielder3_id=draft.fielder3_id,
        shot_type=draft.shot_type,
        commentary_text=draft.commentary_text,
        provider_delivery_id=draft.provider_delivery_id,
        source_provider_id=getattr(source, "id", None),
        revision=revision,
        is_current=True,
        superseded_at=None,
        retired_player_id=draft.retired_player_id,
        retirement_type=draft.retirement_type,
    )


_CURRENT_FIELDS = [
    "id",
    "delivery_seq",
    "over_number",
    "ball_in_over",
    "bowler_id",
    "batter_id",
    "non_striker_id",
    "runs_off_bat",
    "wides",
    "noballs",
    "byes",
    "leg_byes",
    "penalty_runs",
    "is_wicket",
    "dismissal_type",
    "dismissed_player_id",
    "bowler_credited_wicket",
    "fielder1_id",
    "fielder2_id",
    "fielder3_id",
    "shot_type",
    "commentary_text",
    "provider_delivery_id",
    "retired_player_id",
    "retirement_type",
    "revision",
]


def _current_rows(innings: Any, match_date: date) -> dict[int, dict[str, Any]]:
    """Map delivery_seq -> stored columns for the current rows of an innings."""
    rows = Delivery.objects.filter(
        innings_id=innings.id, is_current=True, match_date=match_date
    ).values(*_CURRENT_FIELDS)
    return {row["delivery_seq"]: row for row in rows}


def _row_matches_draft(row: dict[str, Any], draft: DeliveryDraft) -> bool:
    """True when the stored current row equals the draft's content.

    ``as_content_dict`` normalises ids to strings; the DB returns UUID objects,
    so string values are stringified before comparison. ``None`` only ever
    equals ``None`` (never an empty/absent value). Numeric fields compare by
    value — ``bool()`` coercion would wrongly treat 2 == 4 as a match.
    """
    for key, expected in draft.as_content_dict().items():
        actual = row[key]
        if isinstance(expected, str) and actual is not None:
            actual = str(actual)
        if expected is None or actual is None:
            if expected is not None or actual is not None:
                return False
        elif actual != expected:
            return False
    return True


def append_or_revise(
    innings: Any,
    records: Iterable[DeliveryDraft],
    *,
    source: Any = None,
    suppress_events: bool = False,
) -> dict[str, Any]:
    """Append or revise delivery rows for ``innings``.

    Args:
        innings: the ``Innings`` row the deliveries belong to.
        records: canonical drafts (validated, order-independent).
        source: the data provider (``DataProvider`` or None) — its id is stored
            on every row and used as the correction's provider link.
        suppress_events: when True no ``delivery.appended``/``delivery.corrected``
            events are published (bulk historical loads rebuild once instead).

    Returns a report dict: ``{"match_date", "appended": [seq...],
    "corrected": [seq...], "noop": [seq...], "revisions": {seq: revision}}``.
    """
    drafts = sorted(records, key=lambda d: d.delivery_seq)
    validate_drafts(drafts)
    match_date = match_date_for(innings)
    partitions.ensure_partition(match_date.year)

    appended: list[int] = []
    corrected: list[int] = []
    noop: list[int] = []
    revisions: dict[int, int] = {}

    with transaction.atomic():
        existing = _current_rows(innings, match_date)
        if not existing:
            rows = [
                _build_delivery(
                    d, match_date=match_date, innings=innings, source=source, revision=d.revision
                )
                for d in drafts
            ]
            Delivery.objects.bulk_create(rows, batch_size=500)
            appended = [d.delivery_seq for d in drafts]
            revisions = {d.delivery_seq: d.revision for d in drafts}
        else:
            for draft in drafts:
                cur = existing.get(draft.delivery_seq)
                if cur is None:
                    _build_delivery(
                        draft,
                        match_date=match_date,
                        innings=innings,
                        source=source,
                        revision=draft.revision,
                    ).save()
                    appended.append(draft.delivery_seq)
                    revisions[draft.delivery_seq] = draft.revision
                elif draft.revision < cur["revision"]:
                    # Stale revision for a ball that has since moved on: no-op.
                    noop.append(draft.delivery_seq)
                elif draft.revision == cur["revision"] and _row_matches_draft(cur, draft):
                    # Exact replay of the current revision: idempotent no-op.
                    noop.append(draft.delivery_seq)
                else:
                    new_revision = max(cur["revision"] + 1, draft.revision)
                    Delivery.objects.filter(
                        id=cur["id"],
                        match_date=match_date,
                        innings_id=innings.id,
                        delivery_seq=draft.delivery_seq,
                        is_current=True,
                    ).update(is_current=False, superseded_at=timezone.now())
                    _build_delivery(
                        draft,
                        match_date=match_date,
                        innings=innings,
                        source=source,
                        revision=new_revision,
                    ).save()
                    record_correction(
                        provider=source,
                        entity_type="delivery",
                        entity_id=cur["id"],
                        provider_entity_id=draft.provider_delivery_id,
                        old_value={k: _as_json(v) for k, v in cur.items() if k != "id"},
                        new_value=draft.as_content_dict(),
                        reason="provider content revision",
                        source_revision=cur["revision"],
                        target_revision=new_revision,
                    )
                    corrected.append(draft.delivery_seq)
                    revisions[draft.delivery_seq] = new_revision

    report = {
        "match_date": match_date,
        "appended": appended,
        "corrected": corrected,
        "noop": noop,
        "revisions": revisions,
    }

    if not suppress_events:
        payload = {
            "match_id": str(innings.match_id),
            "innings_id": str(innings.id),
            "match_date": match_date.isoformat(),
            "source": getattr(source, "code", None),
            "appended": appended,
            "corrected": corrected,
        }
        if appended:
            core_events.publish(core_events.TOPIC_DELIVERY_APPENDED, payload)
        if corrected:
            core_events.publish(core_events.TOPIC_DELIVERY_CORRECTED, payload)

    return report


def _as_json(value: Any) -> Any:
    """JSON-safe value for the correction audit row."""
    if isinstance(value, (bytes, UUID)):
        return str(value)
    if isinstance(value, (date, datetime.datetime, datetime.time)):
        return value.isoformat()
    return value
