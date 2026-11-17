"""Cricsheet adapter: pure parser unit tests + end-to-end pipeline imports.

The pure parser (:mod:`apps.ingestion.adapters.cricsheet_parse`) is exercised
without a database (dismissal/retirement mapping, 0-based ball numbering, run
decomposition, format mapping, registry extraction, error paths). The Django
adapter is exercised end-to-end through ``run_pipeline`` with the fixture match
``tests/fixtures/cricsheet/123456.yaml`` (India 8/1 -> Australia 9/0, win by 10
wickets):

* match/teams/players/innings/deliveries/summaries/result all persist;
* ``bowler_credited_wicket`` / ``dismissal_type`` / retirement land on the
  ``delivery`` rows;
* idempotent re-import of identical content is skipped (same content sha);
* changed content supersedes only the changed ball (revision 1 -> 2) with a
  ``DataCorrection`` audit row;
* a malformed file fails permanently: DLQ ``CricsheetParseError`` + failed job;
* ``_reconcile`` writes a ``reconciliation_mismatch`` DLQ row when the rebuilt
  summaries disagree with the file.

The ``cricsheet`` pipeline stays registered for the whole session (registered
at app load in ``ingestion.apps.ready()``), so these tests deliberately do NOT
clear the registry — unlike ``test_ingestion.py`` whose fake pipelines are
isolated by a module-level fixture.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

import pytest

from apps.core.enums import DismissalType, TeamType
from apps.deliveries.models import Delivery
from apps.ingestion.adapters.cricsheet import _reconcile
from apps.ingestion.adapters.cricsheet_parse import (
    CricsheetParseError,
    parse_cricsheet_yaml,
    sha256_of,
)
from apps.ingestion.models import ImportJob, IngestionError
from apps.ingestion.services.jobs import create_or_resume_job
from apps.ingestion.services.pipeline import run_pipeline
from apps.innings.models import Innings, InningsSummary
from apps.matches.models import Match, MatchResult, MatchTeam, Toss
from apps.players.models import Player
from apps.providers.models import DataProvider
from apps.synchronization.models import SyncCheckpoint
from apps.teams.models import Team

from .factories import make_provider

pytestmark = pytest.mark.django_db

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "cricsheet" / "123456.yaml"


@pytest.fixture(autouse=True)
def _ensure_provider() -> object:
    # transaction=True tests flush the migration-seeded ``cricsheet`` provider
    # row; run_pipeline resolves the provider by code, so it must exist.
    return make_provider()


def _write_yaml(tmp_path: Any, text: str, name: str = "match.yaml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Pure parser
# --------------------------------------------------------------------------- #


class TestParser:
    def test_parses_fixture_metadata(self) -> None:
        parsed = parse_cricsheet_yaml(FIXTURE)

        assert parsed.match_id == "123456"
        assert parsed.teams == ("India", "Australia")
        assert parsed.date == datetime.date(2023, 10, 25)
        assert parsed.match_type == "T20I"
        assert parsed.gender == "M"
        assert parsed.overs_limit == 20
        assert parsed.venue == "Narendra Modi Stadium"
        assert parsed.city == "Ahmedabad"
        assert parsed.toss_winner == "India"
        assert parsed.toss_decision == "bat"
        assert parsed.outcome_winner == "Australia"
        assert parsed.outcome_by_wickets == 10
        assert parsed.outcome_by_runs is None
        assert parsed.revision == 1

    def test_innings_totals_derived_from_deliveries(self) -> None:
        parsed = parse_cricsheet_yaml(FIXTURE)
        india, australia = parsed.innings

        assert (india.team, india.number, india.runs, india.wickets) == ("India", 1, 8, 1)
        assert (australia.team, australia.number, australia.runs, australia.wickets) == (
            "Australia",
            2,
            9,
            0,
        )
        assert len(india.deliveries) == 6
        assert len(australia.deliveries) == 6

    def test_ball_numbering_is_one_based_from_cricsheet_zero_based(self) -> None:
        parsed = parse_cricsheet_yaml(FIXTURE)
        india = parsed.innings[0]

        assert [d.ball_in_over for d in india.deliveries] == [1, 2, 3, 4, 5, 6]
        assert [d.over_number for d in india.deliveries] == [1] * 6
        assert [d.delivery_seq for d in india.deliveries] == list(range(1, 7))

    def test_wide_decomposes_into_extras(self) -> None:
        parsed = parse_cricsheet_yaml(FIXTURE)
        wide = parsed.innings[0].deliveries[3]

        assert (wide.runs_off_bat, wide.wides) == (0, 1)
        assert wide.total_runs == 1

    def test_caught_wicket_maps_to_dismissal_and_fielders(self) -> None:
        parsed = parse_cricsheet_yaml(FIXTURE)
        wicket = parsed.innings[0].deliveries[5]

        assert wicket.is_wicket is True
        assert wicket.dismissal_type == DismissalType.CAUGHT
        assert wicket.dismissed_player == "Rohit Sharma"
        assert wicket.bowler_credited_wicket is True
        assert wicket.fielders == ("Steve Smith",)
        assert wicket.retired_player is None

    def test_retired_hurt_is_a_retirement_not_a_wicket(self) -> None:
        parsed = parse_cricsheet_yaml(FIXTURE)
        retirement = parsed.innings[1].deliveries[3]

        assert retirement.is_wicket is False
        assert retirement.dismissal_type is None
        assert retirement.bowler_credited_wicket is False
        assert retirement.retired_player == "Glenn Maxwell"
        assert retirement.retirement_type == "HURT"

    def test_retired_out_is_a_dismissal_not_credited_to_bowler(self, tmp_path: Any) -> None:
        path = _write_yaml(
            tmp_path,
            """\
