"""Phase D: live fixture sync (DATA_INGESTION §2, DOMAIN_MODEL §6.6).

Covers the ``synchronization.services.fixtures`` upsert:

* create (matches + teams), idempotent re-sync, reschedule → ``match.updated``,
  cancellation → lifecycle transition, never delete, no status regression;
* per-record error resilience + the redacted DLQ reporter;
* the ``apps.live`` orchestration (``run_fixture_sync``), normalization shapes
  and the Celery beat task gate.
"""

from __future__ import annotations

import datetime
from dataclasses import replace

import pytest
from django.utils import timezone

from apps.core.enums import MatchStatus
from apps.ingestion.models import IngestionError
from apps.matches import event_schemas
from apps.matches.models import Match, MatchEventLog, MatchTeam
from apps.synchronization.services import fixtures

from .test_live_apply import make_context  # noqa: F401  (shared factory imports)

pytestmark = pytest.mark.django_db


def _provider():
    from tests.phase_c.factories import make_provider

    return make_provider("replay")


#: Fixed kickoff so two ``_record`` calls compare equal (a fresh ``now()``
#: would look like a reschedule on every re-sync).
_BASE_START = timezone.datetime(2026, 12, 1, 10, 0, tzinfo=datetime.UTC)


def _record(provider_match_id: str = "p-1", **overrides: object) -> fixtures.FixtureRecord:
    base = fixtures.FixtureRecord(
        provider_match_id=provider_match_id,
        format_type="T20",
        start_time_utc=_BASE_START,
        teams=("Team Alpha", "Team Beta"),
        team_registry={"Team Alpha": {"key_opta": "alpha-1"}, "Team Beta": {"key_opta": "beta-1"}},
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Upsert semantics
# --------------------------------------------------------------------------- #


def test_sync_creates_matches_and_teams() -> None:
    provider = _provider()
    report = fixtures.sync_fixtures(provider, [_record("p-1"), _record("p-2")])
    assert report.created == 2
    assert report.updated == 0

    match = Match.objects.get(source_provider_id=provider.id, provider_match_id="p-1")
    assert match.status == MatchStatus.SCHEDULED
    assert match.format_type == "T20"
    assert match.revision == 1
    teams = list(match.teams.order_by("team_number"))
    assert [t.team.name for t in teams] == ["Team Alpha", "Team Beta"]
    assert [t.team_number for t in teams] == [1, 2]
    assert MatchTeam.objects.filter(match=match).count() == 2


def test_repeat_sync_is_idempotent_unchanged() -> None:
    provider = _provider()
    fixtures.sync_fixtures(provider, [_record("p-1")])
    report = fixtures.sync_fixtures(provider, [_record("p-1")])
    assert report.created == 0
    assert report.updated == 0
    assert report.unchanged == 1
    assert Match.objects.filter(provider_match_id="p-1").count() == 1
    assert MatchEventLog.objects.filter(event_type=event_schemas.MATCH_UPDATED).count() == 0


def test_reschedule_appends_match_updated_event() -> None:
    provider = _provider()
    original = timezone.now() + datetime.timedelta(days=2)
    fixtures.sync_fixtures(provider, [_record("p-1", start_time_utc=original)])

    moved = original + datetime.timedelta(hours=3)
    report = fixtures.sync_fixtures(provider, [_record("p-1", start_time_utc=moved)])
    assert report.rescheduled == 1
    assert report.updated == 1

    match = Match.objects.get(provider_match_id="p-1")
    assert match.start_time_utc == moved
    assert match.revision == 2
    event = MatchEventLog.objects.filter(
        match_id=match.id, event_type=event_schemas.MATCH_UPDATED
    ).latest("seq")
    assert event.payload["reason"] == "fixture_sync"
    assert event.payload["fields"]["start_time_utc"] == moved.isoformat()
    assert event.payload["source"] == "replay"


def test_cancellation_transitions_status_and_never_deletes() -> None:
    provider = _provider()
    fixtures.sync_fixtures(provider, [_record("p-1")])
    match = Match.objects.get(provider_match_id="p-1")

    report = fixtures.sync_fixtures(
        provider, [_record("p-1", status=MatchStatus.CANCELLED, status_detail="rain")]
    )
    assert report.cancelled == 1

    match.refresh_from_db()
    assert match.status == MatchStatus.CANCELLED
    assert match.status_detail == "rain"
    assert Match.objects.filter(provider_match_id="p-1").count() == 1  # never deleted
    assert (
        MatchEventLog.objects.filter(
            match_id=match.id, event_type=event_schemas.MATCH_STATE_CHANGED
        ).count()
        == 1
    )
    event = MatchEventLog.objects.filter(
        match_id=match.id, event_type=event_schemas.MATCH_STATE_CHANGED
    ).latest("seq")
    assert event.payload["from"] == MatchStatus.SCHEDULED
    assert event.payload["to"] == MatchStatus.CANCELLED


def test_fixture_list_cannot_regress_in_progress_match() -> None:
    provider = _provider()
    fixtures.sync_fixtures(provider, [_record("p-1")])
    match = Match.objects.get(provider_match_id="p-1")
    match.status = MatchStatus.IN_PROGRESS
    match.save(update_fields=["status"])

    report = fixtures.sync_fixtures(provider, [_record("p-1", status=MatchStatus.SCHEDULED)])
    assert report.skipped == 1
    match.refresh_from_db()
    assert match.status == MatchStatus.IN_PROGRESS  # not regressed


def test_missing_fixture_in_later_window_is_not_deleted() -> None:
    provider = _provider()
    fixtures.sync_fixtures(provider, [_record("p-1"), _record("p-2")])
    fixtures.sync_fixtures(provider, [_record("p-1")])  # p-2 absent
    assert Match.objects.filter(provider_match_id="p-2").exists()
    assert MatchTeam.objects.filter(match__provider_match_id="p-2").count() == 2


def test_content_update_other_than_start() -> None:
    provider = _provider()
    fixtures.sync_fixtures(provider, [_record("p-1", overs_limit=20)])
    report = fixtures.sync_fixtures(provider, [_record("p-1", overs_limit=15)])
    assert report.updated == 1
    assert report.rescheduled == 0
    match = Match.objects.get(provider_match_id="p-1")
    assert match.overs_limit == 15


# --------------------------------------------------------------------------- #
# Error resilience + DLQ
# --------------------------------------------------------------------------- #


def test_malformed_record_is_counted_and_reported() -> None:
    provider = _provider()
    reported: list[tuple[object, Exception]] = []
    report = fixtures.sync_fixtures(
        provider,
        [_record("p-1"), "garbage"],  # type: ignore[list-item]
        on_error=lambda record, exc: reported.append((record, exc)),
    )
    assert report.created == 1
    assert report.errors == 1
    assert len(reported) == 1
    assert Match.objects.filter(provider_match_id="p-1").exists()
    assert Match.objects.filter(provider_match_id="bad").count() == 0


def test_default_reporter_writes_redacted_dlq_row() -> None:
    from apps.live.services.fixture_sync import _dlq_report_fixture_error

    _dlq_report_fixture_error(_record("bad"), ValueError("boom"))
    row = IngestionError.objects.get(stage="fixture_sync")
    assert row.error_class == "ValueError"
    assert row.payload["provider_match_id"] == "bad"
    assert row.payload["error"] == "boom"
    assert "bad" in row.error_detail


# --------------------------------------------------------------------------- #
# Orchestration (apps/live)
# --------------------------------------------------------------------------- #


class _StubAdapter:
    def __init__(self, raw: list[object]) -> None:
        self._raw = raw

    def list_fixtures(self, window: object) -> list[object]:
        return self._raw


def _register_stub(raw: list[object]) -> _StubAdapter:
    from apps.providers.registry import clear_registry, register_adapter

    clear_registry()  # test isolation: no stale adapter from an earlier test
    adapter = _StubAdapter(raw)
    register_adapter("replay", lambda: adapter)
    return adapter


def test_normalize_fixture_accepts_shapes() -> None:
    from apps.live.services.fixture_sync import normalize_fixture

    record = _record("p-1")
    assert normalize_fixture(record) is record
    assert (
        normalize_fixture(
            {
                "provider_match_id": "d-1",
                "format_type": "T20",
                "start_time_utc": timezone.now(),
            }
        ).provider_match_id
        == "d-1"
    )

    class _Obj:
        provider_match_id = "o-1"
        format_type = "T20"
        start_time_utc = timezone.now()

    assert normalize_fixture(_Obj()).provider_match_id == "o-1"


def test_run_fixture_sync_uses_adapter() -> None:
    from apps.live.services.fixture_sync import run_fixture_sync

    _register_stub([_record("p-1"), _record("p-2")])
    provider = _provider()
    report = run_fixture_sync(provider, days=3)
    assert report.created == 2
    assert Match.objects.filter(source_provider_id=provider.id).count() == 2


def test_run_fixture_sync_noop_without_adapter() -> None:
    from apps.live.services.fixture_sync import run_fixture_sync
    from apps.providers.registry import clear_registry

    clear_registry()
    provider = _provider()
    report = run_fixture_sync(provider, days=3)
    assert report.created == 0
    assert report.errors == 0


def test_sync_fixtures_task_respects_flag() -> None:
    from apps.live.services.tasks import sync_fixtures_task

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("django.conf.settings.LIVE_INGESTION_ENABLED", False)
        assert sync_fixtures_task() == []


def test_sync_fixtures_task_runs_with_adapter() -> None:
    from apps.live.services.tasks import sync_fixtures_task

    _register_stub([_record("p-1")])
    provider = _provider()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("django.conf.settings.LIVE_INGESTION_ENABLED", True)
        reports = sync_fixtures_task(provider_id=str(provider.id), days=1)
    assert reports == [
        {
            "provider": "replay",
            "created": 1,
            "updated": 0,
            "rescheduled": 0,
            "cancelled": 0,
            "unchanged": 0,
            "skipped": 0,
            "errors": 0,
        }
    ]
