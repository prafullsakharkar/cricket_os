"""Pure Cricsheet YAML parser (stdlib + PyYAML only; no Django).

Turns one local Cricsheet match file into plain dataclasses that the Django
adapter (:mod:`ingestion.adapters.cricsheet`) maps onto canonical rows. Keeping
the parser pure makes every format rule (dismissal mapping, retirement
handling, over/ball numbering, registry extraction) unit-testable without a
database — the same split the Phase B register importer uses.

Cricsheet specifics handled here:

* ``delivery_seq`` is the 1-based position within an innings; ``over`` is
  1-based and ``ball`` is 0-based (``ball_in_over = ball + 1``);
* ``runs.total`` is decomposed into ``runs_off_bat`` + extras
  (``wides``/``noballs``/``byes``/``legbyes``/``penalty``);
* ``wicket.kind`` maps to a canonical ``DismissalType`` value; ``retired hurt``
  / ``retired not out`` become a **retirement** on the ball (``is_wicket=False``,
  ``retirement_type`` ``HURT``/``NOT_OUT``) per the schema's retirement rules,
  while ``retired out`` is a dismissal (``RETIRED_OUT``, not bowler-credited);
* bowler-credited dismissals are the kinds where the bowler takes the wicket
  (bowled, caught, caught & bowled, lbw, stumped, hit wicket, mankad);
* the provider identity is the filename stem (Cricsheet's ``match_id``), and the
  idempotency-relevant content hash is sha256 of the raw file bytes.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

#: Cricsheet ``wicket.kind`` -> canonical DismissalType value (apps.core.enums).
DISMISSAL_MAP: dict[str, str] = {
    "bowled": "BOWLED",
    "caught": "CAUGHT",
    "caught and bowled": "CAUGHT_AND_BOWLED",
    "lbw": "LBW",
    "run out": "RUN_OUT",
    "stumped": "STUMPED",
    "hit wicket": "HIT_WICKET",
    "obstructing the field": "OBSTRUCTING_THE_FIELD",
    "timed out": "TIMED_OUT",
    "handled the ball": "HANDLED_THE_BALL",
    "hit the ball twice": "HIT_THE_BALL_TWICE",
    "retired out": "RETIRED_OUT",
    "mankad": "MANKAD",
}

#: Dismissals where the bowler is credited with the wicket.
_BOWLER_CREDITED = {
    "BOWLED",
    "CAUGHT",
    "CAUGHT_AND_BOWLED",
    "LBW",
    "STUMPED",
    "HIT_WICKET",
    "MANKAD",
}

#: Cricsheet ``info.match_type`` -> FormatType value (apps.core.enums).
FORMAT_MAP: dict[str, str] = {
    "Test": "TEST",
    "WTest": "TEST",
    "ODI": "ODI",
    "WODI": "ODI",
    "T20I": "T20I",
    "WT20I": "T20I",
    "IT20": "T20I",
    "T20": "T20",
    "WT20": "T20",
    "First-class": "FIRST_CLASS",
    "List A": "LIST_A",
    "T Hundred": "THE_HUNDRED",
    "T10": "T10",
    "Other": "OTHER",
}

_GENDER_MAP = {"male": "M", "female": "W"}


class CricsheetParseError(ValueError):
    """The file is not a well-formed Cricsheet match (missing required keys)."""


@dataclass(frozen=True)
class CricsheetDelivery:
    delivery_seq: int
    over_number: int
    ball_in_over: int
    batter: str
    bowler: str
    non_striker: str
    runs_off_bat: int
    wides: int
    noballs: int
    byes: int
    leg_byes: int
    penalty_runs: int
    is_wicket: bool
    dismissal_type: str | None
    dismissed_player: str | None
    bowler_credited_wicket: bool
    fielders: tuple[str, ...]
    retired_player: str | None
    retirement_type: str | None

    @property
    def total_runs(self) -> int:
        return (
            self.runs_off_bat
            + self.wides
            + self.noballs
            + self.byes
            + self.leg_byes
            + self.penalty_runs
        )


@dataclass(frozen=True)
class CricsheetInnings:
    team: str
    number: int
    deliveries: tuple[CricsheetDelivery, ...]

    @property
    def runs(self) -> int:
        return sum(d.total_runs for d in self.deliveries)

    @property
    def wickets(self) -> int:
        return sum(1 for d in self.deliveries if d.is_wicket)


@dataclass(frozen=True)
class CricsheetMatch:
    match_id: str
    content_sha256: str
    date: date
    gender: str
    match_type: str
    teams: tuple[str, str]
    toss_winner: str | None
    toss_decision: str | None
    venue: str | None
    city: str | None
    overs_limit: int | None
    outcome_winner: str | None
    outcome_by_runs: int | None
    outcome_by_wickets: int | None
    revision: int
    #: Cricsheet ``info.registry.people``: name -> {key_*: value}.
    player_registry: dict[str, dict[str, str]]
    #: Cricsheet ``info.registry.teams``: name -> {key_*: value}.
    team_registry: dict[str, dict[str, str]]
    #: team name -> ordered player names (``info.players``).
    players_by_team: dict[str, tuple[str, ...]]
    innings: tuple[CricsheetInnings, ...]


def sha256_of(path: Path) -> str:
    """sha256 hex digest of the raw file bytes (the content idempotency key)."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _as_int(value: Any, *, field: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise CricsheetParseError(f"{field}: expected an integer, got {value!r}") from exc


