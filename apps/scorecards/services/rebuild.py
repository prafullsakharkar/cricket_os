"""Engine-driven rebuild of derived scorecard rows (Tier-1).

``rebuild_innings`` / ``rebuild_match`` are the ONLY writers of the derived
read models — ``batting_entry``, ``bowling_entry``, ``fall_of_wicket``,
``partnership``, ``over``, ``innings_summary`` and ``match_result`` (plus the
denormalised result columns on ``match``). They follow SCORING_ENGINE_API §7:

1. current ``delivery`` rows (``is_current`` only) -> ``DeliveryRecord``;
   current ``innings_event`` rows -> engine ``InningsEvent``;
   ``innings`` metadata -> ``InningsContext``; ``match`` -> ``MatchContext``;
2. ``engine.rebuild_innings`` / ``engine.rebuild_match`` produces the outputs
   whose field names align column-for-column with the derived tables;
3. the outputs are persisted in ONE transaction, and every affected row is
   revisioned with the new innings revision.

Determinism: each rebuild computes a canonical digest over the derived state.
If the digest of the new engine output equals the digest of the currently
stored rows, the rebuild is a no-op (no revision bump, no events). Two rebuilds
of unchanged data always yield the same digest, and a correction that changes
ball content always yields a different digest (tests pin both).

Tier isolation: this module NEVER writes ``player_match_stat`` (Tier-2). The
statistics module subscribes to ``match.completed`` / ``correction.applied``
and builds stats idempotently.

Summary-only innings (``innings_summary.batch_complete = False``) carry no
``delivery`` rows; their derived rows come from the provider summary, so the
engine is skipped and their rows are left untouched (the summary-only adapter
writes them directly).
"""

from __future__ import annotations

import datetime
import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from apps.core import events as core_events
from apps.core.enums import MatchStatus as DbMatchStatus
from apps.deliveries.models import Delivery, Over
from apps.innings.models import Innings, InningsEvent, InningsSummary
from apps.matches.models import Match, MatchResult, MatchTeam
from apps.scorecards.models import BattingEntry, BowlingEntry, FallOfWicket, Partnership
from apps.scoring import engine as scoring_engine
from apps.scoring.config import preset_for_format
from apps.scoring.types import (
    DeliveryRecord,
    DismissalType,
    Format,
    InningsContext,
    InningsEventType,
    InningsResult,
    MatchContext,
    MatchScorecard,
    RetirementType,
)
from apps.scoring.types import (
    InningsEvent as EngineInningsEvent,
)
from apps.scoring.types import (
    InningsStatus as EngineInningsStatus,
)
from apps.scoring.types import (
    MatchResult as EngineMatchResult,
)
from apps.scoring.types import (
    MatchStatus as EngineMatchStatus,
)

_RATE_DECIMAL = Decimal("0.001")

#: Match lifecycle states that mean "the match has reached a terminal decision".
_TERMINAL = {
    DbMatchStatus.COMPLETED,
    DbMatchStatus.ABANDONED,
    DbMatchStatus.CANCELLED,
    DbMatchStatus.FORFEITED,
}


@dataclass(frozen=True)
class RebuildReport:
    """What a rebuild did (or would do), with the determinism digest."""

    scope: str  # "innings" | "match"
    match_id: str | None
    innings_id: str | None
    changed: bool
    digest: str
    skipped_reason: str | None = None
    match_completed: bool = False


# --------------------------------------------------------------------------- #
# Canonicalisation / digest
# --------------------------------------------------------------------------- #


