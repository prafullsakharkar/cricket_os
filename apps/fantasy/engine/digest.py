"""Canonical digests (Task 2): content hash, snapshot digest, result digest.

Three deterministic SHA-256 digests anchor the phase's reproducibility
guarantee ("the same frozen ruleset version + match snapshot always reproduce
byte-identical points"):

- :func:`content_hash` — canonical serialization of a whole ruleset (rules
  sorted, Decimals normalized to 2dp); stable under key reordering and rule
  order, changes when any point value changes. Recorded on the frozen version
  at freeze time (Task 6).
- :func:`snapshot_digest` — canonical serialization of a match snapshot;
  recorded with each calc run so a recomputation is reproducible even after
  canonical data later changes.
- :func:`result_digest` — canonical serialization of the computed result;
  stored on the calc-audit row and used by ``fantasy_verify``.

All digests use ``sort_keys=True`` + compact separators so they are stable
across Python hash seeds, dict insertion orders, and rule/player ordering.
Decimals are normalized to 2 decimal places before serialization so
equivalent values (``1`` vs ``1.0`` vs ``1.00``) hash identically.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

from apps.fantasy.engine.types import (
    FantasyMatchSnapshot,
    FantasyResult,
    PlayerLine,
    PointRuleSpec,
    RulesetSpec,
    q2,
)


def _normalize(value: Any) -> Any:
    """Recursively normalize values for canonical serialization.

    Decimals are quantized to 2dp (the documented quantization); tuples become
    lists; everything else passes through (json handles str/int/bool/None).
    """
    if isinstance(value, Decimal):
        return str(q2(value))
    if isinstance(value, tuple):
        return [_normalize(v) for v in value]
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items()}
    return value


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(_normalize(payload), sort_keys=True, separators=(",", ":"), default=str)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _rule_dict(rule: PointRuleSpec) -> dict[str, Any]:
    return {
        "metric": rule.metric,
        "operator": rule.operator,
        "scope": rule.scope,
        "points": rule.points,
        "unit": rule.unit,
        "min_value": rule.min_value,
        "max_value": rule.max_value,
        "tiers": [(t.min_value, t.points) for t in rule.tiers],
        "tier_mode": rule.tier_mode,
        "min_balls": rule.min_balls,
        "min_overs": rule.min_overs,
        "cap_value": rule.cap_value,
    }


def _rule_sort_key(rule: PointRuleSpec) -> tuple[Any, ...]:
    # Sort by the canonical serialization so rule order never affects the hash.
    return (json.dumps(_normalize(_rule_dict(rule)), sort_keys=True),)


def content_hash(ruleset: RulesetSpec) -> str:
    """Canonical digest of a ruleset (stable under rule/key reordering)."""
    payload: dict[str, Any] = {
        "ruleset_id": ruleset.ruleset_id,
        "version": ruleset.version,
        "format_scope": ruleset.format_scope,
        "rules": [_rule_dict(r) for r in sorted(ruleset.rules, key=_rule_sort_key)],
        "policies": {
            "super_over": ruleset.policies.super_over,
            "abandoned": ruleset.policies.abandoned,
            "reduced_overs": ruleset.policies.reduced_overs,
            "retirement_hurt": ruleset.policies.retirement_hurt,
            "substitutes": ruleset.policies.substitutes,
        },
    }
    return _sha256(_canonical(payload))


def _line_dict(line: PlayerLine) -> dict[str, Any]:
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


def snapshot_digest(snapshot: FantasyMatchSnapshot) -> str:
    """Canonical digest of a match snapshot (order-independent)."""
    payload: dict[str, Any] = {
        "match": {
            "match_id": snapshot.match.match_id,
            "format": snapshot.match.format,
            "result_type": snapshot.match.result_type,
            "series_id": snapshot.match.series_id,
        },
        "coverage": {
            "has_ball_data": snapshot.coverage.has_ball_data,
            "has_dismissal_kinds": snapshot.coverage.has_dismissal_kinds,
            "has_fielder_credit_types": snapshot.coverage.has_fielder_credit_types,
        },
        "super_over_played": snapshot.super_over_played,
        "reduced_overs": snapshot.reduced_overs,
        "player_lines": [
            _line_dict(line)
            for line in sorted(snapshot.player_lines, key=lambda item: item.player_id)
        ],
    }
    return _sha256(_canonical(payload))


def result_digest(result: FantasyResult) -> str:
    """Canonical digest of a computed result (order-independent)."""
    players: list[dict[str, Any]] = []
    for player in sorted(result.players, key=lambda p: p.player_id):
        players.append(
            {
                "player_id": player.player_id,
                "total": player.total,
                "components": [
                    {"metric": c.metric, "points": c.points, "detail": c.detail}
                    for c in sorted(player.components, key=lambda c: c.metric)
                ],
            }
        )
    payload: dict[str, Any] = {
        "match_id": result.match_id,
        "ruleset_id": result.ruleset_id,
        "version": result.version,
        "players": players,
        "unsupported_rules": sorted(result.unsupported_rules),
        "is_partial": result.is_partial,
    }
    return _sha256(_canonical(payload))
