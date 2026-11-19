"""Phase I Task 4 (4c): selector exclusion, 301 redirect, sweep, admin, matrix.

* merged-away matches are invisible to every match feed, the poll dispatcher
  and the statistics sweep;
* a read of a merged match's id (or any sub-path) answers 301 to the canonical;
* the duplicate-candidate admin actions confirm/reject and the merge ledger's
  split action reverse a merge;
* edge-case matrix: double-headers, UTC-midnight/date-line normalisation,
  super-over and abandoned/terminal matches, plus Hypothesis order-independence
  and idempotence properties (run with ``--hypothesis-profile=ci``).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, timedelta
from datetime import datetime as dt

import pytest
from django.contrib.auth.models import User
from django.db import transaction
from django.test import Client
from django.utils import timezone
from hypothesis import given, settings
from hypothesis import strategies as st
from tests.phase_c.factories import (
    make_innings,
    make_match,
    make_match_teams,
    make_player,
    make_provider,
    make_team,
)

from apps.administration.models import AuditLog
from apps.core import events
from apps.core.enums import MatchStatus
from apps.live.services import polling
from apps.matches.models import Match, MatchDuplicateCandidate, MatchMerge, MatchResult
from apps.matches.selectors.reads import (
    list_all_matches,
    list_live_matches,
    list_recent_matches,
    list_upcoming_matches,
)
from apps.matches.services import merge
from apps.providers.models import ProviderEntityMap
from apps.statistics.models import StatisticsWatermark
from apps.statistics.services import recompute
from apps.synchronization.services import fixtures

pytestmark = pytest.mark.django_db

_DAY = dt(2026, 10, 25, 9, 0, tzinfo=UTC)


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


def _pair(*, status: str = MatchStatus.COMPLETED) -> tuple[Match, Match, object, object]:
    india = make_team("India")
    australia = make_team("Australia")
    kwargs: dict[str, object] = {"start_time_utc": _DAY, "match_number": 1, "status": status}
    if status not in (MatchStatus.SCHEDULED, MatchStatus.POSTPONED):
        kwargs["end_time_utc"] = timezone.now()
    canonical = make_match(**kwargs)
    merged = make_match(**kwargs)
    make_match_teams(canonical, india, australia)
    make_match_teams(merged, india, australia)
    return canonical, merged, india, australia


@pytest.fixture
def admin_user() -> User:
    return User.objects.create_superuser(username="admin", password="pw", email="admin@example.com")


@pytest.fixture
def client(admin_user: User) -> Client:
    client = Client()
    client.force_login(admin_user)
    return client


# --------------------------------------------------------------------------- #
# Selector exclusion
# --------------------------------------------------------------------------- #


class TestSelectorExclusion:
    def test_upcoming_feed_excludes_merged(self) -> None:
        canonical, merged, _, _ = _pair(status=MatchStatus.SCHEDULED)
        start = timezone.now() + timedelta(days=1)
        Match.objects.filter(id__in=[canonical.id, merged.id]).update(start_time_utc=start)
        merge.merge_matches(merged, canonical, actor="steward-1")

        upcoming = list_upcoming_matches(days=7)
        ids = {m.id for m in upcoming}
        assert canonical.id in ids
        assert merged.id not in ids
        assert list_all_matches().filter(id=merged.id).count() == 0

    def test_live_feed_and_poll_dispatcher_exclude_merged(self) -> None:
        canonical, merged, _, _ = _pair(status=MatchStatus.IN_PROGRESS)
        merge.merge_matches(merged, canonical, actor="steward-1")

        live_ids = {m.id for m in list_live_matches()}
        assert canonical.id in live_ids
        assert merged.id not in live_ids
        poll_ids = {m.id for m in polling.live_matches()}
        assert merged.id not in poll_ids

    def test_recent_feed_excludes_merged(self) -> None:
        canonical, merged, _, _ = _pair(status=MatchStatus.COMPLETED)
        merge.merge_matches(merged, canonical, actor="steward-1")
        recent_ids = {m.id for m in list_recent_matches(days=30)}
        assert canonical.id in recent_ids
        assert merged.id not in recent_ids

    def test_stats_sweep_excludes_merged_matches(self) -> None:
        canonical, merged, india, _ = _pair(status=MatchStatus.COMPLETED)
        player = make_player("Sweep Player")
        from apps.statistics.models import PlayerMatchStat

        for match in (canonical, merged):
            PlayerMatchStat.objects.create(
                match=match,
                player=player,
                team=india,
                did_bat=True,
                batting=[],
                bowling=[],
            )
        merge.merge_matches(merged, canonical, actor="steward-1")
        # Both watermarks are stale; the sweep must still leave the merged
        # match untouched (excluded by merged_into_id).
        StatisticsWatermark.objects.filter(match_id__in=[canonical.id, merged.id]).update(
            status="pending"
        )

        count = recompute.sweep_stale_statistics()

        # Only the canonical was swept: the merged-away match is excluded from
        # the sweep inputs entirely.
        assert count == 1
        assert StatisticsWatermark.objects.filter(match_id=canonical.id, status="done").exists()
        assert StatisticsWatermark.objects.filter(match_id=merged.id, status="pending").exists()


# --------------------------------------------------------------------------- #
# 301 redirect
# --------------------------------------------------------------------------- #


class TestMatchRedirect:
    def test_merged_match_detail_answers_301(self, api_client: object) -> None:
        canonical, merged, _, _ = _pair()
        merge.merge_matches(merged, canonical, actor="steward-1")

        response = api_client.get(f"/api/v1/matches/{merged.id}/")  # type: ignore[union-attr]
        assert response.status_code == 301
        assert response["Location"] == f"/api/v1/matches/{canonical.id}/"

    def test_subpath_redirects_preserve_the_suffix(self, api_client: object) -> None:
        canonical, merged, _, _ = _pair()
        merge.merge_matches(merged, canonical, actor="steward-1")

        response = api_client.get(  # type: ignore[union-attr]
            f"/api/v1/matches/{merged.id}/scorecard"
        )
        assert response.status_code == 301
        assert response["Location"] == f"/api/v1/matches/{canonical.id}/scorecard"

    def test_live_match_detail_is_200(self, api_client: object) -> None:
        canonical, _merged, _, _ = _pair()
        response = api_client.get(f"/api/v1/matches/{canonical.id}/")  # type: ignore[union-attr]
        assert response.status_code == 200
        assert response.json()["data"]["id"] == str(canonical.id)


# --------------------------------------------------------------------------- #
# Admin review actions
# --------------------------------------------------------------------------- #


class TestAdminActions:
    def test_confirm_merge_flow(self, client: Client) -> None:
        canonical, candidate, _, _ = _pair()
        dup = MatchDuplicateCandidate.objects.create(
            candidate_match=candidate,
            canonical_match=canonical,
            score="0.850",
        )
        response = client.post(
            "/admin/matches/matchduplicatecandidate/",
            {
                "action": "confirm_match_merge",
                "_selected_action": [str(dup.pk)],
                "index": "0",
                "confirm": "yes",
            },
        )
        assert response.status_code == 302
        dup.refresh_from_db()
        assert dup.status == "resolved"
        assert dup.resolved_at is not None
        merge_row = MatchMerge.objects.get()
        assert merge_row.merged_match_id == candidate.id
        assert merge_row.canonical_match_id == canonical.id
        candidate.refresh_from_db()
        assert candidate.merged_into_id == canonical.id
        assert AuditLog.objects.filter(action=events.TOPIC_MATCH_MERGED).count() == 1
        assert AuditLog.objects.filter(action=events.TOPIC_MATCH_DUP_REVIEWED).count() == 1

    def test_confirm_merge_confirmation_page_shows_rows(self, client: Client) -> None:
        canonical, candidate, _, _ = _pair()
        dup = MatchDuplicateCandidate.objects.create(
            candidate_match=candidate,
            canonical_match=canonical,
            score="0.850",
        )
        response = client.post(
            "/admin/matches/matchduplicatecandidate/",
            {
                "action": "confirm_match_merge",
                "_selected_action": [str(dup.pk)],
                "index": "0",
            },
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert "Confirm match merges" in content
        assert "score 0.850" in content

    def test_reject_candidate_flow(self, client: Client) -> None:
        canonical, candidate, _, _ = _pair()
        dup = MatchDuplicateCandidate.objects.create(
            candidate_match=candidate,
            canonical_match=canonical,
            score="0.850",
        )
        response = client.post(
            "/admin/matches/matchduplicatecandidate/",
            {
                "action": "reject_match_candidate",
                "_selected_action": [str(dup.pk)],
                "index": "0",
                "confirm": "yes",
            },
        )
        assert response.status_code == 302
        dup.refresh_from_db()
        assert dup.status == "rejected"
        assert MatchMerge.objects.count() == 0  # nothing merged
        assert AuditLog.objects.filter(action=events.TOPIC_MATCH_DUP_REVIEWED).count() == 1
        assert AuditLog.objects.get(action=events.TOPIC_MATCH_DUP_REVIEWED).after["decision"] == (
            "rejected"
        )

    def test_split_action_flow(self, client: Client) -> None:
        canonical, merged, _, _ = _pair()
        merge_row = merge.merge_matches(merged, canonical, actor="steward-1")
        response = client.post(
            "/admin/matches/matchmerge/",
            {
                "action": "split_match",
                "_selected_action": [str(merge_row.pk)],
                "index": "0",
                "confirm": "yes",
            },
        )
        assert response.status_code == 302
        merge_row.refresh_from_db()
        assert merge_row.status == "split"
        merged.refresh_from_db()
        assert merged.merged_into_id is None
        assert AuditLog.objects.filter(action=events.TOPIC_MATCH_SPLIT).count() == 1


# --------------------------------------------------------------------------- #
# Edge-case matrix
# --------------------------------------------------------------------------- #


class TestDoubleHeader:
    def test_double_header_with_differing_match_number_is_queued_not_collapsed(self) -> None:
        cricsheet = make_provider("cricsheet")
        live = make_provider("live")
        fixtures.sync_fixtures(cricsheet, [_record("c-1", match_number=1)])
        report = fixtures.sync_fixtures(live, [_record("live-1", match_number=2)])

        # Distinct rows + a steward-review candidate (never silently merged).
        assert report.created == 1
        assert report.candidates == 1
        assert Match.objects.count() == 2
        dup = MatchDuplicateCandidate.objects.get()
        assert dup.status == "open"
        assert dup.candidate_match.provider_match_id == "live-1"
        assert dup.canonical_match.provider_match_id == "c-1"
        assert float(dup.score) == pytest.approx(0.85)

    def test_double_header_beyond_tolerance_is_a_miss(self) -> None:
        cricsheet = make_provider("cricsheet")
        live = make_provider("live")
        fixtures.sync_fixtures(cricsheet, [_record("c-1", match_number=1)])
        report = fixtures.sync_fixtures(
            live,
            [
                _record(
                    "live-1",
                    match_number=2,
                    start_time_utc=_DAY + timedelta(hours=5),  # > 180 min tolerance
                )
            ],
        )
        assert report.created == 1
        assert report.canonicalised == 0
        assert Match.objects.count() == 2
        assert MatchDuplicateCandidate.objects.count() == 0


class TestTimezoneAndDateLine:
    def test_starts_on_either_side_of_utc_midnight_share_a_key(self) -> None:
        cricsheet = make_provider("cricsheet")
        live = make_provider("live")
        fixtures.sync_fixtures(
            cricsheet, [_record("c-1", start_time_utc=dt(2026, 10, 25, 23, 30, tzinfo=UTC))]
        )
        report = fixtures.sync_fixtures(
            live, [_record("live-1", start_time_utc=dt(2026, 10, 26, 0, 30, tzinfo=UTC))]
        )
        assert report.canonicalised == 1
        assert Match.objects.count() == 1
        # The owner's fixture id lives natively on the match row; the incoming
        # provider's id is mapped (one map row).
        assert ProviderEntityMap.objects.filter(entity_type="match").count() == 1

    def test_local_dates_differ_but_utc_start_is_identical(self) -> None:
        from apps.matches.services.canonicalise import canonical_key

        key_a = canonical_key(
            series_or_competition_id=None,
            team_ids=frozenset(),
            start_time_utc=dt(2026, 10, 25, 20, 0, tzinfo=UTC),
        )
        key_b = canonical_key(
            series_or_competition_id=None,
            team_ids=frozenset(),
            start_time_utc=dt(2026, 10, 25, 20, 30, tzinfo=UTC),
        )
        assert key_a["start_bucket"] == key_b["start_bucket"]

    def test_naive_naive_aware_mix_is_normalised(self) -> None:
        from apps.matches.services.canonicalise import canonical_key

        key = canonical_key(
            series_or_competition_id=None,
            team_ids=frozenset(),
            start_time_utc=dt(2026, 10, 25, 9, 0),  # naive
        )
        assert key["start_time_utc"].tzinfo is not None


class TestSuperOverAndAbandoned:
    def test_canonicalise_then_merge_keeps_super_over_rows(self) -> None:
        canonical, merged, india, australia = _pair(status=MatchStatus.COMPLETED)
        make_innings(canonical, india, australia, innings_number=1)
        make_innings(merged, india, australia, innings_number=1)
        merged_super = make_innings(
            merged, india, australia, innings_number=1, is_super_over=True, super_over_number=1
        )

        merge_row = merge.merge_matches(merged, canonical, actor="steward-1")
        # The super-over innings moved; the conflicting normal innings stayed.
        assert merged_super.id in canonical.innings.values_list("id", flat=True)
        assert merged.innings.filter(is_super_over=False).count() == 1

        merge.split_match(merge_row, actor="steward-1")
        assert merged_super.id in merged.innings.values_list("id", flat=True)
        assert merged_super.is_super_over is True

    def test_terminal_merge_keeps_no_result_row(self) -> None:
        canonical, merged, _, _ = _pair(status=MatchStatus.ABANDONED)
        MatchResult.objects.create(match=merged, result_type="NO_RESULT", winner_team=None)
        merge.merge_matches(merged, canonical, actor="steward-1")
        result = MatchResult.objects.get(match_id=canonical.id)
        assert result.result_type == "NO_RESULT"
        assert not MatchResult.objects.filter(match_id=merged.id).exists()

    def test_abandoned_existing_match_map_row_is_not_primary(self) -> None:
        _canonical, _merged, india, australia = _pair(status=MatchStatus.ABANDONED)
        live = make_provider("live")
        from apps.matches.services.canonicalise import find_canonical_match

        result = find_canonical_match(
            live,
            {
                "provider_match_id": "live-1",
                "team_ids": {india.id, australia.id},  # type: ignore[union-attr]
                "start_time_utc": _DAY,
                "match_number": 1,
            },
        )
        assert result.decision == "canonical"
        row = ProviderEntityMap.objects.get(provider=live, entity_type="match")
        assert row.is_primary is False  # terminal -> completed-card precedence (4c)


# --------------------------------------------------------------------------- #
# Hypothesis properties (run with --hypothesis-profile=ci)
# --------------------------------------------------------------------------- #


class TestProperties:
    @given(
        offset_minutes=st.integers(min_value=-170, max_value=170),
        flipped=st.booleans(),
    )
    @settings(max_examples=30, deadline=None)
    def test_canonicalise_order_independent_and_idempotent(
        self, offset_minutes: int, flipped: bool
    ) -> None:
        with transaction.atomic():
            try:
                start = _DAY + timedelta(minutes=offset_minutes)
                prov_a = make_provider(f"pa{offset_minutes}")
                prov_b = make_provider(f"pb{offset_minutes}")
                rec_a = _record("a-1", start_time_utc=start, match_number=1)
                rec_b = _record("b-1", start_time_utc=start + timedelta(minutes=1), match_number=1)
                if flipped:
                    first_provider, second_provider = prov_b, prov_a
                    first_record, second_record = rec_b, rec_a
                else:
                    first_provider, second_provider = prov_a, prov_b
                    first_record, second_record = rec_a, rec_b

                first = fixtures.sync_fixtures(first_provider, [first_record])
                assert first.created == 1
                second = fixtures.sync_fixtures(second_provider, [second_record])
                assert second.canonicalised == 1
                # Either arrival order ends with one canonical match. The owner's
                # fixture id is native on the match row, so only the
                # canonicalising provider carries a map row (ruling 4c-4).
                assert Match.objects.count() == 1
                assert ProviderEntityMap.objects.filter(entity_type="match").count() == 1

                # Idempotence: re-syncing the second provider updates the same
                # map row (no duplicates) and reports canonicalised=1 — the
                # fixture was still resolved against the canonical.
                repeat = fixtures.sync_fixtures(second_provider, [second_record])
                assert repeat.canonicalised == 1
                assert repeat.unchanged == 0
                assert Match.objects.count() == 1
                assert ProviderEntityMap.objects.filter(entity_type="match").count() == 1
            except BaseException:
                transaction.set_rollback(True)
                raise
            else:
                transaction.set_rollback(True)

    @given(
        offset_minutes=st.integers(min_value=-170, max_value=170),
    )
    @settings(max_examples=20, deadline=None)
    def test_candidate_band_is_stable_across_arrival_orders(self, offset_minutes: int) -> None:
        with transaction.atomic():
            try:
                start = _DAY + timedelta(minutes=offset_minutes)
                prov_a = make_provider(f"ca{offset_minutes}")
                prov_b = make_provider(f"cb{offset_minutes}")
                rec_a = _record("a-1", start_time_utc=start, match_number=1)
                rec_b = _record("b-1", start_time_utc=start + timedelta(minutes=1), match_number=2)

                fixtures.sync_fixtures(prov_a, [rec_a])
                second = fixtures.sync_fixtures(prov_b, [rec_b])
                assert second.candidates == 1
                assert Match.objects.count() == 2
                dup = MatchDuplicateCandidate.objects.get()
                assert dup.status == "open"
                assert float(dup.score) == pytest.approx(0.85)
                # Never auto-merged below the auto threshold.
                assert MatchMerge.objects.count() == 0
            except BaseException:
                transaction.set_rollback(True)
                raise
            else:
                transaction.set_rollback(True)
