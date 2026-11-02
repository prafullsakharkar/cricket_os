"""Summary-only adapter: pure parser unit tests + end-to-end pipeline imports.

The pure parser (:mod:`apps.ingestion.adapters.summary_parse`) is exercised
without a database (overs -> legal_balls derivation, line extraction, error
paths). The Django adapter is exercised end-to-end through ``run_pipeline``
with the fixture card ``tests/fixtures/summary/S-001.json`` (ODI: India 250/8
-> Australia 251/5, Australia win by 5 wickets):

* match/teams/22 players/innings/toss/result persist;
* ``innings_summary`` rows carry the provider figures with
  ``batch_complete=false`` (summary-only: engine rebuild skips them);
* ``batting_entry`` / ``bowling_entry`` cards are written directly by the
  adapter (provider-written, authoritative);
* **no ``delivery`` rows are ever written**;
* idempotent re-import of identical content is skipped; changed content
  re-upserts the card rows;
* a malformed file fails permanently: DLQ ``SummaryParseError`` + failed job.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from apps.deliveries.models import Delivery
from apps.ingestion.adapters.summary_parse import SummaryParseError, parse_summary_json
from apps.ingestion.models import ImportJob, IngestionError
from apps.ingestion.services.pipeline import run_pipeline
from apps.innings.models import Innings, InningsSummary
from apps.matches.models import Match, MatchResult, MatchTeam, Toss
from apps.players.models import Player
from apps.scorecards.models import BattingEntry, BowlingEntry
from apps.synchronization.models import SyncCheckpoint

from .factories import make_provider

pytestmark = pytest.mark.django_db

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "summary" / "S-001.json"


@pytest.fixture(autouse=True)
def _ensure_provider() -> object:
    # transaction=True tests flush the migration-seeded ``cricsheet`` provider
    # row; run_pipeline resolves the provider by code, so it must exist.
    return make_provider()


def _write_json(tmp_path: Any, data: dict[str, Any], name: str = "match.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Pure parser
# --------------------------------------------------------------------------- #


class TestParser:
    def test_parses_fixture_metadata(self) -> None:
        parsed = parse_summary_json(FIXTURE)

        assert parsed.match_id == "S-001"
        assert parsed.teams == ("India", "Australia")
        assert parsed.date.isoformat() == "2023-10-25"
        assert parsed.match_type == "ODI"
        assert parsed.gender == "M"
        assert parsed.overs_limit == 50
        assert parsed.venue == "Narendra Modi Stadium"
        assert parsed.toss_winner == "India"
        assert parsed.toss_decision == "bat"
        assert parsed.result_winner == "Australia"
        assert (parsed.result_type, parsed.margin_type, parsed.margin_value) == (
            "WIN",
            "wickets",
            5,
        )

    def test_innings_derived_from_overs_displays(self) -> None:
        parsed = parse_summary_json(FIXTURE)
        india, australia = parsed.innings

        assert (india.team, india.runs, india.wickets, india.overs_display) == (
            "India",
            250,
            8,
            "50.0",
        )
        assert india.legal_balls == 300
        assert india.extras == {"wides": 10, "noballs": 2, "byes": 5, "leg_byes": 3, "penalty": 0}

        assert (australia.runs, australia.wickets, australia.overs_display) == (251, 5, "48.2")
        assert australia.legal_balls == 290

    def test_batting_and_bowling_lines(self) -> None:
        parsed = parse_summary_json(FIXTURE)
        india = parsed.innings[0]

        rohit = india.batting[0]
        assert (rohit.player, rohit.runs, rohit.balls, rohit.fours, rohit.sixes) == (
            "Rohit Sharma",
            90,
            100,
            8,
            2,
        )
        assert rohit.is_out is True
        assert (rohit.dismissal_type, rohit.bowler, rohit.fielder) == (
            "CAUGHT",
            "Mitchell Starc",
            "David Warner",
        )
        assert india.batting[1].is_out is False

        starc = india.bowling[0]
        assert (
            starc.player,
            starc.overs_display,
            starc.legal_balls,
            starc.runs_conceded,
            starc.wickets,
            starc.maidens,
        ) == ("Mitchell Starc", "10.0", 60, 45, 2, 1)

    def test_overs_without_decimal_parses(self, tmp_path: Any) -> None:
        data = _base_data(tmp_path)
        data["innings"][0]["overs"] = "50"
        parsed = parse_summary_json(_write_json(tmp_path, data))
        assert parsed.innings[0].legal_balls == 300

    def test_invalid_json_raises(self, tmp_path: Any) -> None:
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(SummaryParseError, match="invalid JSON"):
            parse_summary_json(path)

    def test_missing_match_id_raises(self, tmp_path: Any) -> None:
        data = _base_data(tmp_path)
        del data["match_id"]
        with pytest.raises(SummaryParseError, match="match_id"):
            parse_summary_json(_write_json(tmp_path, data))

    def test_wrong_team_count_raises(self, tmp_path: Any) -> None:
        data = _base_data(tmp_path)
        data["teams"] = ["India"]
        with pytest.raises(SummaryParseError, match="exactly two teams"):
            parse_summary_json(_write_json(tmp_path, data))

    def test_bad_date_raises(self, tmp_path: Any) -> None:
        data = _base_data(tmp_path)
        data["date"] = "not-a-date"
        with pytest.raises(SummaryParseError, match="bad date"):
            parse_summary_json(_write_json(tmp_path, data))

    def test_bad_overs_display_raises(self, tmp_path: Any) -> None:
        data = _base_data(tmp_path)
        data["innings"][0]["overs"] = "48.9"
        with pytest.raises(SummaryParseError, match="bad overs display"):
            parse_summary_json(_write_json(tmp_path, data))


def _base_data(tmp_path: Any) -> dict[str, Any]:
    """A minimal valid card the error-path tests can corrupt."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# End-to-end pipeline import (transaction=True so on_commit fires)
