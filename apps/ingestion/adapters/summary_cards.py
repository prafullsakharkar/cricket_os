"""Summary-only historical adapter: local card JSON → provider-written figures.

Registers the ``summary_cards`` pipeline used by ``import_summary_cards`` and
the ``ingestion.ingest_job`` task. One pipeline run ingests ONE local
summary-only scorecard JSON file (see :mod:`ingestion.adapters.summary_parse`):

1. **parse** → :class:`SummaryMatch`;
2. **identity resolution** — teams/players upserted via the provider map keyed
   by normalised name (summary cards carry no registry ids);
3. **persist** — the match, toss, innings, and the provider's **final figures**:
   ``innings_summary`` rows with ``batch_complete=false`` plus the
   ``batting_entry`` / ``bowling_entry`` rows of the card. **No ``delivery``
   rows are ever written** (DATABASE_SCHEMA §4.13);
4. **result** — the provider's match result is written to ``match_result`` +
   the denormalised ``match`` columns.

Because ``batch_complete=false`` marks these innings as summary-only, the
engine rebuild skips them (provider-written rows are authoritative) — the
adapter never calls ``rebuild_match``.

The idempotency key is ``summary:<file_stem>:<content_sha256>`` — identical
content re-imports as a no-op; changed content re-upserts the card rows.
"""

from __future__ import annotations

import datetime
import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from apps.ingestion.adapters.identity import upsert_player, upsert_team
from apps.ingestion.adapters.summary_parse import (
    SummaryMatch,
    SummaryParseError,
    parse_summary_json,
)
from apps.ingestion.services.pipeline import (
    IngestContext,
    PipelineError,
    PipelineSpec,
    StageResult,
    register_pipeline,
)
from apps.innings.models import Innings, InningsSummary
from apps.matches.models import Match, MatchResult, MatchTeam, Toss
from apps.scorecards.models import BattingEntry, BowlingEntry

#: The pipeline name this adapter registers (used by commands/tasks).
PIPELINE_NAME = "summary_cards"

_UTC = datetime.UTC


# --------------------------------------------------------------------------- #
# Identity resolution
# --------------------------------------------------------------------------- #


def _player_map(provider: Any, parsed: SummaryMatch) -> dict[str, UUID]:
    """Name -> internal player UUID across the roster AND every card line."""
    names = {name for roster in parsed.players_by_team.values() for name in roster}
    for innings in parsed.innings:
        names.update(line.player for line in innings.batting)
        names.update(line.player for line in innings.bowling)
    return {
        name: upsert_player(provider, name=name, registry={}, gender=parsed.gender)
        for name in names
    }


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


def _upsert_match(provider: Any, parsed: SummaryMatch) -> Match:
    start = datetime.datetime.combine(parsed.date, datetime.time(9, 0), tzinfo=_UTC)
    match, created = Match.objects.get_or_create(
        source_provider_id=provider.pk,
        provider_match_id=parsed.match_id,
        defaults={
            "format_type": parsed.match_type,
            "gender": parsed.gender,
            "start_time_utc": start,
            "source_local_date": parsed.date,
            "source_timezone": "UTC",
            "overs_limit": parsed.overs_limit,
            "status": "SCHEDULED",
            "provider_updated_at": timezone.now(),
            "ingested_at": timezone.now(),
            "revision": 1,
        },
    )
    if not created:
        Match.objects.filter(id=match.id).update(
            format_type=parsed.match_type,
            gender=parsed.gender,
            source_local_date=parsed.date,
            overs_limit=parsed.overs_limit,
            provider_updated_at=timezone.now(),
        )

    for number, name in enumerate(parsed.teams, start=1):
        team = upsert_team(
            provider,
            name=name,
            registry={},
            match_type=parsed.match_type,
            gender=parsed.gender,
        )
        MatchTeam.objects.get_or_create(match=match, team=team, defaults={"team_number": number})
    return match


