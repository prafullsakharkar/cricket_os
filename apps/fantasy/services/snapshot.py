"""Snapshot builder (Task 3): canonical match statistics -> frozen
``FantasyMatchSnapshot`` with coverage flags and a stable digest.

The snapshot is the immutable input to every fantasy computation: it carries
match context, per-player batting/bowling/fielding lines, dismissal kinds,
awards and a ``coverage`` block describing which data classes are present.
The same (ruleset, snapshot) always reproduces byte-identical points.

Sources (canonical rows, never ``delivery``):

- ``Match`` + ``MatchResult`` — result type, super over, DLS (reduced overs).
- ``MatchPlayer`` — playing-XI membership, substitutes.
- ``PlayerMatchStat`` — batting/bowling JSON lists (one per innings) and the
  fielding JSON; ``is_partial`` marks a summary-only innings.
- ``MatchAward`` — player-of-the-match.

Fielding counts: read from the fielding JSON when present; otherwise derived
from the dismissal credits inside the batting lines (a caught dismissal
credits ``fielder1_id`` a catch, a stumping credits ``fielder1_id``, a run-out
credits every listed fielder), and bowled/lbw wickets are counted per bowler.
A summary-only match (any ``is_partial`` row) yields reduced coverage
(``has_ball_data=False``) and, when its dismissals carry no fielder credits,
``has_fielding_data=False`` — the engine then reports those rules as
unsupported and flags the result partial (never guessed).
"""

from __future__ import annotations

import gzip
import json
from typing import Any

from apps.fantasy.engine import (
    FantasyMatchSnapshot,
    MatchContext,
    PlayerLine,
    SnapshotCoverage,
    snapshot_digest,
)
from apps.fantasy.selectors import match_fantasy_source

#: App dismissal types -> engine ``dismissal_kind`` vocabulary. ``CAUGHT`` and
#: ``CAUGHT_AND_BOWLED`` both map to ``caught`` (a caught-and-bowled is still a
#: catch); ``MANKAD`` counts as a run-out for duck purposes. Retired-hurt is
#: handled separately (not an out by default; the policy can override).
DISMISSAL_TO_KIND: dict[str, str] = {
    "BOWLED": "bowled",
    "CAUGHT": "caught",
    "CAUGHT_AND_BOWLED": "caught",
    "LBW": "lbw",
    "RUN_OUT": "run_out",
    "STUMPED": "stumped",
    "HIT_WICKET": "hit_wicket",
    "OBSTRUCTING_THE_FIELD": "obstructing_the_field",
    "TIMED_OUT": "timed_out",
    "HANDLED_THE_BALL": "handled_ball",
    "HIT_THE_BALL_TWICE": "hit_the_ball_twice",
    "RETIRED_OUT": "retired_out",
    "MANKAD": "run_out",
}

#: Dismissal types that credit a fielder (used to derive fielding counts).
_CAUGHT_KINDS = {"CAUGHT", "CAUGHT_AND_BOWLED"}
_STUMPED_KINDS = {"STUMPED"}
_RUN_OUT_KINDS = {"RUN_OUT", "MANKAD"}
_BOWLED_LBW_KINDS = {"BOWLED", "LBW"}


def _batting_lines(batting: Any) -> list[dict[str, Any]]:
    return [line for line in batting if isinstance(line, dict)] if isinstance(batting, list) else []


def _bowling_lines(bowling: Any) -> list[dict[str, Any]]:
    return [line for line in bowling if isinstance(line, dict)] if isinstance(bowling, list) else []