def _canon(value: Any) -> Any:
    """Normalise a value so engine output and stored rows hash identically."""
    if value is None:
        return None
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime.date, datetime.datetime, datetime.time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float):
        return _rate(value)
    if isinstance(value, (dict,)):
        return {k: _canon(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canon(v) for v in value]
    return value


def _rate(value: float | Decimal | None) -> str | None:
    """Canonical 3-decimal string for a rate (run rate, SR, economy, avg)."""
    if value is None:
        return None
    return f"{Decimal(str(value)):.3f}"


def digest(*states: Any) -> str:
    """SHA-256 over the canonical serialisation of the given states."""
    payload = json.dumps([_canon(s) for s in states], sort_keys=True, default=str).encode()
    return hashlib.sha256(payload).hexdigest()


# --------------------------------------------------------------------------- #
# Engine context builders
# --------------------------------------------------------------------------- #


def _format_for(match: Match) -> Format:
    return Format(match.format_type)


def _match_context(match: Match) -> MatchContext:
    teams = tuple(
        str(team_id)
        for team_id in MatchTeam.objects.filter(match=match)
        .order_by("team_number")
        .values_list("team_id", flat=True)
    )
    if len(teams) != 2:
        raise ValueError(f"match {match.id} must have exactly two teams for a rebuild")
    return MatchContext(
        match_id=str(match.id),
        format=_format_for(match),
        status=EngineMatchStatus(match.status),
        teams=teams,
        overs_limit=match.overs_limit,
        balls_per_over=match.balls_per_over,
        is_follow_on=match.is_follow_on,
        is_dls_applied=match.is_dls_applied,
        time_expired=False,
        result_type=None,
    )


def _delivery_records(innings: Innings) -> tuple[DeliveryRecord, ...]:
    fields = [
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
        "retired_player_id",
        "retirement_type",
        "revision",
    ]
    rows = (
        Delivery.objects.filter(
            innings_id=innings.id, is_current=True, match_date=innings.match.start_time_utc.date()
        )
        .order_by("delivery_seq")
        .values(*fields)
    )
    records = []
    for row in rows:
        records.append(
            DeliveryRecord(
                innings_id=str(innings.id),
                delivery_seq=row["delivery_seq"],
                over_number=row["over_number"],
                ball_in_over=row["ball_in_over"],
                bowler_id=str(row["bowler_id"]),
                batter_id=str(row["batter_id"]),
                non_striker_id=str(row["non_striker_id"]),
                runs_off_bat=row["runs_off_bat"],
                wides=row["wides"],
                noballs=row["noballs"],
                byes=row["byes"],
                leg_byes=row["leg_byes"],
                penalty_runs=row["penalty_runs"],
                is_wicket=row["is_wicket"],
                dismissal_type=(
                    DismissalType(row["dismissal_type"]) if row["dismissal_type"] else None
                ),
                dismissed_player_id=str(row["dismissed_player_id"])
                if row["dismissed_player_id"]
                else None,
                bowler_credited_wicket=row["bowler_credited_wicket"],
                fielder1_id=str(row["fielder1_id"]) if row["fielder1_id"] else None,
                fielder2_id=str(row["fielder2_id"]) if row["fielder2_id"] else None,
                fielder3_id=str(row["fielder3_id"]) if row["fielder3_id"] else None,
                retired_player_id=str(row["retired_player_id"])
                if row["retired_player_id"]
                else None,
                retirement_type=(
                    RetirementType(row["retirement_type"]) if row["retirement_type"] else None
                ),
                revision=row["revision"],
                is_current=True,
            )
        )
    return tuple(records)


def _event_records(innings: Innings) -> tuple[EngineInningsEvent, ...]:
    rows = (
        InningsEvent.objects.filter(innings_id=innings.id, is_current=True)
        .order_by("anchor_delivery_seq")
        .values("event_type", "player_id", "anchor_delivery_seq", "over_number", "revision")
    )
    return tuple(
        EngineInningsEvent(
            innings_id=str(innings.id),
            event_type=InningsEventType(row["event_type"]),
            player_id=str(row["player_id"]) if row["player_id"] else None,
            anchor_delivery_seq=row["anchor_delivery_seq"],
            over_number=row["over_number"],
            revision=row["revision"],
            is_current=True,
        )
        for row in rows
    )


def _is_summary_only(innings: Innings) -> bool:
    summary = InningsSummary.objects.filter(innings_id=innings.id).values("batch_complete").first()
    return bool(summary and not summary["batch_complete"])


def build_innings_context(innings: Innings) -> InningsContext | None:
    """The engine context for one innings, or None for summary-only innings."""
    if _is_summary_only(innings):
        return None
    return InningsContext(
        innings_id=str(innings.id),
        innings_number=innings.innings_number,
        batting_team_id=str(innings.batting_team_id),
        bowling_team_id=str(innings.bowling_team_id),
        is_super_over=innings.is_super_over,
        super_over_number=innings.super_over_number,
        declared=innings.declared,
        forfeited=innings.forfeited,
        all_out=innings.all_out,
        target=innings.target,
        target_balls=innings.target_balls,
        dls_target=innings.dls_target,
        is_follow_on_innings=innings.is_follow_on_innings,
        status=EngineInningsStatus(innings.status),
        deliveries=_delivery_records(innings),
        events=_event_records(innings),
    )


def compute_scorecard(match: Match) -> MatchScorecard:
    """Engine ``MatchScorecard`` for a match without persisting anything.

    Summary-only innings are skipped entirely: their provider-written rows are
    authoritative and there is nothing to derive from ``delivery`` rows.
    """
    innings = list(
        match.innings.all().order_by("innings_number", "is_super_over", "super_over_number")
    )
    contexts = [ctx for ctx in (build_innings_context(i) for i in innings) if ctx is not None]
    config = preset_for_format(_format_for(match))
    return scoring_engine.rebuild_match(_match_context(match), contexts, config)


# --------------------------------------------------------------------------- #
# Derived-state serialisation (engine output vs stored rows)
# --------------------------------------------------------------------------- #


def _state_from_result(innings: Innings, result: InningsResult) -> dict[str, Any]:
    totals = result.totals
    return {
        "status": result.status.value,
        "summary": {
            "runs": totals.runs,
            "wickets": totals.wickets,
            "legal_balls": totals.legal_balls,
            "overs_display": totals.overs_display,
            "run_rate": _rate(totals.run_rate),
            "extras": {
                "wides": totals.extras.wides,
                "noballs": totals.extras.noballs,
                "byes": totals.extras.byes,
                "leg_byes": totals.extras.leg_byes,
                "penalty": totals.extras.penalty,
                "total": totals.extras.total,
            },
        },
        "batting": sorted(
            (
                {
                    "player_id": line.player_id,
                    "runs": line.runs,
                    "balls": line.balls,
                    "dots": line.dots,
                    "fours": line.fours,
                    "sixes": line.sixes,
                    "ones": line.ones,
                    "twos": line.twos,
                    "threes": line.threes,
                    "fives": line.fives,
                    "is_out": line.out,
                    "dismissal_type": line.dismissal_type.value if line.dismissal_type else None,
                    "bowler_id": line.bowler_id,
                    "fielder1_id": line.fielder1_id,
                    "fielder2_id": line.fielder2_id,
                    "fielder3_id": line.fielder3_id,
                    "strike_rate": _rate(line.strike_rate),
                }
                for line in result.batting
            ),
            key=lambda row: row["player_id"],
        ),
        "bowling": sorted(
            (
                {
                    "player_id": line.player_id,
                    "legal_balls": line.legal_balls,
                    "runs_conceded": line.runs_conceded,
                    "wickets": line.wickets,
                    "maidens": line.maidens,
                    "wides": line.wides,
                    "noballs": line.noballs,
                    "dots": line.dots,
                    "overs_display": line.overs_display,
                    "economy": _rate(line.economy),
                    "bowling_avg": _rate(line.bowling_avg),
                    "strike_rate": _rate(line.strike_rate),
                }
                for line in result.bowling
            ),
            key=lambda row: row["player_id"],
        ),
        "fow": sorted(
            (
                {
                    "wicket_number": row.wicket_number,
                    "score": row.score,
                    "over_display": row.over_display,
                    "delivery_seq": row.delivery_seq,
                    "dismissed_player_id": row.dismissed_player_id,
                }
                for row in result.fall_of_wickets
            ),
            key=lambda row: row["wicket_number"],
        ),
        "partnerships": sorted(
            (
                {
                    "wicket_number": row.wicket_number,
                    "batter1_id": row.batter1_id,
                    "batter2_id": row.batter2_id,
                    "runs": row.runs,
                    "balls": row.balls,
                    "fours": row.fours,
                    "sixes": row.sixes,
                    "start_delivery_seq": row.start_delivery_seq,
                    "end_delivery_seq": row.end_delivery_seq,
                    "unbroken": row.unbroken,
                }
                for row in result.partnerships
            ),
            key=lambda row: row["wicket_number"],
        ),
        "overs": sorted(
            (
                {
                    "over_number": row.over_number,
                    "bowler_id": row.bowler_id,
                    "runs_conceded": row.runs_conceded,
                    "wickets": row.wickets,
                    "is_maiden": row.is_maiden,
                    "is_complete": row.is_complete,
                    "deliveries_count": row.deliveries_count,
                }
                for row in result.overs
            ),
            key=lambda row: row["over_number"],
        ),
    }


def _state_from_db(innings: Innings) -> dict[str, Any]:
    summary = (
        InningsSummary.objects.filter(innings_id=innings.id)
        .values("runs", "wickets", "legal_balls", "overs_display", "run_rate", "extras")
        .first()
    )
    return {
        "status": innings.status,
        "summary": {
            "runs": summary["runs"] if summary else 0,
            "wickets": summary["wickets"] if summary else 0,
            "legal_balls": summary["legal_balls"] if summary else 0,
            "overs_display": summary["overs_display"] if summary else None,
            "run_rate": _rate(summary["run_rate"]) if summary else None,
            "extras": summary["extras"] if summary else {},
        },
        "batting": sorted(
            (
                {
                    "player_id": str(row["player_id"]),
                    "runs": row["runs"],
                    "balls": row["balls"],
                    "dots": row["dots"],
                    "fours": row["fours"],
                    "sixes": row["sixes"],
                    "ones": row["ones"],
                    "twos": row["twos"],
                    "threes": row["threes"],
                    "fives": row["fives"],
                    "is_out": row["is_out"],
                    "dismissal_type": row["dismissal_type"],
                    "bowler_id": str(row["bowler_id"]) if row["bowler_id"] else None,
                    "fielder1_id": str(row["fielder1_id"]) if row["fielder1_id"] else None,
                    "fielder2_id": str(row["fielder2_id"]) if row["fielder2_id"] else None,
                    "fielder3_id": str(row["fielder3_id"]) if row["fielder3_id"] else None,
                    "strike_rate": _rate(row["strike_rate"]),
                }
                for row in BattingEntry.objects.filter(innings_id=innings.id).values(
                    "player_id",
                    "runs",
                    "balls",
                    "dots",
                    "fours",
                    "sixes",
                    "ones",
                    "twos",
                    "threes",
                    "fives",
                    "is_out",
                    "dismissal_type",
                    "bowler_id",
                    "fielder1_id",
                    "fielder2_id",
                    "fielder3_id",
                    "strike_rate",
                )
            ),
            key=lambda row: row["player_id"],
        ),
        "bowling": sorted(
            (
                {
                    "player_id": str(row["player_id"]),
                    "legal_balls": row["legal_balls"],
                    "runs_conceded": row["runs_conceded"],
                    "wickets": row["wickets"],
                    "maidens": row["maidens"],
                    "wides": row["wides"],
                    "noballs": row["noballs"],
                    "dots": row["dots"],
                    "overs_display": row["overs_display"],
                    "economy": _rate(row["economy"]),
                    "bowling_avg": _rate(row["bowling_avg"]),
                    "strike_rate": _rate(row["strike_rate"]),
                }
                for row in BowlingEntry.objects.filter(innings_id=innings.id).values(
                    "player_id",
                    "legal_balls",
                    "runs_conceded",
                    "wickets",
                    "maidens",
                    "wides",
                    "noballs",
                    "dots",
                    "overs_display",
                    "economy",
                    "bowling_avg",
                    "strike_rate",
                )
            ),
            key=lambda row: row["player_id"],
        ),
        "fow": sorted(
            (
                {
                    "wicket_number": row["wicket_number"],
                    "score": row["score"],
                    "over_display": row["over_display"],
                    "delivery_seq": row["delivery_seq"],
                    "dismissed_player_id": str(row["dismissed_player_id"])
                    if row["dismissed_player_id"]
                    else None,
                }
                for row in FallOfWicket.objects.filter(innings_id=innings.id).values(
                    "wicket_number", "score", "over_display", "delivery_seq", "dismissed_player_id"
                )
            ),
            key=lambda row: row["wicket_number"],
        ),
        "partnerships": sorted(
            (
                {
                    "wicket_number": row["wicket_number"],
                    "batter1_id": str(row["batter1_id"]) if row["batter1_id"] else None,
                    "batter2_id": str(row["batter2_id"]) if row["batter2_id"] else None,
                    "runs": row["runs"],
                    "balls": row["balls"],
                    "fours": row["fours"],
                    "sixes": row["sixes"],
                    "start_delivery_seq": row["start_delivery_seq"],
                    "end_delivery_seq": row["end_delivery_seq"],
                    "unbroken": row["unbroken"],
                }
                for row in Partnership.objects.filter(innings_id=innings.id).values(
                    "wicket_number",
                    "batter1_id",
                    "batter2_id",
                    "runs",
                    "balls",
                    "fours",
                    "sixes",
                    "start_delivery_seq",
                    "end_delivery_seq",
                    "unbroken",
                )
            ),
            key=lambda row: row["wicket_number"],
        ),
        "overs": sorted(
            (
                {
                    "over_number": row["over_number"],
                    "bowler_id": str(row["bowler_id"]),
                    "runs_conceded": row["runs_conceded"],
                    "wickets": row["wickets"],
                    "is_maiden": row["is_maiden"],
                    "is_complete": row["is_complete"],
                    "deliveries_count": row["deliveries_count"],
                }
                for row in Over.objects.filter(innings_id=innings.id).values(
                    "over_number",
                    "bowler_id",
                    "runs_conceded",
                    "wickets",
                    "is_maiden",
                    "is_complete",
                    "deliveries_count",
                )
            ),
            key=lambda row: row["over_number"],
        ),
    }


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


def _player_meta(match: Match) -> dict[str, dict[str, Any]]:
    """player_id -> {batting_position, is_captain, is_keeper, is_overseas}."""
    meta: dict[str, dict[str, Any]] = {}
    for row in match.players.all().values(
        "player_id", "batting_position", "is_captain", "is_keeper", "is_overseas"
    ):
        meta[str(row["player_id"])] = {
            "batting_position": row["batting_position"],
            "is_captain": row["is_captain"],
            "is_keeper": row["is_keeper"],
            "is_overseas": row["is_overseas"],
        }
    return meta


def _persist_innings(innings: Innings, result: InningsResult, *, revision: int) -> None:
    """Replace the derived rows of one innings with the engine output."""
    meta = _player_meta(innings.match)
    batting_team_id = innings.batting_team_id
    bowling_team_id = innings.bowling_team_id

    BattingEntry.objects.filter(innings_id=innings.id).delete()
    BowlingEntry.objects.filter(innings_id=innings.id).delete()
    FallOfWicket.objects.filter(innings_id=innings.id).delete()
    Partnership.objects.filter(innings_id=innings.id).delete()
    Over.objects.filter(innings_id=innings.id).delete()

    batting_rows = []
    for line in result.batting:
        m = meta.get(line.player_id, {})
        batting_rows.append(
            BattingEntry(
                innings_id=innings.id,
                player_id=line.player_id,
                team_id=batting_team_id,
                position=m.get("batting_position"),
                is_captain=bool(m.get("is_captain", False)),
                is_keeper=bool(m.get("is_keeper", False)),
                runs=line.runs,
                balls=line.balls,
                dots=line.dots,
                fours=line.fours,
                sixes=line.sixes,
                ones=line.ones,
                twos=line.twos,
                threes=line.threes,
                fives=line.fives,
                strike_rate=_as_decimal(line.strike_rate),
                is_out=line.out,
                dismissal_type=line.dismissal_type.value if line.dismissal_type else None,
                bowler_id=line.bowler_id,
                fielder1_id=line.fielder1_id,
                fielder2_id=line.fielder2_id,
                fielder3_id=line.fielder3_id,
                is_overseas=bool(m.get("is_overseas", False)),
                revision=revision,
            )
        )
    BattingEntry.objects.bulk_create(batting_rows, batch_size=200)

    bowling_rows = [
        BowlingEntry(
            innings_id=innings.id,
            player_id=line.player_id,
            team_id=bowling_team_id,
            overs_display=line.overs_display,
            legal_balls=line.legal_balls,
            maidens=line.maidens,
            runs_conceded=line.runs_conceded,
            wickets=line.wickets,
            wides=line.wides,
            noballs=line.noballs,
            dots=line.dots,
            economy=_as_decimal(line.economy),
            bowling_avg=_as_decimal(line.bowling_avg),
            strike_rate=_as_decimal(line.strike_rate),
            revision=revision,
        )
        for line in result.bowling
    ]
    BowlingEntry.objects.bulk_create(bowling_rows, batch_size=200)

    fow_rows = [
        FallOfWicket(
            innings_id=innings.id,
            wicket_number=row.wicket_number,
            score=row.score,
            over_display=row.over_display,
            delivery_seq=row.delivery_seq,
            dismissed_player_id=row.dismissed_player_id,
        )
        for row in result.fall_of_wickets
    ]
    FallOfWicket.objects.bulk_create(fow_rows, batch_size=200)

    partnership_rows = [
        Partnership(
            innings_id=innings.id,
            wicket_number=row.wicket_number,
            batter1_id=row.batter1_id,
            batter2_id=row.batter2_id,
            runs=row.runs,
            balls=row.balls,
            fours=row.fours,
            sixes=row.sixes,
            start_delivery_seq=row.start_delivery_seq,
            end_delivery_seq=row.end_delivery_seq,
            unbroken=row.unbroken,
        )
        for row in result.partnerships
    ]
    Partnership.objects.bulk_create(partnership_rows, batch_size=200)

    over_rows = [
        Over(
            innings_id=innings.id,
            over_number=row.over_number,
            bowler_id=row.bowler_id,
            is_maiden=row.is_maiden,
            runs_conceded=row.runs_conceded,
            wickets=row.wickets,
            is_complete=row.is_complete,
            deliveries_count=row.deliveries_count,
        )
        for row in result.overs
    ]
    Over.objects.bulk_create(over_rows, batch_size=200)

    totals = result.totals
    InningsSummary.objects.update_or_create(
        innings_id=innings.id,
        defaults={
            "runs": totals.runs,
            "wickets": totals.wickets,
            "legal_balls": totals.legal_balls,
            "overs_display": totals.overs_display,
            "extras": {
                "wides": totals.extras.wides,
                "noballs": totals.extras.noballs,
                "byes": totals.extras.byes,
                "leg_byes": totals.extras.leg_byes,
                "penalty": totals.extras.penalty,
                "total": totals.extras.total,
            },
            "run_rate": _as_decimal(totals.run_rate),
            "batch_complete": True,
            "updated_at": timezone.now(),
        },
    )

    Innings.objects.filter(id=innings.id).update(
        status=result.status.value,
        declared=result.declared,
        all_out=result.all_out,
        revision=revision,
    )


def _as_decimal(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)).quantize(_RATE_DECIMAL)


