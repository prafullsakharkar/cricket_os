"""Card builders for shadow comparison (Phase I Task 2).

Three *independent* views of a completed match are built and diffed by
``apps.shadow.compare``:

* :func:`card_from_read_models` — the platform's canonical scorecard: the
  persisted ``innings_summary``/``batting_entry``/``bowling_entry`` read models
  plus ``match_result`` (this is what public endpoints serve);
* :func:`card_from_provider` — the provider's own card via the adapter
  (``ReplayAdapter.get_scorecard`` today; a licensed adapter's card when the
  provider gate is met);
* :func:`card_from_deliveries` — a **second derivation** straight from the raw
  ``delivery`` table (independent aggregation, not the read models), standing
  in for the historical source after completion.

Conventions mirrored from the scoring engine so the comparison does not
false-positive (SCORING_ENGINE_API, DATABASE_SCHEMA §4.6/§4.7):
``counts_ball_on_noball=True`` (a no-ball counts as a ball faced), wides never
count as a ball faced, bowler runs = wides + no-balls + runs off bat (byes/
leg-byes/penalty not charged), ``is_wicket`` counts only dismissals toward the
team tally plus RETIRED_OUT. Maidens are carried in the card but excluded from
mismatch detection (their definition is convention-sensitive).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# --------------------------------------------------------------------------- #
# Card shape (provider-neutral)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CardBatting:
    player_key: str
    runs: int = 0
    balls: int = 0
    fours: int = 0
    sixes: int = 0
    dismissal: str | None = None


@dataclass(frozen=True)
class CardBowling:
    player_key: str
    legal_balls: int = 0
    runs: int = 0
    wickets: int = 0
    maidens: int = 0


@dataclass(frozen=True)
class CardInnings:
    innings_number: int
    runs: int = 0
    wickets: int = 0
    overs_display: str = ""
    extras: dict[str, int] = None  # type: ignore[assignment]
    batting: tuple[CardBatting, ...] = ()
    bowling: tuple[CardBowling, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "extras", self.extras or {})
        object.__setattr__(self, "batting", tuple(self.batting))
        object.__setattr__(self, "bowling", tuple(self.bowling))


@dataclass(frozen=True)
class Card:
    match_id: str
    status: str | None = None
    result_type: str | None = None
    winner_team_id: str | None = None
    innings: tuple[CardInnings, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "innings", tuple(self.innings))


# --------------------------------------------------------------------------- #
# 1. The platform's canonical card (read models)
# --------------------------------------------------------------------------- #


def card_from_read_models(match: Any) -> Card:
    """The canonical card from the persisted read models + match result."""
    from apps.matches.models import MatchResult

    innings = tuple(
        CardInnings(
            innings_number=innings.innings_number,
            runs=innings.summary.runs if innings.summary else 0,
            wickets=innings.summary.wickets if innings.summary else 0,
            overs_display=(
                innings.summary.overs_display
                if innings.summary and innings.summary.overs_display
                else ""
            ),
            extras=innings.summary.extras if innings.summary else {},
            batting=tuple(
                CardBatting(
                    player_key=str(entry.player_id),
                    runs=entry.runs,
                    balls=entry.balls,
                    fours=entry.fours,
                    sixes=entry.sixes,
                    dismissal=entry.dismissal_type,
                )
                for entry in innings.batting_entries.select_related("player").all()
            ),
            bowling=tuple(
                CardBowling(
                    player_key=str(entry.player_id),
                    legal_balls=entry.legal_balls,
                    runs=entry.runs_conceded,
                    wickets=entry.wickets,
                    maidens=entry.maidens,
                )
                for entry in innings.bowling_entries.select_related("player").all()
            ),
        )
        for innings in match.innings.select_related("summary").order_by("innings_number")
    )
    result = MatchResult.objects.filter(match=match).first()
    return Card(
        match_id=str(match.id),
        status=str(match.status),
        result_type=str(result.result_type) if result else None,
        winner_team_id=str(result.winner_team_id) if result and result.winner_team_id else None,
        innings=innings,
    )


# --------------------------------------------------------------------------- #
# 2. The provider's card (adapter)
# --------------------------------------------------------------------------- #


def card_from_provider(match: Any, adapter: Any) -> Card:
    """The provider's own card, normalised to the same card shape.

    ``ReplayAdapter.get_scorecard`` returns innings summaries only (no per-
    player lines), so batting/bowling tuples are empty in replay mode. A
    licensed adapter's card maps here when the provider gate is met.
    """
    raw = adapter.get_scorecard(str(match.id))
    innings = tuple(
        CardInnings(
            innings_number=summary["innings_number"],
            runs=summary.get("runs", 0),
            wickets=summary.get("wickets", 0),
            overs_display=summary.get("overs_display", ""),
            extras=summary.get("extras") or {},
        )
        for summary in raw.get("summaries", [])
    )
    return Card(
        match_id=str(match.id),
        status=raw.get("status"),
        innings=innings,
    )


# --------------------------------------------------------------------------- #
# 3. The second derivation (raw deliveries) — independent of read models
# --------------------------------------------------------------------------- #


def card_from_deliveries(match: Any) -> Card:
    """A card aggregated straight from raw ``delivery`` rows.

    This is the shadow comparison's "second source": an independent
    recomputation of totals/extras/wickets and per-batter/per-bowler lines
    that does not use the persisted read models, so drift in the read models
    is detectable. It mirrors the engine's documented conventions.
    """
    from apps.deliveries.models import Delivery

    rows = list(
        Delivery.objects.filter(match=match, is_current=True)
        .select_related("innings")
        .order_by("innings__innings_number", "delivery_seq")
        .values(
            "innings__innings_number",
            "delivery_seq",
            "bowler_id",
            "batter_id",
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
            "over_number",
            "ball_in_over",
        )
    )

    by_innings: dict[int, dict[str, Any]] = {}
    for row in rows:
        inn = row["innings__innings_number"]
        bucket = by_innings.setdefault(
            inn,
            {
                "runs": 0,
                "wickets": 0,
                "wides": 0,
                "noballs": 0,
                "byes": 0,
                "leg_byes": 0,
                "penalty": 0,
                "balls": 0,
                "legal_balls": 0,
                "batting": {},
                "bowling": {},
            },
        )
        bucket["runs"] += (
            row["runs_off_bat"]
            + row["wides"]
            + row["noballs"]
            + row["byes"]
            + row["leg_byes"]
            + row["penalty_runs"]
        )
        bucket["wides"] += row["wides"]
        bucket["noballs"] += row["noballs"]
        bucket["byes"] += row["byes"]
        bucket["leg_byes"] += row["leg_byes"]
        bucket["penalty"] += row["penalty_runs"]
        bucket["balls"] += 1
        is_legal = row["wides"] == 0 and row["noballs"] == 0
        if is_legal:
            bucket["legal_balls"] += 1
        if row["is_wicket"]:
            bucket["wickets"] += 1

        batter_key = str(row["batter_id"])
        batter = bucket["batting"].setdefault(
            batter_key,
            {"runs": 0, "balls": 0, "fours": 0, "sixes": 0, "dismissal": None},
        )
        batter["runs"] += row["runs_off_bat"]
        # Wides never count as a ball faced; no-balls do (counts_ball_on_noball).
        if row["wides"] == 0:
            batter["balls"] += 1
        batter["fours"] += 1 if row["runs_off_bat"] == 4 else 0
        batter["sixes"] += 1 if row["runs_off_bat"] == 6 else 0
        if row["is_wicket"] and row["dismissed_player_id"] == row["batter_id"]:
            batter["dismissal"] = row["dismissal_type"]

        bowler_key = str(row["bowler_id"])
        bowler = bucket["bowling"].setdefault(
            bowler_key, {"legal_balls": 0, "runs": 0, "wickets": 0}
        )
        if is_legal:
            bowler["legal_balls"] += 1
        bowler["runs"] += row["runs_off_bat"] + row["wides"] + row["noballs"]
        if row["bowler_credited_wicket"]:
            bowler["wickets"] += 1

    innings: list[CardInnings] = []
    for inn in sorted(by_innings):
        bucket = by_innings[inn]
        batting = tuple(
            CardBatting(
                player_key=key,
                runs=values["runs"],
                balls=values["balls"],
                fours=values["fours"],
                sixes=values["sixes"],
                dismissal=values["dismissal"],
            )
            for key, values in sorted(bucket["batting"].items())
        )
        bowling = tuple(
            CardBowling(
                player_key=key,
                legal_balls=values["legal_balls"],
                runs=values["runs"],
                wickets=values["wickets"],
            )
            for key, values in sorted(bucket["bowling"].items())
        )
        overs_display = _overs_display(bucket["legal_balls"], bucket["balls"])
        innings.append(
            CardInnings(
                innings_number=inn,
                runs=bucket["runs"],
                wickets=bucket["wickets"],
                overs_display=overs_display,
                extras={
                    "wides": bucket["wides"],
                    "noballs": bucket["noballs"],
                    "byes": bucket["byes"],
                    "leg_byes": bucket["leg_byes"],
                    "penalty": bucket["penalty"],
                    "total": bucket["wides"]
                    + bucket["noballs"]
                    + bucket["byes"]
                    + bucket["leg_byes"]
                    + bucket["penalty"],
                },
                batting=batting,
                bowling=bowling,
            )
        )

    return Card(
        match_id=str(match.id),
        status=str(match.status),
        result_type=None,  # result inference is deliberately not done here
        innings=tuple(innings),
    )


def _overs_display(legal_balls: int, recorded_balls: int) -> str:
    """Best-effort overs display: complete overs from legal balls.

    Mirrors the engine's ``overs_display`` convention (6 legal balls per over).
    Extra recorded balls (wides/no-balls) do not advance the over.
    """
    overs, balls = divmod(legal_balls, 6)
    return f"{overs}.{balls}" if balls else str(overs)


__all__ = [
    "Card",
    "CardBatting",
    "CardBowling",
    "CardInnings",
    "card_from_deliveries",
    "card_from_provider",
    "card_from_read_models",
]