def _extras(raw: Any) -> dict[str, int]:
    extras = raw.get("extras") or {}
    return {
        "wides": extras.get("wides", 0),
        "noballs": extras.get("noballs", 0),
        "byes": extras.get("byes", 0),
        "leg_byes": extras.get("legbyes", 0),
        "penalty_runs": extras.get("penalty", 0),
    }


def _wicket(raw: Any, delivery_seq: int) -> dict[str, Any]:
    """Decompose a Cricsheet ``wicket`` into the canonical delivery columns."""
    kind = raw.get("kind")
    player_out = raw.get("player_out")
    if not kind or not player_out:
        raise CricsheetParseError(f"delivery {delivery_seq}: wicket requires kind and player_out")
    if kind in ("retired hurt", "retired not out"):
        # A retirement, not a dismissal: no wicket, no dismissal_type.
        return {
            "is_wicket": False,
            "dismissal_type": None,
            "dismissed_player": None,
            "bowler_credited_wicket": False,
            "retired_player": player_out,
            "retirement_type": "HURT" if kind == "retired hurt" else "NOT_OUT",
            "fielders": (),
        }
    dismissal = DISMISSAL_MAP.get(kind)
    if dismissal is None:
        raise CricsheetParseError(f"delivery {delivery_seq}: unknown wicket kind {kind!r}")
    return {
        "is_wicket": True,
        "dismissal_type": dismissal,
        "dismissed_player": player_out,
        "bowler_credited_wicket": dismissal in _BOWLER_CREDITED,
        "retired_player": None,
        "retirement_type": None,
        "fielders": tuple(raw.get("fielders") or ()),
    }