def _derive_fielding(
    stats: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, int]], dict[str, int]]:
    """Derive per-player fielding counts + bowled/lbw wickets from dismissal
    credits in every batting line across the match."""
    fielding: dict[str, dict[str, int]] = {}
    bowled_lbw: dict[str, int] = {}
    for row in stats:
        for line in _batting_lines(row.get("batting")):
            dismissal_type = line.get("dismissal_type")
            if dismissal_type in _CAUGHT_KINDS:
                catcher = line.get("fielder1_id")
                if catcher is None and dismissal_type == "CAUGHT_AND_BOWLED":
                    catcher = line.get("bowler_id")
                if catcher:
                    fielding.setdefault(str(catcher), {})
                    fielding[str(catcher)]["catches"] = fielding[str(catcher)].get("catches", 0) + 1
            elif dismissal_type in _STUMPED_KINDS:
                stumper = line.get("fielder1_id")
                if stumper:
                    fielding.setdefault(str(stumper), {})
                    fielding[str(stumper)]["stumpings"] = (
                        fielding[str(stumper)].get("stumpings", 0) + 1
                    )
            elif dismissal_type in _RUN_OUT_KINDS:
                for fielder in (line.get("fielder1_id"), line.get("fielder2_id")):
                    if fielder:
                        key = str(fielder)
                        fielding.setdefault(key, {})
                        fielding[key]["run_outs"] = fielding[key].get("run_outs", 0) + 1
            if dismissal_type in _BOWLED_LBW_KINDS and line.get("bowler_id"):
                key = str(line["bowler_id"])
                bowled_lbw[key] = bowled_lbw.get(key, 0) + 1
    return fielding, bowled_lbw


def _coverage_flags(
    stats: list[dict[str, Any]],
    derived_fielding: dict[str, dict[str, int]],
) -> SnapshotCoverage:
    """Compute which data classes this match's snapshot carries."""
    has_ball_data = bool(stats) and not any(row.get("is_partial") for row in stats)
    any_dismissal = False
    all_dismissals_credited = True
    has_fielder_refs = False
    fielding_json_present = False
    for row in stats:
        fielding_json = row.get("fielding") or {}
        if fielding_json:
            fielding_json_present = True
        if row.get("run_outs_direct") is not None:  # pragma: no cover - future data
            has_fielder_refs = True
        for line in _batting_lines(row.get("batting")):
            dismissal_type = line.get("dismissal_type")
            if dismissal_type:
                any_dismissal = True
                # Only fielding-involved dismissals (caught/stumped/run-out)
                # need a fielder credit; bowled/lbw/etc. legitimately have none.
                if dismissal_type in _CAUGHT_KINDS | _STUMPED_KINDS | _RUN_OUT_KINDS:
                    has_line_credit = bool(
                        line.get("fielder1_id")
                        or line.get("fielder2_id")
                        or line.get("fielder3_id")
                    )
                    if not has_line_credit:
                        all_dismissals_credited = False
            if line.get("fielder1_id") or line.get("fielder2_id") or line.get("fielder3_id"):
                has_fielder_refs = True

    # Fielding counts are known when the fielding JSON was captured, when there
    # are no dismissals to attribute (0 is exact), or when every dismissal
    # carries fielder credits.
    has_fielding_data = (
        fielding_json_present or not any_dismissal or all_dismissals_credited
    ) and bool(stats)
    return SnapshotCoverage(
        has_ball_data=has_ball_data,
        has_dismissal_kinds=any_dismissal,
        has_fielding_data=has_fielding_data,
        has_fielder_credit_types=has_fielder_refs,
    )


