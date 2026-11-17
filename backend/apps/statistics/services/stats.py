"""Tier-2 per-match statistics (``player_match_stat`` only in Phase C scope).

Statistics are built ONLY from the ``match.completed`` and ``correction.applied``
events (ARCHITECTURE §3, SCORING_RULES §6) — never on per-delivery events. The
build is idempotent: ``player_match_stat`` is keyed by ``(match, player)`` and a
rebuild that produces the same payload leaves ``revision`` untouched.

``is_partial`` flags matches imported through the summary-only path, where the
engine cannot derive ball-dependent metrics; those rows are marked so the API
can label them ``estimated``.
"""

from __future__ import annotations

import dataclasses
from typing import Any
from uuid import UUID

from django.utils import timezone

from apps.matches.models import Match
from apps.scorecards.services.rebuild import compute_scorecard
from apps.scoring import player_match_line
from apps.scoring.config import preset_for_format
from apps.scoring.types import BattingLine, BowlingLine, Format
from apps.statistics.models import PlayerMatchStat


def _line_dict(line: BattingLine | BowlingLine) -> dict[str, Any]:
    data = dataclasses.asdict(line)
    # Enums and nested dataclasses are not JSON-serialisable; normalise them.
    for key in list(data):
        value = data[key]
        if isinstance(value, UUID):
            data[key] = str(value)
        elif hasattr(value, "value"):  # StrEnum
            data[key] = value.value
    return data


def _payload_for(line: Any) -> dict[str, Any]:
    """The durable JSON payload stored on the stat row (drives idempotency)."""
    return {
        "did_bat": line.did_bat,
        "did_bowl": line.did_bowl,
        "batting": [_line_dict(b) for b in line.batting],
        "bowling": [_line_dict(b) for b in line.bowling],
    }


def build_match_stats(match: Match, *, source: Any = None, is_partial: bool = False) -> int:
    """Build/replace ``player_match_stat`` rows for a completed match.

    Returns the number of rows written or revision-bumped (0 when nothing
    changed). Idempotent and safe to call repeatedly.
    """
    scorecard = compute_scorecard(match)
    config = preset_for_format(Format(match.format_type))

    # Union of registered participants and anyone the engine saw batting/bowling.
    player_ids: set[str] = {
        str(pid) for pid in match.players.all().values_list("player_id", flat=True)
    }
    for result in scorecard.innings:
        player_ids.update(line.player_id for line in result.batting)
        player_ids.update(line.player_id for line in result.bowling)

    team_by_player = {
        str(row["player_id"]): (str(row["team_id"]), row["is_playing_xi"])
        for row in match.players.all().values("player_id", "team_id", "is_playing_xi")
    }

    written = 0
    for player_id in sorted(player_ids):
        team_id, is_playing_xi = team_by_player.get(player_id, (None, False))
        if team_id is None:
            continue
        line = player_match_line(
            match_id=str(match.id),
            player_id=player_id,
            team_id=team_id,
            fmt=Format(match.format_type),
            innings=scorecard.innings,
            config=config,
        )
        payload = _payload_for(line)
        existing = PlayerMatchStat.objects.filter(match_id=match.id, player_id=player_id).first()
        if (
            existing is not None
            and existing.batting == payload["batting"]
            and existing.bowling == payload["bowling"]
            and existing.did_bat == payload["did_bat"]
            and existing.did_bowl == payload["did_bowl"]
            and existing.is_partial == is_partial
        ):
            continue
        new_revision = (existing.revision if existing else 0) + 1
        PlayerMatchStat.objects.update_or_create(
            match_id=match.id,
            player_id=player_id,
            defaults={
                "team_id": team_id,
                "innings_id": None,
                "did_bat": payload["did_bat"],
                "did_bowl": payload["did_bowl"],
                "did_field": is_playing_xi,
                "is_playing_xi": is_playing_xi,
                "batting": payload["batting"],
                "bowling": payload["bowling"],
                "fielding": {},
                "source_provider_id": getattr(source, "id", None),
                "computed_at": match.end_time_utc or timezone.now(),
                "is_partial": is_partial,
                "revision": new_revision,
            },
        )
        written += 1
    return written


def _is_partial(match: Match) -> bool:
    """True when any innings came from a summary-only import (no ball data)."""
    return match.innings.filter(summary__batch_complete=False).exists()


def _on_match_event(payload: Any) -> None:
    """Event handler for ``match.completed`` / ``correction.applied``."""
    match_id = payload.get("match_id")
    if not match_id:
        return
    match = Match.objects.filter(id=match_id).first()
    if match is None:
        return
    build_match_stats(match, is_partial=_is_partial(match))


def register() -> None:
    """Wire the Tier-2 statistics rebuild to the match lifecycle events."""
    from apps.core import events as core_events

    core_events.subscribe(core_events.TOPIC_MATCH_COMPLETED, _on_match_event)
    core_events.subscribe(core_events.TOPIC_CORRECTION_APPLIED, _on_match_event)