def parse_cricsheet_yaml(path: Path) -> CricsheetMatch:
    """Parse one Cricsheet match file into a :class:`CricsheetMatch`."""
    raw_bytes = path.read_bytes()
    try:
        data = yaml.safe_load(raw_bytes)
    except yaml.YAMLError as exc:
        raise CricsheetParseError(f"{path.name}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("info"), dict):
        raise CricsheetParseError(f"{path.name}: missing top-level info")

    info: dict[str, Any] = data["info"]
    teams = tuple(str(t) for t in info.get("teams") or ())
    if len(teams) != 2:
        raise CricsheetParseError(f"{path.name}: expected exactly two teams")

    dates = info.get("dates") or []
    if not dates:
        raise CricsheetParseError(f"{path.name}: missing dates")
    try:
        match_date = date.fromisoformat(str(dates[0]))
    except ValueError as exc:
        raise CricsheetParseError(f"{path.name}: bad date {dates[0]!r}") from exc

    match_type = FORMAT_MAP.get(str(info.get("match_type") or ""), "OTHER")
    gender_raw = str(info.get("gender") or "male").lower()
    gender = _GENDER_MAP.get(gender_raw, "X")

    meta = data.get("meta") or {}
    registry = info.get("registry") or {}
    players = info.get("players") or {}
    toss = info.get("toss") or {}
    outcome = info.get("outcome") or {}
    outcome_by = outcome.get("by") or {}

    innings: list[CricsheetInnings] = []
    for number, raw_innings in enumerate(data.get("innings") or [], start=1):
        deliveries: list[CricsheetDelivery] = []
        for seq, raw_delivery in enumerate(raw_innings.get("deliveries") or [], start=1):
            over = raw_delivery.get("over")
            if over is None:
                raise CricsheetParseError(f"innings {number}: delivery {seq} missing over")
            ball_in_over = int(raw_delivery.get("ball", 0)) + 1
            runs = raw_delivery.get("runs") or {}
            extras = _extras(raw_delivery)
            wicket: dict[str, Any]
            if raw_delivery.get("wicket"):
                wicket = _wicket(raw_delivery["wicket"], seq)
            else:
                wicket = {
                    "is_wicket": False,
                    "dismissal_type": None,
                    "dismissed_player": None,
                    "bowler_credited_wicket": False,
                    "retired_player": None,
                    "retirement_type": None,
                    "fielders": (),
                }
            deliveries.append(
                CricsheetDelivery(
                    delivery_seq=seq,
                    over_number=int(over),
                    ball_in_over=ball_in_over,
                    batter=str(raw_delivery["batter"]),
                    bowler=str(raw_delivery["bowler"]),
                    non_striker=str(raw_delivery["non_striker"]),
                    runs_off_bat=runs.get("batter", 0),
                    **extras,
                    is_wicket=wicket["is_wicket"],
                    dismissal_type=wicket["dismissal_type"],
                    dismissed_player=wicket["dismissed_player"],
                    bowler_credited_wicket=wicket["bowler_credited_wicket"],
                    fielders=wicket["fielders"],
                    retired_player=wicket["retired_player"],
                    retirement_type=wicket["retirement_type"],
                )
            )
        innings.append(
            CricsheetInnings(
                team=str(raw_innings.get("team") or teams[number - 1]),
                number=number,
                deliveries=tuple(deliveries),
            )
        )

    return CricsheetMatch(
        match_id=path.stem,
        content_sha256=sha256_of(path),
        date=match_date,
        gender=gender,
        match_type=match_type,
        teams=teams,
        toss_winner=str(toss["winner"]) if toss.get("winner") else None,
        toss_decision=str(toss["decision"]) if toss.get("decision") else None,
        venue=str(info["venue"]) if info.get("venue") else None,
        city=str(info["city"]) if info.get("city") else None,
        overs_limit=_as_int(info.get("overs"), field="info.overs"),
        outcome_winner=str(outcome["winner"]) if outcome.get("winner") else None,
        outcome_by_runs=_as_int(outcome_by.get("runs"), field="outcome.by.runs"),
        outcome_by_wickets=_as_int(outcome_by.get("wickets"), field="outcome.by.wickets"),
        revision=_as_int(meta.get("revision"), field="meta.revision") or 1,
        player_registry={
            str(name): {str(key): str(val) for key, val in (entry or {}).items()}
            for name, entry in (registry.get("people") or {}).items()
        },
        team_registry={
            str(name): {str(key): str(val) for key, val in (entry or {}).items()}
            for name, entry in (registry.get("teams") or {}).items()
        },
        players_by_team={str(t): tuple(str(p) for p in plist) for t, plist in players.items()},
        innings=tuple(innings),
    )


__all__ = [
    "DISMISSAL_MAP",
    "FORMAT_MAP",
    "CricsheetDelivery",
    "CricsheetInnings",
    "CricsheetMatch",
    "CricsheetParseError",
    "parse_cricsheet_yaml",
    "sha256_of",
]