def _line_for(
    row: dict[str, Any],
    derived: dict[str, dict[str, int]],
    bowled_lbw: dict[str, int],
    match_players: dict[str, dict[str, Any]],
    pom_player_id: str | None,
) -> PlayerLine:
    player_id = str(row["player_id"])
    batting = _batting_lines(row.get("batting"))
    bowling = _bowling_lines(row.get("bowling"))
    fielding_json = row.get("fielding") or {}

    # -- batting aggregation (multi-innings matches sum the lines) ----------
    runs = sum(int(line.get("runs") or 0) for line in batting)
    balls = sum(int(line.get("balls") or 0) for line in batting)
    fours = sum(int(line.get("fours") or 0) for line in batting)
    sixes = sum(int(line.get("sixes") or 0) for line in batting)
    retired_hurt = any(
        line.get("retirement_type") == "HURT" or line.get("dismissal_type") == "RETIRED_HURT"
        for line in batting
    )
    dismissal_kind = None
    for line in batting:
        if line.get("out"):
            kind = DISMISSAL_TO_KIND.get(str(line.get("dismissal_type") or ""))
            if kind is not None:
                dismissal_kind = kind  # last out dismissal wins

    # -- bowling aggregation -------------------------------------------------
    balls_bowled = sum(int(line.get("legal_balls") or 0) for line in bowling)
    runs_conceded = sum(int(line.get("runs_conceded") or 0) for line in bowling)
    wickets = sum(int(line.get("wickets") or 0) for line in bowling)
    maidens = sum(int(line.get("maidens") or 0) for line in bowling)
    dots = sum(int(line.get("dots") or 0) for line in bowling)

    # -- fielding: JSON wins; otherwise the derived dismissal credits ---------
    if fielding_json:
        catches = int(fielding_json.get("catches") or 0)
        stumpings = int(fielding_json.get("stumpings") or 0)
        run_outs_total = int(fielding_json.get("run_outs") or 0)
        run_outs_direct = fielding_json.get("run_outs_direct")
        run_outs_indirect = fielding_json.get("run_outs_indirect")
    else:
        mine = derived.get(player_id, {})
        catches = int(mine.get("catches", 0))
        stumpings = int(mine.get("stumpings", 0))
        run_outs_total = int(mine.get("run_outs", 0))
        run_outs_direct = None
        run_outs_indirect = None

    mp = match_players.get(player_id, {})
    return PlayerLine(
        player_id=player_id,
        is_playing_xi=bool(mp.get("is_playing_xi", row.get("is_playing_xi", True))),
        did_bat=bool(row.get("did_bat", bool(batting))),
        did_bowl=bool(row.get("did_bowl", bool(bowling))),
        did_field=bool(row.get("did_field", True)),
        runs=runs,
        balls_faced=balls,
        fours=fours,
        sixes=sixes,
        dismissal_kind=dismissal_kind,
        balls_bowled=balls_bowled,
        wickets=wickets,
        maidens=maidens,
        runs_conceded=runs_conceded,
        dots_bowled=dots,
        bowled_lbw_wickets=int(bowled_lbw.get(player_id, 0)),
        catches=catches,
        stumpings=stumpings,
        run_outs_total=run_outs_total,
        run_outs_direct=run_outs_direct,
        run_outs_indirect=run_outs_indirect,
        player_of_match=player_id == pom_player_id,
        is_substitute=bool(mp.get("is_substitute", False)),
        retired_hurt=retired_hurt,
    )


def build_snapshot(match: Any) -> FantasyMatchSnapshot:
    """Assemble the frozen snapshot for one match (reads the primary DB only)."""
    source = match_fantasy_source(match.id if hasattr(match, "id") else match)
    match_row = source["match"]
    if match_row is None:
        raise ValueError(f"match {match} not found")
    result = source["result"] or {}
    awards = source["awards"]
    stats = source["stats"]

    match_players = {str(row["player_id"]): row for row in source["match_players"]}
    pom_player_id = next(
        (str(row["player_id"]) for row in awards if row["award_type"] == "player_of_match"),
        None,
    )

    derived_fielding, bowled_lbw = _derive_fielding(stats)
    coverage = _coverage_flags(stats, derived_fielding)

    lines = tuple(
        _line_for(row, derived_fielding, bowled_lbw, match_players, pom_player_id) for row in stats
    )

    snapshot = FantasyMatchSnapshot(
        match=MatchContext(
            match_id=str(match_row.id),
            format=match_row.format_type,
            result_type=result.get("result_type") or match_row.result_type,
            series_id=str(match_row.series_id) if match_row.series_id else None,
        ),
        coverage=coverage,
        player_lines=lines,
        super_over_played=bool(
            result.get("is_super_over")
            if result
            else match_row.result_type == "SUPER_OVER_TIE_BREAK"
        ),
        reduced_overs=bool(match_row.is_dls_applied),
    )
    return FantasyMatchSnapshot(
        match=snapshot.match,
        coverage=snapshot.coverage,
        player_lines=snapshot.player_lines,
        super_over_played=snapshot.super_over_played,
        reduced_overs=snapshot.reduced_overs,
        digest=snapshot_digest(snapshot),
    )


# --------------------------------------------------------------------------- #
# Payload serialisation (stored compressed with each calc-audit row)
# --------------------------------------------------------------------------- #