def _upsert_toss(match: Match, parsed: SummaryMatch) -> None:
    if not parsed.toss_winner or not parsed.toss_decision:
        return
    winner = match.teams.filter(team__name=parsed.toss_winner).first()
    if winner is None:
        return
    Toss.objects.update_or_create(
        match=match,
        defaults={"winner_team": winner.team, "decision": parsed.toss_decision},
    )
    Match.objects.filter(id=match.id).update(
        toss_winner_team=winner.team,
        toss_decision=parsed.toss_decision,
    )


def _upsert_players(match: Match, parsed: SummaryMatch, player_ids: dict[str, UUID]) -> None:
    from apps.matches.models import MatchPlayer

    for team_name, names in parsed.players_by_team.items():
        team = match.teams.filter(team__name=team_name).first()
        if team is None:
            continue
        for name in names:
            MatchPlayer.objects.get_or_create(
                match=match,
                player_id=player_ids[name],
                defaults={"team": team.team, "is_playing_xi": True},
            )


def _upsert_innings(match: Match, parsed: SummaryMatch) -> dict[int, Innings]:
    result: dict[int, Innings] = {}
    for innings in parsed.innings:
        batting = match.teams.filter(team__name=innings.team).first()
        if batting is None:
            continue
        bowling = match.teams.exclude(id=batting.id).first()
        assert bowling is not None, "a summary match always has exactly two teams"
        db_innings, _ = Innings.objects.get_or_create(
            match=match,
            innings_number=innings.number,
            is_super_over=False,
            defaults={
                "batting_team_id": batting.team_id,
                "bowling_team_id": bowling.team_id,
                "status": "COMPLETED",
            },
        )
        result[innings.number] = db_innings
    return result


def _persist_cards(
    innings_map: dict[int, Innings],
    parsed: SummaryMatch,
    player_ids: dict[str, UUID],
) -> int:
    """Write the summary-only card rows; return how many rows were written."""
    written = 0
    for parsed_innings in parsed.innings:
        db_innings = innings_map.get(parsed_innings.number)
        if db_innings is None:
            continue

        extras = dict(parsed_innings.extras)
        extras["total"] = sum(extras.values())
        InningsSummary.objects.update_or_create(
            innings=db_innings,
            defaults={
                "runs": parsed_innings.runs,
                "wickets": parsed_innings.wickets,
                "legal_balls": parsed_innings.legal_balls,
                "overs_display": parsed_innings.overs_display,
                "extras": extras,
                # Provider-written figures are authoritative; the engine must
                # not derive anything from absent ball-by-ball data.
                "batch_complete": False,
            },
        )
        written += 1

        present_batting: set[UUID] = set()
        for position, batting_line in enumerate(parsed_innings.batting, start=1):
            BattingEntry.objects.update_or_create(
                innings=db_innings,
                player_id=player_ids[batting_line.player],
                defaults={
                    "team_id": db_innings.batting_team_id,
                    "position": position,
                    "runs": batting_line.runs,
                    "balls": batting_line.balls,
                    "fours": batting_line.fours,
                    "sixes": batting_line.sixes,
                    "is_out": batting_line.is_out,
                    "dismissal_type": batting_line.dismissal_type,
                    "bowler_id": player_ids[batting_line.bowler] if batting_line.bowler else None,
                    "fielder1_id": player_ids[batting_line.fielder]
                    if batting_line.fielder
                    else None,
                    "revision": 1,
                },
            )
            present_batting.add(player_ids[batting_line.player])
            written += 1
        BattingEntry.objects.filter(innings=db_innings).exclude(
            player_id__in=present_batting
        ).delete()

        present_bowling: set[UUID] = set()
        for bowling_line in parsed_innings.bowling:
            BowlingEntry.objects.update_or_create(
                innings=db_innings,
                player_id=player_ids[bowling_line.player],
                defaults={
                    "team_id": db_innings.bowling_team_id,
                    "overs_display": bowling_line.overs_display,
                    "legal_balls": bowling_line.legal_balls,
                    "runs_conceded": bowling_line.runs_conceded,
                    "wickets": bowling_line.wickets,
                    "maidens": bowling_line.maidens,
                    "wides": bowling_line.wides,
                    "noballs": bowling_line.noballs,
                    "revision": 1,
                },
            )
            present_bowling.add(player_ids[bowling_line.player])
            written += 1
        BowlingEntry.objects.filter(innings=db_innings).exclude(
            player_id__in=present_bowling
        ).delete()
    return written


