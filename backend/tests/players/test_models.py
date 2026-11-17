"""Player-domain model constraints and the generated name_key (DATABASE_SCHEMA §3.3)."""

from __future__ import annotations

import datetime

import pytest
from django.db import IntegrityError

from apps.players.models import Player, PlayerDuplicateCandidate, PlayerMerge

pytestmark = pytest.mark.django_db

DOB = datetime.date(1988, 11, 5)
EXPECTED_DAYS = (DOB - datetime.date(1, 1, 1)).days


def make_player(**overrides: object) -> Player:
    defaults: dict[str, object] = {"full_name": "Virat Kohli"}
    defaults.update(overrides)
    return Player.objects.create(**defaults)  # type: ignore[arg-type]


class TestNameKey:
    def test_generated_key_encodes_lowercase_name_and_day_number(self) -> None:
        player = make_player(date_of_birth=DOB)
        assert player.name_key == f"virat kohli|{EXPECTED_DAYS}"

    def test_null_dob_yields_empty_suffix(self) -> None:
        assert make_player().name_key == "virat kohli|"

    def test_distinct_dob_disambiguates_same_name(self) -> None:
        a = make_player(date_of_birth=DOB)
        b = make_player(full_name="Virat Kohli", date_of_birth=datetime.date(1988, 11, 6))
        assert a.name_key != b.name_key

    def test_key_updates_when_source_columns_change(self) -> None:
        player = make_player(date_of_birth=DOB)
        player.full_name = "V Kohli"
        player.save()
        player.refresh_from_db()
        assert player.name_key == f"v kohli|{EXPECTED_DAYS}"

    def test_key_is_casefolded_and_untrimmed_space_preserved(self) -> None:
        player = make_player(full_name="  VIRAT  KOHLI ", date_of_birth=DOB)
        assert player.name_key == f"  virat  kohli |{EXPECTED_DAYS}"


class TestPlayerConstraints:
    def test_duplicate_provider_id_rejected(self) -> None:
        provider_uuid = "22222222-2222-7222-8222-222222222222"
        make_player(source_provider_id=provider_uuid, provider_player_id="p1")
        with pytest.raises(IntegrityError):
            make_player(source_provider_id=provider_uuid, provider_player_id="p1")

    def test_gender_check_constraint(self) -> None:
        with pytest.raises(IntegrityError):
            make_player(gender="Z")

    def test_batting_style_check_constraint(self) -> None:
        with pytest.raises(IntegrityError):
            make_player(batting_style="ambidextrous")

    def test_bowling_style_check_constraint(self) -> None:
        with pytest.raises(IntegrityError):
            make_player(bowling_style="chaos")

    def test_primary_role_check_constraint(self) -> None:
        with pytest.raises(IntegrityError):
            make_player(primary_role="all_rounder_extra")


class TestPlayerMerge:
    def test_merge_row_written_and_unique_per_merged(self) -> None:
        winner = make_player(full_name="V Kohli")
        loser = make_player(full_name="Virat Kohli")
        PlayerMerge.objects.create(canonical_player=winner, merged_player=loser, reason="dup")
        with pytest.raises(IntegrityError):
            PlayerMerge.objects.create(canonical_player=winner, merged_player=loser, reason="dup")

    def test_distinct_check_refuses_self_merge(self) -> None:
        player = make_player()
        with pytest.raises(IntegrityError):
            PlayerMerge.objects.create(canonical_player=player, merged_player=player)

    def test_merge_permission_registered(self) -> None:
        perms = {p[0] for p in PlayerMerge._meta.permissions}
        assert "merge_players" in perms


class TestPlayerDuplicateCandidate:
    def test_pair_unique(self) -> None:
        candidate = make_player(full_name="V Kohli")
        canonical = make_player(full_name="Virat Kohli")
        PlayerDuplicateCandidate.objects.create(
            candidate_player=candidate,
            canonical_player=canonical,
            score="0.850",
            feature_breakdown={"name": 1.0},
        )
        with pytest.raises(IntegrityError):
            PlayerDuplicateCandidate.objects.create(
                candidate_player=candidate,
                canonical_player=canonical,
                score="0.850",
                feature_breakdown={"name": 1.0},
            )

    def test_reversed_pair_is_distinct(self) -> None:
        a = make_player(full_name="A")
        b = make_player(full_name="B")
        PlayerDuplicateCandidate.objects.create(
            candidate_player=a, canonical_player=b, score="0.500", feature_breakdown={}
        )
        PlayerDuplicateCandidate.objects.create(
            candidate_player=b, canonical_player=a, score="0.500", feature_breakdown={}
        )
        assert PlayerDuplicateCandidate.objects.count() == 2

    def test_status_check_constraint(self) -> None:
        a = make_player(full_name="A")
        b = make_player(full_name="B")
        with pytest.raises(IntegrityError):
            PlayerDuplicateCandidate.objects.create(
                candidate_player=a,
                canonical_player=b,
                score="0.500",
                feature_breakdown={},
                status="spam",
            )