def _match_result_state(match: Match, result: EngineMatchResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "result_type": result.result_type.value,
        "winner_team_id": result.winner_team_id,
        "margin_type": result.margin_type.value if result.margin_type else None,
        "margin_value": result.margin_value,
        "is_dls": result.is_dls,
        "is_super_over": result.is_super_over,
        "result_text": result.result_text,
    }


def _persist_match_result(
    match: Match, result: EngineMatchResult | None, *, revision: int
) -> dict[str, Any] | None:
    """Persist the result row + denormalised match columns; return the new state."""
    if result is None:
        return None
    state = _match_result_state(match, result)
    MatchResult.objects.update_or_create(
        match=match,
        defaults={
            "winner_team_id": result.winner_team_id,
            "result_type": result.result_type.value,
            "margin_type": result.margin_type.value if result.margin_type else None,
            "margin_value": result.margin_value,
            "result_text": result.result_text,
            "is_dls": result.is_dls,
            "is_super_over": result.is_super_over,
            "completed_at": timezone.now(),
        },
    )
    updates: dict[str, Any] = {
        "result_type": result.result_type.value,
        "result_text": result.result_text,
        "winner_team_id": result.winner_team_id,
        "revision": revision,
    }
    if match.status not in _TERMINAL:
        updates["status"] = DbMatchStatus.COMPLETED
    if match.end_time_utc is None:
        updates["end_time_utc"] = timezone.now()
    Match.objects.filter(id=match.id).update(**updates)
    return state