def _snapshot_to_dict(snapshot: FantasyMatchSnapshot) -> dict[str, Any]:
    return {
        "match": {
            "match_id": snapshot.match.match_id,
            "format": snapshot.match.format,
            "result_type": snapshot.match.result_type,
            "series_id": snapshot.match.series_id,
        },
        "coverage": {
            "has_ball_data": snapshot.coverage.has_ball_data,
            "has_dismissal_kinds": snapshot.coverage.has_dismissal_kinds,
            "has_fielding_data": snapshot.coverage.has_fielding_data,
            "has_fielder_credit_types": snapshot.coverage.has_fielder_credit_types,
        },
        "super_over_played": snapshot.super_over_played,
        "reduced_overs": snapshot.reduced_overs,
        "player_lines": [_line_to_dict(line) for line in snapshot.player_lines],
    }


def _line_to_dict(line: PlayerLine) -> dict[str, Any]:
    return {
        "player_id": line.player_id,
        "is_playing_xi": line.is_playing_xi,
        "did_bat": line.did_bat,
        "did_bowl": line.did_bowl,
        "did_field": line.did_field,
        "runs": line.runs,
        "balls_faced": line.balls_faced,
        "fours": line.fours,
        "sixes": line.sixes,
        "dismissal_kind": line.dismissal_kind,
        "balls_bowled": line.balls_bowled,
        "wickets": line.wickets,
        "maidens": line.maidens,
        "runs_conceded": line.runs_conceded,
        "dots_bowled": line.dots_bowled,
        "bowled_lbw_wickets": line.bowled_lbw_wickets,
        "hat_trick": line.hat_trick,
        "catches": line.catches,
        "stumpings": line.stumpings,
        "run_outs_total": line.run_outs_total,
        "run_outs_direct": line.run_outs_direct,
        "run_outs_indirect": line.run_outs_indirect,
        "player_of_match": line.player_of_match,
        "is_super_over": line.is_super_over,
        "is_substitute": line.is_substitute,
        "retired_hurt": line.retired_hurt,
    }


def _snapshot_from_dict(data: dict[str, Any]) -> FantasyMatchSnapshot:
    match = data["match"]
    coverage = data["coverage"]
    snapshot = FantasyMatchSnapshot(
        match=MatchContext(
            match_id=match["match_id"],
            format=match.get("format"),
            result_type=match.get("result_type"),
            series_id=match.get("series_id"),
        ),
        coverage=SnapshotCoverage(
            has_ball_data=coverage["has_ball_data"],
            has_dismissal_kinds=coverage["has_dismissal_kinds"],
            has_fielding_data=coverage["has_fielding_data"],
            has_fielder_credit_types=coverage["has_fielder_credit_types"],
        ),
        super_over_played=data["super_over_played"],
        reduced_overs=data["reduced_overs"],
        player_lines=tuple(PlayerLine(**line) for line in data["player_lines"]),
    )
    return FantasyMatchSnapshot(
        match=snapshot.match,
        coverage=snapshot.coverage,
        player_lines=snapshot.player_lines,
        super_over_played=snapshot.super_over_played,
        reduced_overs=snapshot.reduced_overs,
        digest=snapshot_digest(snapshot),
    )


def snapshot_payload(snapshot: FantasyMatchSnapshot) -> bytes:
    """Canonical JSON of the snapshot, gzip-compressed (stored on calc audit)."""
    canonical = json.dumps(
        _snapshot_to_dict(snapshot), sort_keys=True, separators=(",", ":"), default=str
    )
    return gzip.compress(canonical.encode("utf-8"))


def snapshot_from_payload(payload: bytes) -> FantasyMatchSnapshot:
    """Rehydrate a snapshot from its stored payload (recomputable inputs)."""
    raw = gzip.decompress(payload).decode("utf-8")
    return _snapshot_from_dict(json.loads(raw))


def snapshot_digest_from(snapshot: FantasyMatchSnapshot) -> str:
    """The canonical digest of a snapshot (== ``snapshot.digest``)."""
    return snapshot_digest(snapshot)
