"""Pure summary-only scorecard parser (stdlib only; no Django).

Summary-only cards carry the **final figures** of each innings — no ball-by-ball
data (DATABASE_SCHEMA §4.13). This parser turns one local JSON file into plain
dataclasses that the Django adapter (:mod:`ingestion.adapters.summary_cards`)
maps onto ``innings_summary`` (``batch_complete=false``) plus provider-written
``batting_entry`` / ``bowling_entry`` rows and the match result.

File shape::

    {
      "match_id": "S-001",
      "date": "2023-10-25",
      "match_type": "ODI",            # Cricsheet-style label (FORMAT_MAP)
      "gender": "male",               # optional (defaults to male)
      "overs_limit": 50,
      "venue": "...", "city": "...",
      "teams": ["India", "Australia"],
      "toss": {"winner": "India", "decision": "bat"},
      "result": {"winner": "Australia", "result_type": "WIN",
                 "margin_type": "wickets", "margin_value": 5},
      "players": {"India": ["Rohit Sharma", ...], "Australia": [...]},
      "innings": [
        {"team": "India", "runs": 250, "wickets": 8, "overs": "50.0",
         "extras": {"wides": 10, "noballs": 2, "byes": 5, "leg_byes": 3, "penalty": 0},
         "batting": [{"player": "...", "runs": 90, "balls": 100, "fours": 8,
                      "sixes": 2, "is_out": true, "dismissal_type": "CAUGHT",
                      "bowler": "...", "fielder": "..."}],
         "bowling": [{"player": "...", "overs": "10.0", "legal_balls": 60,
                      "runs_conceded": 45, "wickets": 2, "maidens": 1}]}
      ]
    }

``overs`` is the display form (``"48.2"`` = 48 overs + 2 balls); the parser
derives ``legal_balls`` from it (single source of truth). The idempotency hash
is sha256 of the raw bytes (shared with the Cricsheet parser).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from apps.ingestion.adapters.cricsheet_parse import FORMAT_MAP, sha256_of

_GENDER_MAP = {"male": "M", "female": "W"}


class SummaryParseError(ValueError):
    """The file is not a well-formed summary-only scorecard."""


@dataclass(frozen=True)
class SummaryBattingLine:
    player: str
    runs: int
    balls: int
    fours: int = 0
    sixes: int = 0
    is_out: bool = False
    dismissal_type: str | None = None
    bowler: str | None = None
    fielder: str | None = None


@dataclass(frozen=True)
class SummaryBowlingLine:
    player: str
    overs_display: str
    legal_balls: int
    runs_conceded: int
    wickets: int
    maidens: int = 0
    wides: int = 0
    noballs: int = 0


@dataclass(frozen=True)
class SummaryInnings:
    team: str
    number: int
    runs: int
    wickets: int
    overs_display: str
    legal_balls: int
    extras: dict[str, int]
    batting: tuple[SummaryBattingLine, ...]
    bowling: tuple[SummaryBowlingLine, ...]


@dataclass(frozen=True)
class SummaryMatch:
    match_id: str
    content_sha256: str
    date: date
    gender: str
    match_type: str
    overs_limit: int | None
    venue: str | None
    city: str | None
    teams: tuple[str, str]
    toss_winner: str | None
    toss_decision: str | None
    result_winner: str | None
    result_type: str | None
    margin_type: str | None
    margin_value: int | None
    players_by_team: dict[str, tuple[str, ...]]
    innings: tuple[SummaryInnings, ...]


def _as_int(value: Any, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise SummaryParseError(f"{field}: expected an integer, got {value!r}")
    return value


def _as_int_or_none(value: Any, *, field: str) -> int | None:
    if value is None:
        return None
    return _as_int(value, field=field)


def _str(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise SummaryParseError(f"{field}: expected a non-empty string, got {value!r}")
    return value


def _overs_to_legal_balls(overs_display: str) -> int:
    """``"48.2"`` -> 290 (48*6 + 2); ``"50"`` -> 300."""
    parts = overs_display.split(".")
    try:
        whole = int(parts[0])
        fraction = int(parts[1]) if len(parts) > 1 and parts[1] else 0
    except ValueError as exc:
        raise SummaryParseError(f"bad overs display {overs_display!r}") from exc
    if len(parts) > 2 or fraction >= 6:
        raise SummaryParseError(f"bad overs display {overs_display!r}")
    return whole * 6 + fraction


def _batting(raw: Any, *, innings_number: int) -> tuple[SummaryBattingLine, ...]:
    lines: list[SummaryBattingLine] = []
    for index, row in enumerate(raw or [], start=1):
        if not isinstance(row, dict):
            raise SummaryParseError(f"innings {innings_number}: batting line {index} not an object")
        lines.append(
            SummaryBattingLine(
                player=_str(row.get("player"), field=f"batting {index}.player"),
                runs=_as_int(row.get("runs", 0), field=f"batting {index}.runs"),
                balls=_as_int(row.get("balls", 0), field=f"batting {index}.balls"),
                fours=_as_int(row.get("fours", 0), field=f"batting {index}.fours"),
                sixes=_as_int(row.get("sixes", 0), field=f"batting {index}.sixes"),
                is_out=bool(row.get("is_out", False)),
                dismissal_type=row.get("dismissal_type"),
                bowler=row.get("bowler"),
                fielder=row.get("fielder"),
            )
        )
    return tuple(lines)


def _bowling(raw: Any, *, innings_number: int) -> tuple[SummaryBowlingLine, ...]:
    lines: list[SummaryBowlingLine] = []
    for index, row in enumerate(raw or [], start=1):
        if not isinstance(row, dict):
            raise SummaryParseError(f"innings {innings_number}: bowling line {index} not an object")
        overs_display = _str(row.get("overs"), field=f"bowling {index}.overs")
        lines.append(
            SummaryBowlingLine(
                player=_str(row.get("player"), field=f"bowling {index}.player"),
                overs_display=overs_display,
                legal_balls=_as_int(
                    row.get("legal_balls", _overs_to_legal_balls(overs_display)),
                    field=f"bowling {index}.legal_balls",
                ),
                runs_conceded=_as_int(
                    row.get("runs_conceded", 0), field=f"bowling {index}.runs_conceded"
                ),
                wickets=_as_int(row.get("wickets", 0), field=f"bowling {index}.wickets"),
                maidens=_as_int(row.get("maidens", 0), field=f"bowling {index}.maidens"),
                wides=_as_int(row.get("wides", 0), field=f"bowling {index}.wides"),
                noballs=_as_int(row.get("noballs", 0), field=f"bowling {index}.noballs"),
            )
        )
    return tuple(lines)


def parse_summary_json(path: Path) -> SummaryMatch:
    """Parse one summary-only scorecard JSON file into a :class:`SummaryMatch`."""
    raw_bytes = path.read_bytes()
    try:
        data = json.loads(raw_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SummaryParseError(f"{path.name}: invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SummaryParseError(f"{path.name}: expected a JSON object")

    match_id = _str(data.get("match_id"), field="match_id")
    teams = tuple(str(t) for t in data.get("teams") or ())
    if len(teams) != 2:
        raise SummaryParseError(f"{path.name}: expected exactly two teams")

    try:
        match_date = date.fromisoformat(_str(data.get("date"), field="date"))
    except ValueError as exc:
        raise SummaryParseError(f"{path.name}: bad date {data.get('date')!r}") from exc

    gender = _GENDER_MAP.get(str(data.get("gender") or "male").lower(), "X")
    match_type = FORMAT_MAP.get(str(data.get("match_type") or ""), "OTHER")
    toss = data.get("toss") or {}
    result = data.get("result") or {}

    innings: list[SummaryInnings] = []
    for number, raw_innings in enumerate(data.get("innings") or [], start=1):
        if not isinstance(raw_innings, dict):
            raise SummaryParseError(f"innings {number}: not an object")
        overs_display = _str(raw_innings.get("overs"), field=f"innings {number}.overs")
        extras_raw = raw_innings.get("extras") or {}
        extras: dict[str, int] = {}
        for key in ("wides", "noballs", "byes", "leg_byes", "penalty"):
            extras[key] = _as_int(extras_raw.get(key, 0), field=f"innings {number}.extras.{key}")
        innings.append(
            SummaryInnings(
                team=_str(raw_innings.get("team"), field=f"innings {number}.team"),
                number=number,
                runs=_as_int(raw_innings.get("runs"), field=f"innings {number}.runs"),
                wickets=_as_int(raw_innings.get("wickets"), field=f"innings {number}.wickets"),
                overs_display=overs_display,
                legal_balls=_overs_to_legal_balls(overs_display),
                extras=extras,
                batting=_batting(raw_innings.get("batting"), innings_number=number),
                bowling=_bowling(raw_innings.get("bowling"), innings_number=number),
            )
        )

    players = data.get("players") or {}
    return SummaryMatch(
        match_id=match_id,
        content_sha256=sha256_of(path),
        date=match_date,
        gender=gender,
        match_type=match_type,
        overs_limit=_as_int_or_none(data.get("overs_limit"), field="overs_limit"),
        venue=data.get("venue"),
        city=data.get("city"),
        teams=teams,
        toss_winner=str(toss["winner"]) if toss.get("winner") else None,
        toss_decision=str(toss["decision"]) if toss.get("decision") else None,
        result_winner=str(result["winner"]) if result.get("winner") else None,
        result_type=str(result.get("result_type")) or None,
        margin_type=str(result["margin_type"]) if result.get("margin_type") else None,
        margin_value=_as_int_or_none(result.get("margin_value"), field="result.margin_value"),
        players_by_team={str(t): tuple(str(p) for p in plist) for t, plist in players.items()},
        innings=tuple(innings),
    )


__all__ = [
    "SummaryBattingLine",
    "SummaryBowlingLine",
    "SummaryInnings",
    "SummaryMatch",
    "SummaryParseError",
    "parse_summary_json",
]