def _existing_result_state(match: Match) -> dict[str, Any] | None:
    row = (
        MatchResult.objects.filter(match_id=match.id)
        .values(
            "result_type",
            "winner_team_id",
            "margin_type",
            "margin_value",
            "is_dls",
            "is_super_over",
            "result_text",
        )
        .first()
    )
    if row is None:
        return None
    return {
        "result_type": row["result_type"],
        "winner_team_id": str(row["winner_team_id"]) if row["winner_team_id"] else None,
        "margin_type": row["margin_type"],
        "margin_value": row["margin_value"],
        "is_dls": row["is_dls"],
        "is_super_over": row["is_super_over"],
        "result_text": row["result_text"],
    }


# --------------------------------------------------------------------------- #
# Public entry points
# --------------------------------------------------------------------------- #


def rebuild_innings(
    innings: Innings, *, source: Any = None, estimated: bool = False
) -> RebuildReport:
    """Rebuild one innings' derived rows from its current deliveries/events."""
    match: Match = innings.match
    # The caller's instance may predate a completion/correction; the lifecycle
    # decisions (match.completed vs correction.applied) must read fresh state.
    match.refresh_from_db()
    if _is_summary_only(innings):
        return RebuildReport(
            scope="innings",
            match_id=str(match.id),
            innings_id=str(innings.id),
            changed=False,
            digest=digest(None),
            skipped_reason="summary_only",
        )
    context = build_innings_context(innings)
    assert context is not None, "summary-only innings returned above"
    config = preset_for_format(_format_for(match))
    result = scoring_engine.rebuild_innings(context, config, estimated=estimated)

    new_state = _state_from_result(innings, result)
    old_state = _state_from_db(innings)
    new_digest = digest(new_state)
    if digest(old_state) == new_digest:
        return RebuildReport(
            scope="innings",
            match_id=str(match.id),
            innings_id=str(innings.id),
            changed=False,
            digest=new_digest,
        )

    with transaction.atomic():
        new_revision = (innings.revision or 0) + 1
        _persist_innings(innings, result, revision=new_revision)

    payload = {
        "match_id": str(match.id),
        "innings_id": str(innings.id),
        "source": getattr(source, "code", None),
        "digest": new_digest,
    }
    core_events.publish(core_events.TOPIC_SCORECARD_UPDATED, payload)
    if match.status in {DbMatchStatus.COMPLETED, DbMatchStatus.ABANDONED}:
        core_events.publish(core_events.TOPIC_CORRECTION_APPLIED, payload)

    return RebuildReport(
        scope="innings",
        match_id=str(match.id),
        innings_id=str(innings.id),
        changed=True,
        digest=new_digest,
    )