def _persist_result(match: Match, parsed: SummaryMatch) -> None:
    """Write the provider's match result + denormalised match columns."""
    if not parsed.result_winner:
        return
    winner = match.teams.filter(team__name=parsed.result_winner).first()
    if winner is None:
        return
    result_type = parsed.result_type or "WIN"
    result_text = f"{parsed.result_winner} won"
    if parsed.margin_type == "wickets":
        result_text = f"{parsed.result_winner} won by {parsed.margin_value} wickets"
    elif parsed.margin_type == "runs":
        result_text = f"{parsed.result_winner} won by {parsed.margin_value} runs"

    MatchResult.objects.update_or_create(
        match=match,
        defaults={
            "winner_team_id": winner.team_id,
            "result_type": result_type,
            "margin_type": parsed.margin_type,
            "margin_value": parsed.margin_value,
            "result_text": result_text,
            "is_dls": False,
            "is_super_over": False,
            "completed_at": timezone.now(),
        },
    )
    Match.objects.filter(id=match.id).update(
        status="COMPLETED",
        result_type=result_type,
        result_text=result_text,
        winner_team_id=winner.team_id,
        end_time_utc=timezone.now(),
    )


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #


def build_summary_pipeline(factory_args: dict[str, Any]) -> PipelineSpec:
    """Pipeline factory for the ``summary_cards`` name (registered at import).

    The idempotency key needs only the filename stem and the raw sha256 — no
    parsing — so a malformed file still lands in a job that fails permanently
    (the parse error becomes a ``PipelineError`` inside the stage → DLQ +
    ``failed`` job, never a retried transient).
    """
    path = Path(factory_args["path"])
    match_id = path.stem
    content_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    return PipelineSpec(
        provider_code=factory_args.get("provider_code", "cricsheet"),
        job_type="summary_cards",
        idempotency_key=f"summary:{match_id}:{content_sha256}",
        scope={"match_id": match_id, "file": str(path)},
        stages=[_parse_and_ingest_stage(path, dry_run=bool(factory_args.get("dry_run", False)))],
        checkpoint_scope=f"summary:{match_id}",
    )


def _parse_and_ingest_stage(path: Path, *, dry_run: bool) -> Callable[[IngestContext], StageResult]:
    """Parse the file then run the ingest stage (parse failures are permanent)."""

    def stage(ctx: IngestContext) -> StageResult:
        try:
            parsed = parse_summary_json(path)
        except SummaryParseError as exc:
            raise PipelineError(
                str(exc),
                stage="parse",
                error_class="SummaryParseError",
                payload={"file": str(path)},
            ) from exc
        if dry_run:
            total = sum(len(i.batting) + len(i.bowling) + 1 for i in parsed.innings)
            return StageResult(read=total, written=0)
        return _ingest_stage(parsed)(ctx)

    return stage


def _ingest_stage(parsed: SummaryMatch) -> Callable[[IngestContext], StageResult]:
    def stage(ctx: IngestContext) -> StageResult:
        provider = ctx.provider
        with transaction.atomic():
            match = _upsert_match(provider, parsed)
            _upsert_toss(match, parsed)
            player_ids = _player_map(provider, parsed)
            _upsert_players(match, parsed, player_ids)
            innings = _upsert_innings(match, parsed)
            written = _persist_cards(innings, parsed, player_ids)
            _persist_result(match, parsed)
        return StageResult(read=written, written=written, checkpoint_cursor=parsed.match_id)

    return stage


register_pipeline(PIPELINE_NAME, build_summary_pipeline)

__all__ = ["PIPELINE_NAME", "build_summary_pipeline"]