# --------------------------------------------------------------------------- #


@pytest.mark.django_db(transaction=True)
class TestSummaryImport:
    def _import(self, path: Path) -> Any:
        return run_pipeline(pipeline_name="summary_cards", factory_args={"path": str(path)})

    def test_full_import_round_trip(self) -> None:
        report = self._import(FIXTURE)

        assert report.action == "created"
        assert report.status == "succeeded"
        assert report.read == 24
        assert report.written == 24

        match = Match.objects.get(provider_match_id="S-001")
        assert match.format_type == "ODI"
        assert match.status == "COMPLETED"
        assert match.result_type == "WIN"
        assert match.winner_team.name == "Australia"
        assert match.toss_decision == "bat"
        assert match.overs_limit == 50

        assert MatchTeam.objects.filter(match=match).count() == 2
        assert {t.team.name for t in MatchTeam.objects.filter(match=match)} == {
            "India",
            "Australia",
        }
        assert Player.objects.count() == 22
        assert Toss.objects.get(match=match).decision == "bat"

        # Innings summaries: provider figures, summary-only.
        innings = list(Innings.objects.filter(match=match).order_by("innings_number"))
        summaries = {i.innings_number: i.summary for i in innings}
        assert (summaries[1].runs, summaries[1].wickets, summaries[1].overs_display) == (
            250,
            8,
            "50.0",
        )
        assert summaries[1].legal_balls == 300
        assert summaries[1].batch_complete is False
        assert (summaries[2].runs, summaries[2].wickets) == (251, 5)
        assert summaries[2].batch_complete is False

        # Cards: batting + bowling entries written by the provider.
        india = innings[0]
        assert BattingEntry.objects.filter(innings=india).count() == 6
        assert BowlingEntry.objects.filter(innings=india).count() == 6
        rohit = Player.objects.get(full_name="Rohit Sharma")
        entry = BattingEntry.objects.get(innings=india, player=rohit)
        assert (entry.runs, entry.balls, entry.fours, entry.sixes) == (90, 100, 8, 2)
        assert entry.is_out is True
        assert entry.dismissal_type == "CAUGHT"
        assert entry.bowler_id == Player.objects.get(full_name="Mitchell Starc").id

        # Result written by the provider (no engine derivation).
        result = MatchResult.objects.get(match=match)
        assert (result.result_type, result.margin_type, result.margin_value) == (
            "WIN",
            "wickets",
            5,
        )

        # Summary-only: no ball-by-ball rows at all.
        assert Delivery.objects.filter(match_id=match.id).count() == 0

        # Checkpoint advanced.
        checkpoint = SyncCheckpoint.objects.get(scope_key="summary:S-001")
        assert checkpoint.cursor == "S-001"

    def test_no_deliveries_and_rebuild_skips_summary_only(self) -> None:
        from apps.scorecards.services.rebuild import rebuild_match

        self._import(FIXTURE)
        match = Match.objects.get(provider_match_id="S-001")
        assert Delivery.objects.filter(match_id=match.id).count() == 0

        # The engine must not touch provider-written rows.
        report = rebuild_match(match)
        assert report.skipped_reason == "summary_only"
        assert report.changed is False
        india = Innings.objects.get(match=match, innings_number=1)
        assert InningsSummary.objects.get(innings=india).runs == 250
        assert BattingEntry.objects.filter(innings=india).count() == 6

    def test_identical_reimport_is_skipped(self) -> None:
        first = self._import(FIXTURE)
        second = self._import(FIXTURE)

        assert second.action == "skipped"
        assert second.status == "succeeded"
        assert ImportJob.objects.filter(idempotency_key=first.idempotency_key).count() == 1

    def test_changed_content_reupserts_card_rows(self, tmp_path: Any) -> None:
        self._import(FIXTURE)
        text = FIXTURE.read_text(encoding="utf-8").replace(
            '"runs": 90, "balls": 100', '"runs": 91, "balls": 100'
        )
        changed = tmp_path / "S-001.json"
        changed.write_text(text, encoding="utf-8")

        report = self._import(changed)
        assert report.action == "created"
        assert report.status == "succeeded"

        match = Match.objects.get(provider_match_id="S-001")
        india = Innings.objects.get(match=match, innings_number=1)
        rohit = Player.objects.get(full_name="Rohit Sharma")
        entry = BattingEntry.objects.get(innings=india, player=rohit)
        assert entry.runs == 91
        assert BattingEntry.objects.filter(innings=india).count() == 6
        assert Delivery.objects.filter(match_id=match.id).count() == 0

    def test_malformed_file_fails_permanently_with_dlq(self, tmp_path: Any) -> None:
        path = tmp_path / "BAD-001.json"
        path.write_text("{not json", encoding="utf-8")

        report = self._import(path)

        assert report.action == "created"
        assert report.status == "failed"
        assert report.error_class == "SummaryParseError"

        job = ImportJob.objects.get(id=report.job_id)
        assert job.status == "failed"
        (dlq,) = IngestionError.objects.filter(import_job=job)
        assert dlq.stage == "parse"
        assert dlq.error_class == "SummaryParseError"
        assert Match.objects.filter(provider_match_id="BAD-001").count() == 0
