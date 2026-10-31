"""Pure identity-scoring unit tests (no DB)."""

from __future__ import annotations

import datetime

import pytest

from apps.players.identity_scoring import (
    DOB_MISMATCH_CAP,
    WEIGHTS,
    PlayerFeatures,
    normalize_name,
    score_pair,
)

DOB = datetime.date(1988, 11, 5)
OTHER_DOB = datetime.date(1988, 11, 6)


def make_features(**overrides: object) -> PlayerFeatures:
    defaults: dict[str, object] = {
        "full_name": "Virat Kohli",
        "date_of_birth": DOB,
        "birth_country": "India",
        "gender": "M",
        "team_ids": frozenset(),
    }
    defaults.update(overrides)
    return PlayerFeatures(**defaults)  # type: ignore[arg-type]


class TestNormalizeName:
    def test_nfkd_decomposes_accents(self) -> None:
        assert normalize_name("José") == normalize_name("Jose")

    def test_decomposed_and_precomposed_are_equal(self) -> None:
        decomposed = "e\u0301"  # e + combining acute
        assert normalize_name(decomposed) == normalize_name("\u00e9")

    def test_casefold_lowercases(self) -> None:
        assert normalize_name("VIRAT KOHLI") == "virat kohli"

    def test_punctuation_removed(self) -> None:
        assert normalize_name("Kohli, Virat") == "kohli virat"
        assert normalize_name("O'Brien") == "obrien"

    def test_whitespace_collapsed(self) -> None:
        assert normalize_name("  Virat   Kohli\t") == "virat kohli"


class TestScorePair:
    def test_identical_features_score_one(self) -> None:
        # Shared-team evidence is required for a perfect score: with no team
        # history even identical features cap at 0.85 (teams weight is 0).
        a = make_features(team_ids=frozenset({"t1"}))
        assert score_pair(a, a).score == 1.0

    def test_identical_features_without_teams_scores_085(self) -> None:
        a = make_features(team_ids=frozenset())
        assert score_pair(a, a).score == pytest.approx(0.85)

    def test_name_equal_scores_full_name_weight(self) -> None:
        a = make_features(full_name="Virat Kohli")
        b = make_features(full_name="VIRAT KOHLI")  # normalises equal
        assert score_pair(a, b).breakdown["name"]["score"] == 1.0

    def test_initials_vs_full_name_scores_one(self) -> None:
        a = make_features(full_name="V Kohli")
        b = make_features(full_name="Virat Kohli")
        assert score_pair(a, b).breakdown["name"]["score"] == 1.0

    def test_token_overlap_ratio(self) -> None:
        a = make_features(full_name="Virat Kohli")
        b = make_features(full_name="Virat Kohli Sharma")
        # Dice: 2 * |A∩B| / (|A| + |B|) = 2*2/5. A verbatim subset is NOT an
        # initialled form, so it must not score 1.0.
        assert score_pair(a, b).breakdown["name"]["score"] == pytest.approx(0.8)

    def test_empty_name_scores_zero(self) -> None:
        a = make_features(full_name="")
        b = make_features(full_name="Virat Kohli")
        assert score_pair(a, b).breakdown["name"]["score"] == 0.0

    def test_dob_equal_scores_one(self) -> None:
        a = make_features(date_of_birth=DOB)
        b = make_features(date_of_birth=DOB)
        assert score_pair(a, b).breakdown["date_of_birth"]["score"] == 1.0

    def test_dob_missing_scores_half(self) -> None:
        a = make_features(date_of_birth=None)
        b = make_features(date_of_birth=DOB)
        assert score_pair(a, b).breakdown["date_of_birth"]["score"] == 0.5

    def test_dob_mismatch_scores_zero(self) -> None:
        a = make_features(date_of_birth=DOB)
        b = make_features(date_of_birth=OTHER_DOB)
        assert score_pair(a, b).breakdown["date_of_birth"]["score"] == 0.0

    def test_dob_mismatch_caps_total_at_025(self) -> None:
        # Everything else agrees (name, country, gender, teams) yet the total
        # is capped — no combination may push a mismatched pair over 0.25.
        a = make_features(date_of_birth=DOB)
        b = make_features(date_of_birth=OTHER_DOB)
        result = score_pair(a, b)
        assert result.score == DOB_MISMATCH_CAP
        assert result.breakdown["cap"]["applied"] is True
        assert result.breakdown["cap"]["max_total"] == DOB_MISMATCH_CAP

    def test_dob_mismatch_cap_blocks_duplicate_threshold(self) -> None:
        a = make_features(date_of_birth=DOB)
        b = make_features(date_of_birth=OTHER_DOB)
        result = score_pair(a, b)
        assert result.score < 0.60  # never flagged as a duplicate candidate

    def test_country_equal_missing_different(self) -> None:
        equal = score_pair(
            make_features(birth_country="India"), make_features(birth_country="India")
        )
        assert equal.breakdown["birth_country"]["score"] == 1.0
        missing = score_pair(
            make_features(birth_country=None), make_features(birth_country="India")
        )
        assert missing.breakdown["birth_country"]["score"] == 0.5
        different = score_pair(
            make_features(birth_country="India"),
            make_features(birth_country="Sri Lanka"),
        )
        assert different.breakdown["birth_country"]["score"] == 0.0

    def test_gender_equal_unknown_different(self) -> None:
        equal = score_pair(make_features(gender="M"), make_features(gender="M"))
        assert equal.breakdown["gender"]["score"] == 1.0
        unknown = score_pair(make_features(gender=None), make_features(gender="M"))
        assert unknown.breakdown["gender"]["score"] == 0.5
        different = score_pair(make_features(gender="M"), make_features(gender="W"))
        assert different.breakdown["gender"]["score"] == 0.0

    def test_shared_teams_dice(self) -> None:
        a = make_features(team_ids=frozenset({"t1", "t2"}))
        b = make_features(team_ids=frozenset({"t2", "t3", "t4"}))
        assert score_pair(a, b).breakdown["shared_teams"]["score"] == pytest.approx(2 * 1 / 5)

    def test_no_team_evidence_scores_zero(self) -> None:
        a = make_features(team_ids=frozenset())
        b = make_features(team_ids=frozenset())
        assert score_pair(a, b).breakdown["shared_teams"]["score"] == 0.0

    def test_full_agreement_scores_one(self) -> None:
        a = make_features(team_ids=frozenset({"t1"}))
        b = make_features(full_name="V Kohli", team_ids=frozenset({"t1"}))
        assert score_pair(a, b).score == 1.0

    def test_weights_sum_to_one(self) -> None:
        assert sum(WEIGHTS.values()) == pytest.approx(1.0)

    def test_breakdown_lists_each_contribution_and_raw(self) -> None:
        result = score_pair(make_features(), make_features())
        for name in WEIGHTS:
            entry = result.breakdown[name]
            assert entry["score"] * entry["weight"] == pytest.approx(entry["contribution"])
            assert entry["weight"] == WEIGHTS[name]
            assert "date_of_birth" in entry["raw"]["a"]
            assert "date_of_birth" in entry["raw"]["b"]

    def test_breakdown_raw_dates_are_iso_strings(self) -> None:
        result = score_pair(make_features(), make_features())
        raw = result.breakdown["name"]["raw"]["a"]
        assert raw["date_of_birth"] == DOB.isoformat()

    def test_breakdown_raw_team_ids_are_strings(self) -> None:
        a = make_features(team_ids=frozenset({"t1", "t2"}))
        b = make_features()
        result = score_pair(a, b)
        assert result.breakdown["shared_teams"]["raw"]["a"]["team_ids"] == ["t1", "t2"]
