"""Hypothesis property tests for the pure identity scorer (no DB).

Three laws hold for every pair of feature sets:
- **symmetry**: ``score_pair(a, b).score == score_pair(b, a).score`` (and the
  per-feature scores agree — only the raw dicts swap sides);
- **bounds**: the total is always in [0, 1];
- **determinism**: identical inputs yield an identical ``ScoreResult``.
"""

from __future__ import annotations

import datetime

from hypothesis import given, settings
from hypothesis import strategies as st

from apps.players.identity_scoring import PlayerFeatures, score_pair

FEATURES = st.builds(
    PlayerFeatures,
    full_name=st.text(max_size=40),
    date_of_birth=st.one_of(
        st.none(),
        st.dates(min_value=datetime.date(1900, 1, 1), max_value=datetime.date(2020, 1, 1)),
    ),
    birth_country=st.one_of(st.none(), st.text(max_size=30)),
    gender=st.one_of(st.none(), st.sampled_from(["M", "W", "X"])),
    team_ids=st.frozensets(st.text(max_size=16), max_size=6),
)


@given(a=FEATURES, b=FEATURES)
@settings(max_examples=100)
def test_score_is_symmetric(a: PlayerFeatures, b: PlayerFeatures) -> None:
    forward = score_pair(a, b)
    backward = score_pair(b, a)
    assert forward.score == backward.score
    # Every feature's score is symmetric; only the raw dicts swap sides.
    for name in forward.breakdown:
        if name == "cap":
            assert forward.breakdown[name] == backward.breakdown[name]
            continue
        assert forward.breakdown[name]["score"] == backward.breakdown[name]["score"]
        assert forward.breakdown[name]["contribution"] == backward.breakdown[name]["contribution"]


@given(a=FEATURES, b=FEATURES)
@settings(max_examples=100)
def test_score_is_bounded(a: PlayerFeatures, b: PlayerFeatures) -> None:
    result = score_pair(a, b)
    assert 0.0 <= result.score <= 1.0


@given(a=FEATURES)
@settings(max_examples=100)
def test_score_is_deterministic(a: PlayerFeatures) -> None:
    assert score_pair(a, a) == score_pair(a, a)