info:
  dates: ["2023-10-25"]
  gender: male
  match_type: T20I
  teams: [India, Australia]
  registry: {people: {}, teams: {}}
  players: {India: ["A"], Australia: ["B"]}
  outcome: {}
innings:
- team: India
  deliveries:
  - batter: A
    bowler: B
    non_striker: B
    runs: {batter: 0, extras: 0, total: 0}
    wicket: {kind: "retired out", player_out: A}
    over: 1
    ball: 0
""",
        )
        parsed = parse_cricsheet_yaml(path)
        dismissal = parsed.innings[0].deliveries[0]

        assert dismissal.is_wicket is True
        assert dismissal.dismissal_type == DismissalType.RETIRED_OUT
        assert dismissal.bowler_credited_wicket is False

    def test_unknown_wicket_kind_raises(self, tmp_path: Any) -> None:
        path = _write_yaml(
            tmp_path,
            """\
info:
  dates: ["2023-10-25"]
  gender: male
  match_type: T20I
  teams: [India, Australia]
  registry: {people: {}, teams: {}}
  players: {India: ["A"], Australia: ["B"]}
  outcome: {}
innings:
- team: India
  deliveries:
  - batter: A
    bowler: B
    non_striker: B
    runs: {batter: 0, extras: 0, total: 0}
    wicket: {kind: "smote", player_out: A}
    over: 1
    ball: 0
""",
        )
        with pytest.raises(CricsheetParseError, match="unknown wicket kind"):
            parse_cricsheet_yaml(path)

    def test_missing_info_raises(self, tmp_path: Any) -> None:
        path = _write_yaml(tmp_path, "not_a_match: true\n")
        with pytest.raises(CricsheetParseError, match="missing top-level info"):
            parse_cricsheet_yaml(path)

    def test_wrong_team_count_raises(self, tmp_path: Any) -> None:
        path = _write_yaml(
            tmp_path,
            """\
info:
  dates: ["2023-10-25"]
  gender: male
  match_type: T20I
  teams: [India]
  registry: {people: {}, teams: {}}
  players: {}
  outcome: {}
