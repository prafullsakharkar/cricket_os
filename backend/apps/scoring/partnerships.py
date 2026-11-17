"""Partnerships, fall of wicket and the merged retirement timeline.

Off-ball retirements (``innings_event``) and on-ball retirements (``delivery``)
are exposed through one accessor and never counted twice (TESTING_STRATEGY
§14.9). A retirement breaks a partnership without creating a fall-of-wicket row.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import replace

from apps.scoring.config import RuleConfig
from apps.scoring.errors import DataQualityError, RetirementSourceConflictError
from apps.scoring.laws.overs import overs_display
from apps.scoring.types import (
    DeliveryRecord,
    FallOfWicket,
    InningsContext,
    InningsEvent,
    InningsEventType,
    Partnership,
    RetirementRecord,
    RetirementSource,
    RetirementType,
)
from apps.scoring.validation import (
    ordered_deliveries,
    ordered_events,
    validate_anchor_sequences,
)

TimelineNode = DeliveryRecord | InningsEvent


def validate_single_retirement_source(
    deliveries: Iterable[DeliveryRecord], events: Iterable[InningsEvent]
) -> None:
    """Reject the same retirement being recorded on both a ball and an event."""

    on_ball = {
        (delivery.retired_player_id, delivery.retirement_type)
        for delivery in deliveries
        if delivery.retired_player_id is not None
    }
    off_ball = {(event.player_id, event.retirement_type) for event in events if event.is_retirement}
    overlap = on_ball & off_ball
    if overlap:
        player_id, kind = sorted(overlap, key=lambda item: (item[0] or "", item[1] or ""))[0]
        raise RetirementSourceConflictError(
            "retirement recorded on both delivery and innings_event",
            details={"player_id": player_id, "retirement_type": kind},
        )


def innings_retirements(
    deliveries: Sequence[DeliveryRecord],
    events: Sequence[InningsEvent],
    rule_version: str,
) -> tuple[RetirementRecord, ...]:
    """One merged, ordered record per retirement (on-ball or off-ball)."""

    validate_single_retirement_source(deliveries, events)
    resumed_players = {
        event.player_id
        for event in events
        if event.event_type is InningsEventType.RESUME and event.player_id is not None
    }
    records: list[RetirementRecord] = []
    for delivery in deliveries:
        if delivery.retired_player_id is None or delivery.retirement_type is None:
            continue
        records.append(
            RetirementRecord(
                rule_version=rule_version,
                player_id=delivery.retired_player_id,
                kind=delivery.retirement_type,
                anchor_delivery_seq=delivery.delivery_seq,
                source=RetirementSource.DELIVERY,
                resumed=delivery.retired_player_id in resumed_players,
            )
        )
    for event in events:
        if not event.is_retirement or event.player_id is None:
            continue
        kind = event.retirement_type
        assert kind is not None
        records.append(
            RetirementRecord(
                rule_version=rule_version,
                player_id=event.player_id,
                kind=kind,
                anchor_delivery_seq=event.anchor_delivery_seq,
                source=RetirementSource.EVENT,
                resumed=event.player_id in resumed_players,
            )
        )
    records.sort(
        key=lambda record: (
            record.anchor_delivery_seq is not None,
            -1 if record.anchor_delivery_seq is None else record.anchor_delivery_seq,
            record.player_id,
        )
    )
    return tuple(records)


def merged_timeline(
    deliveries: Sequence[DeliveryRecord], events: Sequence[InningsEvent]
) -> tuple[TimelineNode, ...]:
    """Interleave deliveries and events; an event follows its anchor delivery."""

    by_anchor: dict[int | None, list[InningsEvent]] = {}
    for event in events:
        by_anchor.setdefault(event.anchor_delivery_seq, []).append(event)

    nodes: list[TimelineNode] = list(by_anchor.get(None, ()))
    for delivery in deliveries:
        nodes.append(delivery)
        nodes.extend(by_anchor.get(delivery.delivery_seq, ()))
    return tuple(nodes)


def _pair(delivery: DeliveryRecord) -> tuple[str, str]:
    return (delivery.batter_id, delivery.non_striker_id)


def _same_pair(partnership: Partnership, pair: tuple[str, str]) -> bool:
    return {partnership.batter1_id, partnership.batter2_id} == set(pair)


def build_partnerships(
    context: InningsContext, config: RuleConfig
) -> tuple[tuple[Partnership, ...], tuple[FallOfWicket, ...]]:
    """Build partnerships and fall-of-wicket rows from the merged timeline.

    ``Partnership.wicket_number`` is a monotonically increasing partnership
    ordinal (0 = opening). It equals the number of wickets that have fallen
    except after an off-ball retirement, where a new partnership must open
    without a wicket (and therefore without a FOW row).
    """

    deliveries = ordered_deliveries(context.deliveries, innings_id=context.innings_id)
    events = ordered_events(context.events, innings_id=context.innings_id)
    validate_anchor_sequences(events, deliveries)
    timeline = merged_timeline(deliveries, events)

    partnerships: list[Partnership] = []
    fows: list[FallOfWicket] = []
    current: Partnership | None = None
    break_pending = False
    last_seq: int | None = None
    running_score = 0
    legal_so_far = 0

    for node in timeline:
        if isinstance(node, InningsEvent):
            if node.is_retirement:
                break_pending = current is not None
            elif (
                node.event_type is InningsEventType.RESUME
                and current is not None
                and node.player_id not in {current.batter1_id, current.batter2_id}
            ):
                break_pending = True
            continue

        delivery = node
        pair = _pair(delivery)
        if current is None:
            current = Partnership(
                rule_version=config.version,
                wicket_number=0,
                batter1_id=pair[0],
                batter2_id=pair[1],
                start_delivery_seq=delivery.delivery_seq,
            )
            break_pending = False
        elif not _same_pair(current, pair):
            if not break_pending:
                raise DataQualityError(
                    "batting pair changed without a wicket or retirement",
                    delivery_seq=delivery.delivery_seq,
                    innings_id=delivery.innings_id,
                )
            partnerships.append(replace(current, end_delivery_seq=last_seq, unbroken=False))
            current = Partnership(
                rule_version=config.version,
                wicket_number=len(partnerships),
                batter1_id=pair[0],
                batter2_id=pair[1],
                start_delivery_seq=delivery.delivery_seq,
            )
            break_pending = False

        if delivery.is_legal:
            legal_so_far += 1
        running_score += delivery.total_runs
        current = replace(
            current,
            runs=current.runs + delivery.total_runs,
            balls=current.balls + (1 if delivery.is_legal else 0),
            fours=current.fours + (1 if delivery.runs_off_bat == 4 else 0),
            sixes=current.sixes + (1 if delivery.runs_off_bat == 6 else 0),
        )

        if delivery.is_wicket and delivery.dismissed_player_id is not None:
            fows.append(
                FallOfWicket(
                    rule_version=config.version,
                    wicket_number=len(fows) + 1,
                    score=running_score,
                    over_display=overs_display(legal_so_far, config),
                    delivery_seq=delivery.delivery_seq,
                    dismissed_player_id=delivery.dismissed_player_id,
                )
            )
            break_pending = True
        if delivery.retired_player_id is not None:
            break_pending = True
        last_seq = delivery.delivery_seq

    if current is not None:
        partnerships.append(replace(current, end_delivery_seq=last_seq, unbroken=not break_pending))
    return tuple(partnerships), tuple(fows)


def retirement_kind(value: str) -> RetirementType:
    """Map a delivery/event retirement label to the enum, rejecting unknowns."""

    try:
        return RetirementType(value)
    except ValueError as exc:
        raise DataQualityError(f"unknown retirement kind: {value!r}") from exc
