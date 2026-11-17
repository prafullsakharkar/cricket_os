"""Phase I Task 3: identity continuity across sources (the brief's acceptance).

The live provider (replay namespace) sends a player that the platform already
knows from the historical source (cricsheet). Linking the provider id onto the
existing entity must NOT create a duplicate player, must NOT split or alter
career statistics, and must be idempotent. A DoB-mismatched provider row must
create a stub that is NOT linked and NOT flagged as a duplicate candidate.

Ruling (SDD ledger, Task 3): career-stat rows are created directly on the
existing player (digest-carrying aggregate rows) rather than via the Phase F
pipeline, because the continuity assertion is about *identity mapping*, not
aggregation: the map row must point at the existing entity so its stat rows
stay untouched. The pipeline itself is covered by the Phase F suite.
"""

from __future__ import annotations

import datetime

import pytest

from apps.core.enums import TeamType
from apps.players.models import Player, PlayerDuplicateCandidate
from apps.providers.linking import (
    OUTCOME_ALREADY_LINKED,
    OUTCOME_AUTO_LINKED,
    OUTCOME_CANDIDATE_PENDING,
    OUTCOME_STUB_CREATED,
    link_entity,
)
from apps.providers.models import DataProvider, ProviderEntityMap
from apps.statistics.models import PlayerCareerStat
from apps.teams.models import Team, TeamMembership
from apps.venues.models import Country

pytestmark = pytest.mark.django_db

DOB = datetime.date(1988, 11, 5)
DOB_MISMATCH = datetime.date(1990, 6, 1)


def _seed_existing_player() -> dict[str, object]:
    """A cricsheet-imported player with country, team membership, map row and
    career stats — the canonical entity the replay provider must link onto."""
    from tests.phase_c.factories import make_player

    country, _ = Country.objects.get_or_create(
        iso_code="IN", defaults={"name": "India", "iso3": "IND"}
    )
    team = Team.objects.create(
        name="India", team_type=TeamType.INTERNATIONAL, country_id=country.id
    )
    cricsheet = DataProvider.objects.get(code="cricsheet")

    player = make_player(
        full_name="Virat Kohli",
        date_of_birth=DOB,
        gender="M",
        birth_country=country,
    )
    TeamMembership.objects.create(team=team, player=player)
    ProviderEntityMap.objects.create(
        provider=cricsheet,
        entity_type="player",
        internal_id=player.id,
        provider_entity_id="p-0001",
        verified=False,
    )
    PlayerCareerStat.objects.create(
        player=player,
        scope_type="CAREER",
        format_type="T20I",
        gender="M",
        matches=1,
        innings=1,
        runs=60,
        balls_faced=40,
        rule_version="1.0.0",
        inputs_digest="digest-c1",
    )
    PlayerCareerStat.objects.create(
        player=player,
        scope_type="FORMAT",
        format_type="T20I",
        gender="M",
        matches=1,
        innings=1,
        runs=60,
        balls_faced=40,
        rule_version="1.0.0",
        inputs_digest="digest-f1",
    )
    return {"player": player, "team": team, "country": country}


def _replay_provider() -> DataProvider:
    replay, _ = DataProvider.objects.get_or_create(code="replay", defaults={"name": "Replay"})
    return replay


def _matching_attributes(team: Team) -> dict[str, object]:
    return {
        "full_name": "Virat Kohli",
        "known_as": "Kohli",
        "unique_name": "V Kohli",
        "date_of_birth": DOB,
        "birth_country": "India",
        "gender": "M",
        "team_ids": [str(team.id)],
    }


def _career_snapshot(player: Player) -> list[dict[str, object]]:
    return sorted(
        (
            {
                "scope_type": row.scope_type,
                "format_type": row.format_type,
                "runs": row.runs,
                "balls_faced": row.balls_faced,
                "inputs_digest": row.inputs_digest,
            }
            for row in PlayerCareerStat.objects.filter(player=player).order_by("scope_type")
        ),
        key=lambda row: (row["scope_type"], row["format_type"]),
    )


