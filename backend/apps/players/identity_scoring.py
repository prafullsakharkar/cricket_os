"""Pure player-identity scoring (no DB, no Django ORM — only stdlib types).

Two players are the same person when their distinguishing features agree:
full name, date of birth, birth country, gender and shared team history. The
features are weighted (sum 1.0):

    name           0.35
    date_of_birth  0.25
    birth_country  0.15
    gender         0.10
    shared_teams   0.15

The most important guardrail is the **DoB mismatch cap**: if both dates are
present and differ, the pair is almost certainly distinct, so that feature
scores 0.0 *and* the total is capped at 0.25 — no combination of the other
features may push a mismatched pair above the duplicate-candidate threshold
(D6). Everything else contributes normally and the weighted total is clamped
to [0, 1].

This module is deliberately pure so the weights, normalisation and cap are
unit-testable in isolation and provably deterministic. See
IDENTITY_RESOLUTION.md §2 for the full rationale.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

#: Feature weights (sum 1.0). Documented in IDENTITY_RESOLUTION.md §2.
WEIGHTS: Mapping[str, float] = {
    "name": 0.35,
    "date_of_birth": 0.25,
    "birth_country": 0.15,
    "gender": 0.10,
    "shared_teams": 0.15,
}

#: Hard ceiling on the total when both DoBs are present but differ.
DOB_MISMATCH_CAP = 0.25


@dataclass(frozen=True, slots=True)
class PlayerFeatures:
    """The distinguishing features used to compare two players.

    ``team_ids`` is the set of team ids the player has belonged to (from
    ``TeamMembership``); an empty frozenset means "no shared-team evidence".
    """

    full_name: str
    date_of_birth: date | None
    birth_country: str | None
    gender: str | None
    team_ids: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ScoreResult:
    """A symmetric pair score with a per-feature breakdown.

    ``score`` is the clamped weighted total in [0, 1]. ``breakdown`` maps each
    feature name to its own ``{"score", "weight", "contribution", "raw"}``
    dict plus a top-level ``"cap"`` entry describing any applied ceiling.
    """

    score: float
    breakdown: dict[str, Any]


def normalize_name(value: str) -> str:
    """Normalise a name for comparison.

    Unicode NFKD decomposition → strip combining marks → casefold → remove
    punctuation → collapse whitespace. "Virat" and "VIRAT", "José" and "Jose"
    and "José" (pre-composed vs decomposed) all compare equal.
    """
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    folded = without_marks.casefold()
    # Punctuation (apostrophes, hyphens, periods) carries no identity signal.
    stripped = "".join(ch for ch in folded if ch.isalnum() or ch.isspace())
    return " ".join(stripped.split())


def _name_score(norm_a: str, norm_b: str) -> float:
    """Score two normalised names on [0, 1]."""
    if norm_a == norm_b:
        return 1.0
    tokens_a, tokens_b = norm_a.split(), norm_b.split()
    if not tokens_a or not tokens_b:
        return 0.0  # no tokens on either side: no name evidence
    if _initials_match(tokens_a, tokens_b) or _initials_match(tokens_b, tokens_a):
        return 1.0
    set_a, set_b = set(tokens_a), set(tokens_b)
    return 2 * len(set_a & set_b) / (len(set_a) + len(set_b))


def _initials_match(short: list[str], long: list[str]) -> bool:
    """True when ``short`` is an initialled form of ``long``.

    ``short`` must contain at least one single-letter token (the tell-tale of
    an abbreviation), every single-letter token must equal the first letter of
    some token of ``long``, and every multi-letter token must appear verbatim
    ("V Kohli" vs "Virat Kohli" — but "Virat Kohli" vs "Virat Kohli Sharma"
    is a plain subset, not an initialled form, so it falls through to the
    token-overlap ratio).
    """
    long_tokens = set(long)
    long_initials = {token[0] for token in long_tokens}
    has_initial = False
    for token in short:
        if len(token) == 1:
            has_initial = True
            if token not in long_initials:
                return False
        elif token not in long_tokens:
            return False
    return has_initial


def _dob_score(a: date | None, b: date | None) -> tuple[float, bool]:
    """DoB score plus whether the mismatch cap applies (D6)."""
    if a is None or b is None:
        return 0.5, False  # no evidence either way
    if a == b:
        return 1.0, False
    return 0.0, True  # both present and different: mismatch cap


def _country_score(a: str | None, b: str | None) -> float:
    if a is None or b is None:
        return 0.5
    return 1.0 if a == b else 0.0


def _gender_score(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.5  # unknown/unspecified gender
    return 1.0 if a == b else 0.0


def _teams_score(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 0.0  # no shared-team evidence
    return 2 * len(a & b) / (len(a) + len(b))


def score_pair(a: PlayerFeatures, b: PlayerFeatures) -> ScoreResult:
    """Score how likely ``a`` and ``b`` are the same person.

    Symmetric and deterministic: ``score_pair(a, b) == score_pair(b, a)`` and
    identical inputs always yield an identical :class:`ScoreResult`.
    """
    norm_a = normalize_name(a.full_name)
    norm_b = normalize_name(b.full_name)
    name_score = _name_score(norm_a, norm_b)
    dob_score, dob_capped = _dob_score(a.date_of_birth, b.date_of_birth)

    contributions: dict[str, float] = {
        "name": name_score,
        "date_of_birth": dob_score,
        "birth_country": _country_score(a.birth_country, b.birth_country),
        "gender": _gender_score(a.gender, b.gender),
        "shared_teams": _teams_score(a.team_ids, b.team_ids),
    }

    total = sum(contributions[name] * WEIGHTS[name] for name in WEIGHTS)
    if dob_capped:
        total = min(total, DOB_MISMATCH_CAP)
    total = max(0.0, min(1.0, total))

    breakdown: dict[str, Any] = {}
    for name in WEIGHTS:
        breakdown[name] = {
            "score": contributions[name],
            "weight": WEIGHTS[name],
            "contribution": contributions[name] * WEIGHTS[name],
            "raw": {
                "a": _feature_dict(a),
                "b": _feature_dict(b),
            },
        }
    breakdown["cap"] = {
        "applied": dob_capped,
        "max_total": DOB_MISMATCH_CAP if dob_capped else None,
    }
    return ScoreResult(score=total, breakdown=breakdown)


def _feature_dict(features: PlayerFeatures) -> dict[str, Any]:
    """The feature set as JSON-safe primitives (dates -> ISO strings)."""
    return {
        "full_name": features.full_name,
        "date_of_birth": features.date_of_birth.isoformat()
        if features.date_of_birth is not None
        else None,
        "birth_country": features.birth_country,
        "gender": features.gender,
        "team_ids": sorted(str(team_id) for team_id in features.team_ids),
    }


__all__ = [
    "DOB_MISMATCH_CAP",
    "WEIGHTS",
    "PlayerFeatures",
    "ScoreResult",
    "normalize_name",
    "score_pair",
]