innings: []
""",
        )
        with pytest.raises(CricsheetParseError, match="exactly two teams"):
            parse_cricsheet_yaml(path)

    def test_invalid_yaml_raises(self, tmp_path: Any) -> None:
        path = _write_yaml(tmp_path, "info: [unclosed\n  : bad: : :")
        with pytest.raises(CricsheetParseError, match="invalid YAML"):
            parse_cricsheet_yaml(path)

    def test_sha256_is_deterministic_and_content_sensitive(self, tmp_path: Any) -> None:
        first = sha256_of(FIXTURE)
        assert sha256_of(FIXTURE) == first

        text = FIXTURE.read_text(encoding="utf-8")
        changed = _write_yaml(tmp_path, text.replace("wides: 1", "wides: 0"))
        assert sha256_of(changed) != first

    def test_registry_and_player_rosters_extracted(self) -> None:
        parsed = parse_cricsheet_yaml(FIXTURE)

        assert parsed.player_registry["Virat Kohli"] == {"key_opta": "opt-kohli"}
        assert parsed.team_registry["India"] == {"key_opta": "opt-india"}
        assert len(parsed.players_by_team["India"]) == 4
        assert len(parsed.players_by_team["Australia"]) == 4

    def test_format_mapping(self, tmp_path: Any) -> None:
        cases = {
            "ODI": "ODI",
            "Test": "TEST",
            "WTest": "TEST",
            "T20": "T20",
            "First-class": "FIRST_CLASS",
            "List A": "LIST_A",
            "T Hundred": "THE_HUNDRED",
        }
        for cricsheet_type, expected in cases.items():
            path = _write_yaml(
                tmp_path,
                f"""\
info:
  dates: ["2023-10-25"]
  gender: male
  match_type: {cricsheet_type}
  teams: [India, Australia]
  registry: {{people: {{}}, teams: {{}}}}
  players: {{}}
  outcome: {{}}
