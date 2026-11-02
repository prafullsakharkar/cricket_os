"""Cricsheet historical adapter: local file → canonical rows → Tier-1 rebuild.

Registers the ``cricsheet`` pipeline used by ``import_cricsheet`` and the
``ingestion.ingest_job`` task. One pipeline run ingests ONE local Cricsheet
match YAML file:

1. **parse** (:mod:`ingestion.adapters.cricsheet_parse`) → ``CricsheetMatch``;
2. **identity resolution** — teams/players are upserted via the provider map
   (``upsert_team``/``upsert_player``), keyed by the Cricsheet registry ids or
   normalised names, and registered as ``MatchTeam``/``MatchPlayer``;
3. **persist** — the match, toss, innings and every delivery go through the
   idempotent write path (:func:`append_or_revise`, ``suppress_events=True``);
4. **rebuild** — :func:`rebuild_match` derives the scorecard/result in one
   transaction (events published after the job commits);
5. **reconcile** — the rebuilt ``InningsSummary`` totals are diffed against the
   file's recorded totals; a mismatch writes a ``reconciliation_mismatch`` DLQ
   row (DATA_INGESTION §4.6).

The idempotency key is ``cricsheet:<match_id>:<content_sha256>`` — identical
file content re-imports as a no-op; changed content supersedes only the balls
that changed (revisioned via the write path).
"""

from __future__ import annotations

import datetime
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from apps.core.enums import TeamType
from apps.deliveries.services.validation import DeliveryDraft
from apps.deliveries.services.write import append_or_revise
from apps.ingestion.adapters.cricsheet_parse import (
    CricsheetInnings,
    CricsheetMatch,
    CricsheetParseError,
    parse_cricsheet_yaml,
    sha256_of,
)
from apps.ingestion.models import ImportJob
from apps.ingestion.services.jobs import record_error
from apps.ingestion.services.pipeline import (
    IngestContext,
    PipelineError,
    PipelineSpec,
    StageResult,
    register_pipeline,
)
from apps.innings.models import Innings, InningsSummary
from apps.matches.models import Match, MatchTeam, Toss
from apps.players.identity_scoring import normalize_name
from apps.players.services.players import upsert_player
from apps.scorecards.services.rebuild import rebuild_match
from apps.teams.models import Team
from apps.teams.services.teams import upsert_team

#: The pipeline name this adapter registers (used by commands/tasks).
PIPELINE_NAME = "cricsheet"

_UTC = datetime.UTC


# --------------------------------------------------------------------------- #
# Identity resolution
# --------------------------------------------------------------------------- #


def _registry_identifier(registry: dict[str, dict[str, str]], name: str) -> str | None:
    """First non-empty registry id (key_pulse/key_opta/…) for ``name``."""
    for value in (registry.get(name) or {}).values():
        if value:
            return value
    return None


def _team_type(match_type: str, gender: str) -> str:
    if match_type in ("TEST", "ODI", "T20I"):
        return TeamType.WOMEN_INTERNATIONAL if gender == "W" else TeamType.INTERNATIONAL
    # TeamType has no women's "domestic" label — WOMEN_FRANCHISE is the only
    # women's non-international option (matches the schema §3.2 catalogue).
    return TeamType.WOMEN_FRANCHISE if gender == "W" else TeamType.DOMESTIC


def _upsert_team(provider: Any, parsed: CricsheetMatch, name: str) -> Team:
    identifier = _registry_identifier(parsed.team_registry, name) or normalize_name(name)
    return upsert_team(
        provider,
        identifier,
        name=name,
        team_type=_team_type(parsed.match_type, parsed.gender),
        gender=parsed.gender,
        is_international=parsed.match_type in ("TEST", "ODI", "T20I"),
    )


def _upsert_player(provider: Any, parsed: CricsheetMatch, name: str) -> UUID:
    identifier = _registry_identifier(parsed.player_registry, name) or normalize_name(name)
    return upsert_player(provider, identifier, full_name=name, gender=parsed.gender).pk


def _player_map(provider: Any, parsed: CricsheetMatch) -> dict[str, UUID]:
    """Cricsheet name -> internal player UUID (all named players)."""
    mapping: dict[str, UUID] = {}
    for names in parsed.players_by_team.values():
        for name in names:
            mapping[name] = _upsert_player(provider, parsed, name)
    return mapping


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


def _upsert_match(provider: Any, parsed: CricsheetMatch) -> Match:
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
        updates: dict[str, Any] = {
            "format_type": parsed.match_type,
            "gender": parsed.gender,
            "source_local_date": parsed.date,
            "overs_limit": parsed.overs_limit,
            "provider_updated_at": timezone.now(),
        }
        Match.objects.filter(id=match.id).update(**updates)

    for number, name in enumerate(parsed.teams, start=1):
        team = _upsert_team(provider, parsed, name)
        MatchTeam.objects.get_or_create(match=match, team=team, defaults={"team_number": number})
    return match


def _upsert_toss(match: Match, parsed: CricsheetMatch) -> None:
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


def _upsert_players(match: Match, parsed: CricsheetMatch, player_ids: dict[str, UUID]) -> None:
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


def _upsert_innings(match: Match, parsed: CricsheetMatch) -> dict[int, Innings]:
    """Create/return the innings for each parsed innings, keyed by number."""
    result: dict[int, Innings] = {}
    for innings in parsed.innings:
        batting = match.teams.filter(team__name=innings.team).first()
        if batting is None:
            continue
        bowling = match.teams.exclude(id=batting.id).first()
        assert bowling is not None, "a Cricsheet match always has exactly two teams"
        db_innings, _ = Innings.objects.get_or_create(
            match=match,
            innings_number=innings.number,
            is_super_over=False,
            defaults={
                "batting_team_id": batting.team_id,
                "bowling_team_id": bowling.team_id,
                "status": "IN_PROGRESS",
            },
        )
        result[innings.number] = db_innings
    return result