def rebuild_match(match: Match, *, source: Any = None, estimated: bool = False) -> RebuildReport:
    """Rebuild every innings and the match result in one transaction."""
    # The caller's instance may predate a completion/correction; the lifecycle
    # decisions (match.completed vs correction.applied) must read fresh state.
    match.refresh_from_db()
    innings = list(
        match.innings.all().order_by("innings_number", "is_super_over", "super_over_number")
    )
    pairs = [(db_innings, build_innings_context(db_innings)) for db_innings in innings]
    rebuildable = [(db_innings, ctx) for db_innings, ctx in pairs if ctx is not None]
    if not rebuildable and innings:
        return RebuildReport(
            scope="match",
            match_id=str(match.id),
            innings_id=None,
            changed=False,
            digest=digest(None),
            skipped_reason="summary_only",
        )

    config = preset_for_format(_format_for(match))
    contexts = [ctx for _, ctx in rebuildable]
    scorecard = scoring_engine.rebuild_match(
        _match_context(match), contexts, config, estimated=estimated
    )

    per_innings: list[tuple[Innings, InningsResult, str]] = []
    total_changed = False
    for (db_innings, _ctx), result in zip(rebuildable, scorecard.innings, strict=False):
        new_state = _state_from_result(db_innings, result)
        new_digest = digest(new_state)
        changed = digest(_state_from_db(db_innings)) != new_digest
        total_changed = total_changed or changed
        per_innings.append((db_innings, result, new_digest))

    new_result_state = _match_result_state(match, scorecard.result)
    result_changed = _existing_result_state(match) != new_result_state
    was_completed = match.status in _TERMINAL
    becoming_completed = (
        scorecard.result is not None and not was_completed and new_result_state is not None
    )

    if not total_changed and not result_changed:
        match_digest = digest(
            [state[2] for state in per_innings],
            _match_result_state(match, scorecard.result),
        )
        return RebuildReport(
            scope="match",
            match_id=str(match.id),
            innings_id=None,
            changed=False,
            digest=match_digest,
        )

    with transaction.atomic():
        new_revision = (match.revision or 0) + 1
        for db_innings, result, _digest in per_innings:
            _persist_innings(db_innings, result, revision=new_revision)
        _persist_match_result(match, scorecard.result, revision=new_revision)

    match_digest = digest([state[2] for state in per_innings], new_result_state)
    payload = {
        "match_id": str(match.id),
        "source": getattr(source, "code", None),
        "digest": match_digest,
    }
    core_events.publish(core_events.TOPIC_SCORECARD_UPDATED, payload)
    if becoming_completed:
        core_events.publish(core_events.TOPIC_MATCH_COMPLETED, payload)
    elif was_completed and (total_changed or result_changed):
        core_events.publish(core_events.TOPIC_CORRECTION_APPLIED, payload)

    return RebuildReport(
        scope="match",
        match_id=str(match.id),
        innings_id=None,
        changed=True,
        digest=match_digest,
        match_completed=becoming_completed,
    )