def test_auto_link_keeps_one_player_and_identical_career_stats() -> None:
    seeded = _seed_existing_player()
    existing = seeded["player"]  # type: ignore[union-attr]
    replay = _replay_provider()

    outcome = link_entity(replay, "player", "live-0001", _matching_attributes(seeded["team"]))  # type: ignore[arg-type]

    assert outcome.outcome == OUTCOME_AUTO_LINKED
    assert outcome.internal_id == existing.id
    assert outcome.score is not None and outcome.score >= 0.95

    # Exactly one player for that person; no duplicate, no split career.
    players = Player.objects.filter(full_name="Virat Kohli", date_of_birth=DOB)
    assert players.count() == 1
    assert players.first().id == existing.id

    # Career stats identical row-for-row (digests equal).
    assert _career_snapshot(existing) == [
        {
            "scope_type": "CAREER",
            "format_type": "T20I",
            "runs": 60,
            "balls_faced": 40,
            "inputs_digest": "digest-c1",
        },
        {
            "scope_type": "FORMAT",
            "format_type": "T20I",
            "runs": 60,
            "balls_faced": 40,
            "inputs_digest": "digest-f1",
        },
    ]

    # The map row points at the existing entity, verified=False (steward review).
    map_row = ProviderEntityMap.objects.get(
        provider=replay, entity_type="player", provider_entity_id="live-0001"
    )
    assert map_row.internal_id == existing.id
    assert map_row.verified is False
    assert map_row.confidence is not None and float(map_row.confidence) >= 0.95


def test_repeat_link_is_idempotent_noop() -> None:
    seeded = _seed_existing_player()
    existing = seeded["player"]  # type: ignore[union-attr]
    replay = _replay_provider()

    first = link_entity(replay, "player", "live-0001", _matching_attributes(seeded["team"]))  # type: ignore[arg-type]
    second = link_entity(replay, "player", "live-0001", _matching_attributes(seeded["team"]))  # type: ignore[arg-type]

    assert first.outcome == OUTCOME_AUTO_LINKED
    assert second.outcome == OUTCOME_ALREADY_LINKED
    assert second.internal_id == existing.id
    assert (
        ProviderEntityMap.objects.filter(
            provider=replay, entity_type="player", provider_entity_id="live-0001"
        ).count()
        == 1
    )
    assert Player.objects.filter(full_name="Virat Kohli", date_of_birth=DOB).count() == 1


def test_dob_mismatch_creates_stub_without_link_or_candidate() -> None:
    seeded = _seed_existing_player()
    existing = seeded["player"]  # type: ignore[union-attr]
    replay = _replay_provider()
    attrs = _matching_attributes(seeded["team"])  # type: ignore[arg-type]
    attrs["date_of_birth"] = DOB_MISMATCH

    outcome = link_entity(replay, "player", "live-0009", attrs)

    # Stub created, NOT linked to the existing player.
    assert outcome.outcome == OUTCOME_STUB_CREATED
    stub = Player.objects.get(id=outcome.internal_id)
    assert stub.id != existing.id

    # The DoB mismatch caps/guards the pair: no duplicate candidate flagged.
    assert not PlayerDuplicateCandidate.objects.filter(candidate_player=stub).exists()
    assert (
        ProviderEntityMap.objects.get(
            provider=replay, entity_type="player", provider_entity_id="live-0009"
        ).internal_id
        == stub.id
    )

    # The existing player's career rows are untouched.
    assert _career_snapshot(existing) == [
        {
            "scope_type": "CAREER",
            "format_type": "T20I",
            "runs": 60,
            "balls_faced": 40,
            "inputs_digest": "digest-c1",
        },
        {
            "scope_type": "FORMAT",
            "format_type": "T20I",
            "runs": 60,
            "balls_faced": 40,
            "inputs_digest": "digest-f1",
        },
    ]
    assert Player.objects.filter(full_name="Virat Kohli", date_of_birth=DOB).count() == 1


def test_near_duplicate_in_review_band_is_candidate_pending() -> None:
    """A stub whose best candidate is in [0.60, 0.95) is flagged, not linked."""
    seeded = _seed_existing_player()
    existing = seeded["player"]  # type: ignore[union-attr]
    replay = _replay_provider()

    # Name matches, but the DoB is missing so the resolver does not filter on
    # it and the score lands below 0.95 (name 1.0 + dob unknown 0.5 + country
    # 1.0 + gender 1.0 + teams 1.0 = 0.35+0.125+0.15+0.10+0.15 = 0.875 < 0.95),
    # while the name+teams evidence keeps the pair above the 0.60 band.
    attrs = {
        "full_name": "Virat Kohli",
        "birth_country": "India",
        "gender": "M",
        "team_ids": [str(seeded["team"].id)],  # type: ignore[union-attr]
    }
    outcome = link_entity(replay, "player", "live-0008", attrs)

    assert outcome.outcome == OUTCOME_CANDIDATE_PENDING
    stub = Player.objects.get(id=outcome.internal_id)
    assert stub.id != existing.id
    flagged = PlayerDuplicateCandidate.objects.filter(candidate_player=stub, status="open")
    assert flagged.count() >= 1
    assert flagged.first().canonical_player_id == existing.id
