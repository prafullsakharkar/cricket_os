"""Phase I Task 4 (4b): the canonicalisation service + ingest-time hooks.

* ``canonical_key`` buckets start times on the tolerance grid;
* ``score_candidate``: 1.0 full agreement, 0.0 hard gates (teams / series /
  start-outside-tolerance), partial credit for venue-missing / tie-breaker
  mismatch;
* ``find_canonical_match``: canonical (map row, no new match) / candidate
  (review queue) / miss decisions;
* fixture sync consults the service before creating a ``Match`` (no duplicate
  rows; a cross-provider fixture maps onto the canonical match);
* polling resolves the incoming provider's own fixture id via the map row.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, timedelta
from datetime import datetime as dt

import pytest
from tests.phase_c.factories import make_match, make_match_teams, make_provider, make_team

from apps.core.enums import MatchStatus
from apps.matches.models import Match
from apps.matches.services import canonicalise
from apps.providers.models import ProviderEntityMap
from apps.synchronization.services import fixtures

pytestmark = pytest.mark.django_db

_DAY = dt(2026, 10, 25, 9, 0, tzinfo=UTC)


def _signals(team_ids=None, **overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "team_ids": frozenset(team_ids or ()),
        "start_time_utc": _DAY,
    }
    base.update(overrides)
    return base


def _two_teams() -> tuple[object, object, object]:
    india = make_team("India")
    australia = make_team("Australia")
    match = make_match(start_time_utc=_DAY, match_number=1)
    make_match_teams(match, india, australia)  # type: ignore[arg-type]
    return india, australia, match


def _record(provider_match_id: str = "live-1", **overrides: object) -> fixtures.FixtureRecord:
    base = fixtures.FixtureRecord(
        provider_match_id=provider_match_id,
        format_type="T20I",
        start_time_utc=_DAY,
        teams=("India", "Australia"),
        team_registry={
            "India": {"key_opta": "ind-1"},
            "Australia": {"key_opta": "aus-1"},
        },
        match_number=1,
    )
    return replace(base, **overrides)  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# canonical_key
# --------------------------------------------------------------------------- #


class TestCanonicalKey:
    def test_start_times_within_tolerance_share_a_bucket(self) -> None:
        key_a = canonicalise.canonical_key(
            series_or_competition_id=None,
            team_ids=frozenset(),
            start_time_utc=_DAY,
        )
        key_b = canonicalise.canonical_key(
            series_or_competition_id=None,
            team_ids=frozenset(),
            start_time_utc=_DAY + timedelta(hours=2),
        )
        assert key_a["start_bucket"] == key_b["start_bucket"]

    def test_start_times_beyond_tolerance_bucket_differently(self) -> None:
        key_a = canonicalise.canonical_key(
            series_or_competition_id=None,
            team_ids=frozenset(),
            start_time_utc=_DAY,
        )
        key_b = canonicalise.canonical_key(
            series_or_competition_id=None,
            team_ids=frozenset(),
            start_time_utc=_DAY + timedelta(days=1),
        )
        assert key_a["start_bucket"] != key_b["start_bucket"]

    def test_team_ids_are_order_insensitive(self) -> None:
        india, australia, _ = _two_teams()
        a = canonicalise.canonical_key(
            series_or_competition_id=None,
            team_ids=frozenset({india.id, australia.id}),
            start_time_utc=_DAY,
        )
        b = canonicalise.canonical_key(
            series_or_competition_id=None,
            team_ids=frozenset({australia.id, india.id}),
            start_time_utc=_DAY,
        )
        assert a["team_ids"] == b["team_ids"]


# --------------------------------------------------------------------------- #
# score_candidate
# --------------------------------------------------------------------------- #


class TestScoreCandidate:
    def test_full_agreement_scores_one(self) -> None:
        india, australia, match = _two_teams()
        score, breakdown = canonicalise.score_candidate(
            match,
            _signals(team_ids={india.id, australia.id}),  # type: ignore[union-attr]
        )
        assert score == 1.0
        assert breakdown["teams"] == 1.0

    def test_different_teams_score_zero(self) -> None:
        india, _australia, match = _two_teams()
        england = make_team("England")
        score, breakdown = canonicalise.score_candidate(
            match,
            _signals(team_ids={india.id, england.id}),  # type: ignore[union-attr]
        )
        assert score == 0.0
        assert breakdown["teams"] == 0.0

    def test_different_series_scores_zero(self) -> None:
        from apps.competitions.models import Series

        india, australia, match = _two_teams()
        series = Series.objects.create(name="T20 World Cup")
        match.series_id = series.id
        match.save(update_fields=["series_id"])
        other_series = object()
        score, breakdown = canonicalise.score_candidate(
            match,
            _signals(
                team_ids={india.id, australia.id},  # type: ignore[union-attr]
                series_or_competition_id=other_series,
            ),
        )
        assert score == 0.0
        assert breakdown["series"] == 0.0

    def test_start_beyond_tolerance_scores_zero(self) -> None:
        india, australia, match = _two_teams()
        score, breakdown = canonicalise.score_candidate(
            match,
            _signals(
                team_ids={india.id, australia.id},  # type: ignore[union-attr]
                start_time_utc=_DAY + timedelta(hours=4),  # > 180 min tolerance
            ),
        )
        assert score == 0.0
        assert breakdown["start_time"] == 0.0

    def test_venue_missing_on_one_side_is_partial(self) -> None:
        india, australia, match = _two_teams()
        venue = make_venue()
        match.venue_id = venue.id
        match.save(update_fields=["venue_id"])
        # Incoming side has no venue -> partial credit, not a hard miss.
        score, breakdown = canonicalise.score_candidate(
            match,
            _signals(team_ids={india.id, australia.id}),  # type: ignore[union-attr]
        )
        assert score == pytest.approx(0.90)
        assert breakdown["venue"] == pytest.approx(0.90)

    def test_match_number_mismatch_is_partial(self) -> None:
        india, australia, match = _two_teams()
        score, breakdown = canonicalise.score_candidate(
            match,
            _signals(
                team_ids={india.id, australia.id},  # type: ignore[union-attr]
                match_number=2,  # existing match has match_number=1
            ),
        )
        assert score == pytest.approx(0.85)
        assert breakdown["match_number"] == pytest.approx(0.85)


def make_venue() -> object:
    from apps.venues.models import Venue

    country, _ = get_or_create_country()
    return Venue.objects.create(name="MCG", country=country)


def get_or_create_country():
    from apps.venues.models import Country

    return Country.objects.get_or_create(
        iso_code="AU", defaults={"name": "Australia", "iso3": "AUS"}
    )


# --------------------------------------------------------------------------- #
# find_canonical_match
# --------------------------------------------------------------------------- #


class TestFindCanonicalMatch:
    def test_strong_hit_returns_canonical_and_writes_map_row(self) -> None:
        india, australia, match = _two_teams()
        provider = make_provider("live")
        result = canonicalise.find_canonical_match(
            provider,
            {
                "provider_match_id": "live-1",
                "team_ids": {india.id, australia.id},  # type: ignore[union-attr]
                "start_time_utc": _DAY,
                "match_number": 1,
            },
        )
        assert result.decision == canonicalise.DECISION_CANONICAL
        assert result.canonical_match.id == match.id
        assert result.score == 1.0

        row = ProviderEntityMap.objects.get(
            provider=provider, entity_type="match", provider_entity_id="live-1"
        )
        assert row.internal_id == match.id
        assert row.is_primary is True  # scheduled -> live provider authoritative
        assert Match.objects.count() == 1

    def test_terminal_canonical_map_row_is_not_primary(self) -> None:
        india, australia, match = _two_teams()
        match.status = MatchStatus.COMPLETED
        match.save(update_fields=["status"])
        provider = make_provider("live")
        canonicalise.find_canonical_match(
            provider,
            {
                "provider_match_id": "live-1",
                "team_ids": {india.id, australia.id},  # type: ignore[union-attr]
                "start_time_utc": _DAY,
            },
        )
        row = ProviderEntityMap.objects.get(
            provider=provider, entity_type="match", provider_entity_id="live-1"
        )
        assert row.is_primary is False  # completed-card precedence is 4c's job

    def test_review_band_returns_candidate_with_canonical(self) -> None:
        from apps.matches.models import MatchDuplicateCandidate

        india, australia, match = _two_teams()
        provider = make_provider("live")
        # A tie-breaker mismatch drops the score to 0.85 -> review band.
        result = canonicalise.find_canonical_match(
            provider,
            {
                "provider_match_id": "live-2",
                "team_ids": {india.id, australia.id},  # type: ignore[union-attr]
                "start_time_utc": _DAY,
                "match_number": 2,
            },
        )
        assert result.decision == canonicalise.DECISION_CANDIDATE
        assert result.canonical_match.id == match.id
        # No map row written for a review-band fixture.
        assert not ProviderEntityMap.objects.filter(provider=provider, entity_type="match").exists()
        assert MatchDuplicateCandidate.objects.count() == 0  # flagged after the stub exists

    def test_no_plausible_match_is_a_miss(self) -> None:
        india, _australia, _ = _two_teams()
        england = make_team("England")
        provider = make_provider("live")
        result = canonicalise.find_canonical_match(
            provider,
            {
                "provider_match_id": "live-3",
                "team_ids": {india.id, england.id},  # type: ignore[union-attr]
                "start_time_utc": _DAY,
            },
        )
        assert result.decision == canonicalise.DECISION_MISS
        assert result.canonical_match is None
        assert not ProviderEntityMap.objects.filter(provider=provider).exists()


def test_flag_duplicate_candidate_is_idempotent() -> None:
    from apps.matches.models import MatchDuplicateCandidate

    india, australia, match = _two_teams()
    stub = make_match(start_time_utc=_DAY + timedelta(hours=1), match_number=1)
    make_match_teams(stub, india, australia)  # type: ignore[arg-type]

    first = canonicalise.flag_duplicate_candidate(
        stub, match, score=0.85, breakdown={"match_number": 0.85}
    )
    second = canonicalise.flag_duplicate_candidate(
        stub, match, score=0.85, breakdown={"match_number": 0.85}
    )
    assert first.id == second.id
    assert MatchDuplicateCandidate.objects.count() == 1
    assert float(MatchDuplicateCandidate.objects.get().score) == pytest.approx(0.850)


# --------------------------------------------------------------------------- #
# Fixture-sync hook
# --------------------------------------------------------------------------- #


class TestFixtureSyncHook:
    def test_same_fixture_from_second_provider_canonicalises(self) -> None:
        cricsheet = make_provider("cricsheet")
        live = make_provider("live")

        first = fixtures.sync_fixtures(cricsheet, [_record("c-1")])
        assert first.created == 1

        second = fixtures.sync_fixtures(live, [_record("live-1")])
        assert second.created == 0
        assert second.canonicalised == 1

        # One Match row, owned by cricsheet; the live id maps onto it.
        assert Match.objects.count() == 1
        canonical = Match.objects.get(provider_match_id="c-1")
        row = ProviderEntityMap.objects.get(
            provider=live, entity_type="match", provider_entity_id="live-1"
        )
        assert row.internal_id == canonical.id

    def test_review_band_fixture_creates_stub_and_candidate(self) -> None:
        from apps.matches.models import MatchDuplicateCandidate

        cricsheet = make_provider("cricsheet")
        live = make_provider("live")

        fixtures.sync_fixtures(cricsheet, [_record("c-1")])
        # match_number differs (2 vs 1) -> 0.85 review band.
        report = fixtures.sync_fixtures(live, [_record("live-1", match_number=2)])

        assert report.created == 1
        assert report.candidates == 1
        assert Match.objects.count() == 2
        dup = MatchDuplicateCandidate.objects.get()
        assert dup.status == "open"
        assert dup.canonical_match.provider_match_id == "c-1"
        assert dup.candidate_match.provider_match_id == "live-1"
        assert float(dup.score) == pytest.approx(0.85)

    def test_repeat_sync_of_same_provider_is_unchanged_not_canonicalised(self) -> None:
        provider = make_provider("replay")
        first = fixtures.sync_fixtures(provider, [_record("p-1")])
        second = fixtures.sync_fixtures(provider, [_record("p-1")])
        assert first.created == 1
        assert second.canonicalised == 0
        assert second.unchanged == 1
        assert Match.objects.filter(provider_match_id="p-1").count() == 1


# --------------------------------------------------------------------------- #
# Polling hook
# --------------------------------------------------------------------------- #


def test_provider_match_id_for_resolves_canonicalised_match() -> None:
    cricsheet = make_provider("cricsheet")
    live = make_provider("live")
    fixtures.sync_fixtures(cricsheet, [_record("c-1")])
    fixtures.sync_fixtures(live, [_record("live-1")])

    canonical = Match.objects.get(provider_match_id="c-1")
    # The canonical row's own id is the cricsheet one; the live provider's id
    # comes from its map row.
    assert canonicalise.provider_match_id_for(canonical, "live") == "live-1"
    assert canonicalise.provider_match_id_for(canonical, None) == "c-1"
    assert canonicalise.provider_match_id_for(canonical, "cricsheet") == "c-1"


def test_dispatch_polls_with_adapter_uses_incoming_provider_match_id() -> None:
    from apps.live.services.polling import dispatch_polls

    cricsheet = make_provider("cricsheet")
    live = make_provider("live")
    fixtures.sync_fixtures(cricsheet, [_record("c-1")])
    fixtures.sync_fixtures(live, [_record("live-1")])

    canonical = Match.objects.get(provider_match_id="c-1")
    canonical.status = MatchStatus.IN_PROGRESS
    canonical.save(update_fields=["status"])

    seen: list[str] = []

    class StubLiveAdapter:
        def iter_deliveries(self, provider_match_id: str, since_seq: int | None) -> list[object]:
            seen.append(provider_match_id)
            return []

    reports = dispatch_polls(adapter=StubLiveAdapter(), provider=live, max_matches=1)
    assert len(reports) == 1
    assert reports[0].match_id == str(canonical.id)
    assert seen == ["live-1"]  # the live provider's own fixture id, not "c-1"