innings: []
""",
                name=f"{cricsheet_type.replace(' ', '_')}.yaml",
            )
            assert parse_cricsheet_yaml(path).match_type == expected


# --------------------------------------------------------------------------- #
# End-to-end pipeline import (transaction=True so on_commit fires)
# --------------------------------------------------------------------------- #


@pytest.mark.django_db(transaction=True)
class TestCricsheetImport:
    def _import(self, path: Path) -> Any:
        return run_pipeline(pipeline_name="cricsheet", factory_args={"path": str(path)})

    def test_full_import_round_trip(self) -> None:
        report = self._import(FIXTURE)

        assert report.action == "created"
        assert report.status == "succeeded"
        assert (report.read, report.written, report.skipped) == (12, 12, 0)

        job = ImportJob.objects.get(id=report.job_id)
        assert job.status == "succeeded"
        assert job.job_type == "cricsheet"
        assert job.records_written == 12

        # Match + participation.
        match = Match.objects.get(provider_match_id="123456")
        assert match.format_type == "T20I"
        assert match.gender == "M"
        assert match.source_local_date == datetime.date(2023, 10, 25)
        assert match.overs_limit == 20
        assert match.status == "COMPLETED"
        assert match.result_type == "WIN"
        assert match.winner_team.name == "Australia"
        assert match.toss_decision == "bat"
        assert match.toss_winner_team.name == "India"

        teams = {t.team.name for t in MatchTeam.objects.filter(match=match)}
        assert teams == {"India", "Australia"}
        india = Team.objects.get(name="India")
        assert india.team_type == TeamType.INTERNATIONAL
        assert india.is_international is True

        assert Player.objects.count() == 8
        assert Toss.objects.get(match=match).decision == "bat"

        # Innings + summaries + result.
        innings = list(Innings.objects.filter(match=match).order_by("innings_number"))
        assert [i.innings_number for i in innings] == [1, 2]
        summaries = {i.innings_number: i.summary for i in innings}
        assert (summaries[1].runs, summaries[1].wickets) == (8, 1)
        assert (summaries[2].runs, summaries[2].wickets) == (9, 0)

        result = MatchResult.objects.get(match=match)
        assert (result.result_type, result.margin_type, result.margin_value) == (
            "WIN",
            "wickets",
            10,
        )

        # Checkpoint advanced (cursor = provider match id).
        checkpoint = SyncCheckpoint.objects.get(
            provider=DataProvider.objects.get(code="cricsheet"), scope_key="cricsheet:123456"
        )
        assert checkpoint.cursor == "123456"

    def test_dismissal_and_retirement_persisted_on_delivery_rows(self) -> None:
        self._import(FIXTURE)
        match = Match.objects.get(provider_match_id="123456")
        india = Innings.objects.get(match=match, innings_number=1)
        australia = Innings.objects.get(match=match, innings_number=2)
        rohit = Player.objects.get(full_name="Rohit Sharma")
        maxwell = Player.objects.get(full_name="Glenn Maxwell")

        wicket = Delivery.objects.get(
            innings_id=india.id, delivery_seq=6, is_current=True, match_date=match.source_local_date
        )
        assert wicket.is_wicket is True
        assert wicket.dismissal_type == DismissalType.CAUGHT
        assert wicket.bowler_credited_wicket is True
        assert wicket.dismissed_player_id == rohit.id
        assert wicket.revision == 1

        retirement = Delivery.objects.get(
            innings_id=australia.id,
            delivery_seq=4,
            is_current=True,
            match_date=match.source_local_date,
        )
        assert retirement.is_wicket is False
        assert retirement.retired_player_id == maxwell.id
        assert retirement.retirement_type == "HURT"
        assert retirement.dismissal_type is None

    def test_identical_reimport_is_skipped(self) -> None:
        first = self._import(FIXTURE)
        second = self._import(FIXTURE)

        assert second.action == "skipped"
        assert second.status == "succeeded"
        assert ImportJob.objects.filter(idempotency_key=first.idempotency_key).count() == 1
        assert Delivery.objects.filter(is_current=True).count() == 12

    def test_changed_content_supersedes_only_the_changed_ball(self, tmp_path: Any) -> None:
        self._import(FIXTURE)
        # The wide (delivery 4 of India) becomes a dot ball: content sha changes.
        text = FIXTURE.read_text(encoding="utf-8").replace("wides: 1", "wides: 0")
        changed = _write_yaml(tmp_path, text, name="123456.yaml")

        report = self._import(changed)
        assert report.action == "created"
        assert report.status == "succeeded"

        match = Match.objects.get(provider_match_id="123456")
        india = Innings.objects.get(match=match, innings_number=1)
        seq4 = Delivery.objects.filter(
            innings_id=india.id, delivery_seq=4, match_date=match.source_local_date
        )
        superseded = seq4.get(is_current=False)
        current = seq4.get(is_current=True)
        assert (superseded.revision, current.revision) == (1, 2)
        assert (current.wides, current.runs_off_bat) == (0, 0)

        # Audit trail written by the write path.
        from apps.synchronization.models import DataCorrection

        correction = DataCorrection.objects.get(entity_type="delivery", entity_id=superseded.id)
        assert (correction.source_revision, correction.target_revision) == (1, 2)

        # Rebuild picked up the corrected total: India 8 - 1 (wide) = 7.
        summary = InningsSummary.objects.get(innings=india)
        assert summary.runs == 7

    def test_malformed_file_fails_permanently_with_dlq(self, tmp_path: Any) -> None:
        path = _write_yaml(tmp_path, "info: [unclosed\n  : bad: : :", name="999999.yaml")

        report = self._import(path)

        assert report.action == "created"
        assert report.status == "failed"
        assert report.error_class == "CricsheetParseError"

        job = ImportJob.objects.get(id=report.job_id)
        assert job.status == "failed"
        (dlq,) = IngestionError.objects.filter(import_job=job)
        assert dlq.stage == "parse"
        assert dlq.error_class == "CricsheetParseError"
        assert "999999.yaml" in dlq.error_detail  # type: ignore[operator]
        # No partial data was written.
        assert Match.objects.filter(provider_match_id="999999").count() == 0

    def test_reconciliation_mismatch_writes_dlq_row(self) -> None:
        self._import(FIXTURE)
        match = Match.objects.get(provider_match_id="123456")
        provider = DataProvider.objects.get(code="cricsheet")
        parsed = parse_cricsheet_yaml(FIXTURE)
        job, _ = create_or_resume_job(
            provider, job_type="test", scope={}, idempotency_key="reconcile:test"
        )

        # Tamper with the rebuilt summary so it disagrees with the file.
        india = Innings.objects.get(match=match, innings_number=1)
        InningsSummary.objects.filter(innings=india).update(runs=99)

        _reconcile(match, parsed, job)

        (dlq,) = IngestionError.objects.filter(import_job=job)
        assert dlq.stage == "reconcile"
        assert dlq.error_class == "reconciliation_mismatch"
        assert dlq.payload == {
            "match_id": "123456",
            "innings_number": 1,
            "expected_runs": 8,
            "actual_runs": 99,
            "expected_wickets": 1,
            "actual_wickets": 1,
        }