def _drafts(
    innings: CricsheetInnings,
    player_ids: dict[str, UUID],
    revision: int,
) -> list[DeliveryDraft]:
    drafts: list[DeliveryDraft] = []
    for d in innings.deliveries:
        drafts.append(
            DeliveryDraft(
                delivery_seq=d.delivery_seq,
                over_number=d.over_number,
                ball_in_over=d.ball_in_over,
                bowler_id=player_ids[d.bowler],
                batter_id=player_ids[d.batter],
                non_striker_id=player_ids[d.non_striker],
                runs_off_bat=d.runs_off_bat,
                wides=d.wides,
                noballs=d.noballs,
                byes=d.byes,
                leg_byes=d.leg_byes,
                penalty_runs=d.penalty_runs,
                is_wicket=d.is_wicket,
                dismissal_type=d.dismissal_type,
                dismissed_player_id=player_ids[d.dismissed_player] if d.dismissed_player else None,
                bowler_credited_wicket=d.bowler_credited_wicket,
                fielder1_id=player_ids[d.fielders[0]] if len(d.fielders) > 0 else None,
                fielder2_id=player_ids[d.fielders[1]] if len(d.fielders) > 1 else None,
                fielder3_id=player_ids[d.fielders[2]] if len(d.fielders) > 2 else None,
                retired_player_id=player_ids[d.retired_player] if d.retired_player else None,
                retirement_type=d.retirement_type,
                revision=revision,
            )
        )
    return drafts


def _reconcile(match: Match, parsed: CricsheetMatch, job: ImportJob) -> None:
    """Diff the rebuilt summaries against the file's recorded totals (§4.6)."""
    for db_innings in match.innings.all().order_by("innings_number"):
        expected = next((i for i in parsed.innings if i.number == db_innings.innings_number), None)
        if expected is None:
            continue
        summary = InningsSummary.objects.filter(innings_id=db_innings.id).first()
        if summary is None:
            continue
        if summary.runs != expected.runs or summary.wickets != expected.wickets:
            record_error(
                job,
                stage="reconcile",
                error_class="reconciliation_mismatch",
                payload={
                    "match_id": parsed.match_id,
                    "innings_number": db_innings.innings_number,
                    "expected_runs": expected.runs,
                    "actual_runs": summary.runs,
                    "expected_wickets": expected.wickets,
                    "actual_wickets": summary.wickets,
                },
            )


def build_cricsheet_pipeline(factory_args: dict[str, Any]) -> PipelineSpec:
    """Pipeline factory for the ``cricsheet`` name (registered at import).

    The idempotency key needs only the filename (Cricsheet match id) and the
    raw sha256 — no parsing — so a malformed file still lands in a job that
    fails permanently (the parse error becomes a ``PipelineError`` inside the
    stage → DLQ + ``failed`` job, never a retried transient).
    """
    path = Path(factory_args["path"])
    match_id = path.stem
    content_sha256 = sha256_of(path)
    return PipelineSpec(
        provider_code=factory_args.get("provider_code", "cricsheet"),
        job_type="cricsheet",
        idempotency_key=f"cricsheet:{match_id}:{content_sha256}",
        scope={"match_id": match_id, "file": str(path)},
        stages=[_parse_and_ingest_stage(path)],
        checkpoint_scope=f"cricsheet:{match_id}",
    )


def _parse_and_ingest_stage(path: Any) -> Callable[[IngestContext], StageResult]:
    """Parse the file then run the ingest stage (parse failures are permanent)."""

    def stage(ctx: IngestContext) -> StageResult:
        try:
            parsed = parse_cricsheet_yaml(path)
        except CricsheetParseError as exc:
            raise PipelineError(
                str(exc),
                stage="parse",
                error_class="CricsheetParseError",
                payload={"file": str(path)},
            ) from exc
        return _ingest_stage(parsed)(ctx)

    return stage


def _ingest_stage(parsed: CricsheetMatch) -> Callable[[IngestContext], StageResult]:
    def stage(ctx: IngestContext) -> StageResult:
        provider = ctx.provider
        with transaction.atomic():
            match = _upsert_match(provider, parsed)
            _upsert_toss(match, parsed)
            player_ids = _player_map(provider, parsed)
            _upsert_players(match, parsed, player_ids)
            innings = _upsert_innings(match, parsed)

            total_deliveries = sum(len(i.deliveries) for i in parsed.innings)
            noop = 0
            for parsed_innings in parsed.innings:
                db_innings = innings.get(parsed_innings.number)
                if db_innings is None:
                    continue
                drafts = _drafts(parsed_innings, player_ids, parsed.revision)
                write_report = append_or_revise(
                    db_innings, drafts, source=provider, suppress_events=True
                )
                noop += len(write_report["noop"])

            rebuild_match(match, source=provider)
            _reconcile(match, parsed, ctx.job)
        return StageResult(
            read=total_deliveries,
            written=total_deliveries - noop,
            skipped=noop,
            checkpoint_cursor=parsed.match_id,
        )

    return stage


register_pipeline(PIPELINE_NAME, build_cricsheet_pipeline)

__all__ = ["PIPELINE_NAME", "build_cricsheet_pipeline"]
