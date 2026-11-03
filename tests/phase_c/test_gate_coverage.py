"""Branch coverage for the 95% gate files (write path / rebuild / Cricsheet).

The Phase C coverage gate measures `apps/deliveries/services/write.py`,
`apps/scorecards/services/rebuild.py` and the Cricsheet adapter+parser at
>= 95% line+branch. These focused tests exercise the defensive branches that
the scenario suites do not reach:

* write path: a stored-vs-draft ``None`` mismatch in ``_row_matches_draft``
  and the date/datetime/time arms of the correction audit serializer;
* rebuild: ``_canon`` for UUID/date/Decimal/float digest inputs, the
  exactly-two-teams guard, the result-``None`` (unfinished match) persist
  path, and ``correction.applied`` after a completed match is corrected
  (both the innings- and match-level rebuilds);
* Cricsheet adapter: ``dry_run``, toss/player/innings skips for unknown
  teams, and the two ``_reconcile`` skip guards;
* Cricsheet parser: non-integer revision, a wicket without kind/player_out,
  missing/bad dates, and a delivery without an ``over``.
"""

from __future__ import annotations

import datetime
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from apps.core import events as core_events
from apps.core.uuid7 import uuid7
from apps.deliveries.services.validation import DeliveryDraft
from apps.deliveries.services.write import _as_json, _row_matches_draft
from apps.ingestion.adapters.cricsheet import (
    _reconcile,
    _upsert_innings,
    _upsert_players,
    _upsert_toss,
)
from apps.ingestion.adapters.cricsheet_parse import (
    CricsheetDelivery,
    CricsheetInnings,
    CricsheetMatch,
    CricsheetParseError,
    parse_cricsheet_yaml,
)
from apps.ingestion.services.pipeline import run_pipeline
from apps.matches.models import MatchResult
from apps.scorecards.services.rebuild import digest, rebuild_innings, rebuild_match

from .factories import (
    make_innings,
    make_match,
    make_match_teams,
    make_player,
    make_provider,
    make_team,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "cricsheet" / "123456.yaml"


def _write_yaml(tmp_path: Any, text: str, name: str = "match.yaml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _minimal_yaml() -> str:
    """A valid single-delivery card the error tests can corrupt."""
    return """
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
    wicket: false
    over: 1
    ball: 0
"""


# --------------------------------------------------------------------------- #
# Write path branches (pure — no DB)
# --------------------------------------------------------------------------- #


class TestWritePathBranches:
    def test_row_matches_draft_false_when_exactly_one_side_is_none(self) -> None:
        draft = DeliveryDraft(
            delivery_seq=1,
            over_number=1,
            ball_in_over=1,
            bowler_id="b1",
            batter_id="b2",
            non_striker_id="b3",
            retired_player_id=None,
        )
        # Stored row still has the old retirement (UUID object from the DB).
        row = {
            "delivery_seq": 1,
            "over_number": 1,
            "ball_in_over": 1,
            "bowler_id": "b1",
            "batter_id": "b2",
            "non_striker_id": "b3",
            "runs_off_bat": 0,
            "wides": 0,
            "noballs": 0,
            "byes": 0,
            "leg_byes": 0,
            "penalty_runs": 0,
            "is_wicket": False,
            "dismissal_type": None,
            "dismissed_player_id": None,
            "bowler_credited_wicket": False,
            "fielder1_id": None,
            "fielder2_id": None,
            "fielder3_id": None,
            "shot_type": None,
            "commentary_text": None,
            "provider_delivery_id": None,
            "retired_player_id": uuid7(),
            "retirement_type": "HURT",
            "revision": 1,
        }
        assert _row_matches_draft(row, draft) is False

    def test_row_matches_draft_true_for_equal_content(self) -> None:
        draft = DeliveryDraft(
            delivery_seq=1,
            over_number=1,
            ball_in_over=1,
            bowler_id="b1",
            batter_id="b2",
            non_striker_id="b3",
        )
        row = {
            "delivery_seq": 1,
            "over_number": 1,
            "ball_in_over": 1,
            "bowler_id": "b1",
            "batter_id": "b2",
            "non_striker_id": "b3",
            "runs_off_bat": 0,
            "wides": 0,
            "noballs": 0,
            "byes": 0,
            "leg_byes": 0,
            "penalty_runs": 0,
            "is_wicket": False,
            "dismissal_type": None,
            "dismissed_player_id": None,
            "bowler_credited_wicket": False,
            "fielder1_id": None,
            "fielder2_id": None,
            "fielder3_id": None,
            "shot_type": None,
            "commentary_text": None,
            "provider_delivery_id": None,
            "retired_player_id": None,
            "retirement_type": None,
            "revision": 1,
        }
        assert _row_matches_draft(row, draft) is True

    def test_as_json_serializes_dates_datetimes_and_times(self) -> None:
        assert _as_json(datetime.date(2026, 1, 2)) == "2026-01-02"
        assert _as_json(datetime.datetime(2026, 1, 2, 3, 4, 5)) == "2026-01-02T03:04:05"
        assert _as_json(datetime.time(3, 4, 5)) == "03:04:05"

    def test_as_json_stringifies_bytes_and_uuids(self) -> None:
        # ``str()`` of bytes is the repr form — JSON-safe, never raw binary.
        assert _as_json(b"raw") == "b'raw'"
        value = uuid7()
        assert _as_json(value) == str(value)
        # Plain values pass through untouched.
        assert _as_json(7) == 7


# --------------------------------------------------------------------------- #
# Rebuild branches
# --------------------------------------------------------------------------- #


class TestRebuildDigest:
    def test_canon_handles_uuid_date_decimal_float_and_collections(self) -> None:
        uid = uuid7()
        now = datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.UTC)
        states = (
            {"uuid": uid, "day": datetime.date(2026, 1, 2), "when": now},
            {"amount": Decimal("1.50"), "rate": 3.14159, "nil": None},
            {"nested": {"b": 2, "a": 1}, "seq": [1, "two", 3.0]},
        )
        # Deterministic: same inputs hash identically; sorted keys stable.
        assert digest(*states) == digest(*states)
        # A UUID change (the correction link) changes the digest.
        changed = {"uuid": uuid7(), "day": datetime.date(2026, 1, 2), "when": now}
        assert digest(*states) != digest(changed, states[1], states[2])


@pytest.mark.django_db
class TestRebuildGuards:
    def test_match_context_requires_exactly_two_teams(self) -> None:
        match = make_match()
        t1 = make_team("India")
        t2 = make_team("Australia")
        make_match_teams(match, t1, t2)
        make_innings(match, t1, t2, innings_number=1)
        # Drop one team so the guard fires.
        match.teams.first().delete()  # type: ignore[union-attr]

        with pytest.raises(ValueError, match="exactly two teams"):
            rebuild_match(match)


@pytest.mark.django_db(transaction=True)
class TestRebuildUnfinishedMatch:
    """An innings that never completes has no result: persist(None) runs."""

    @pytest.fixture(autouse=True)
    def _ensure_provider(self) -> object:
        return make_provider()

    def test_unfinished_match_writes_no_result(self) -> None:
        match = make_match()
        t1 = make_team("India")
        t2 = make_team("Australia")
        make_match_teams(match, t1, t2)
        innings = make_innings(match, t1, t2, innings_number=1)
        make_innings(match, t2, t1, innings_number=2)  # never batted
        bowler = make_player("Jasprit Bumrah")
        batter = make_player("Virat Kohli")
        non_striker = make_player("Rohit Sharma")

        drafts = [
            DeliveryDraft(
                delivery_seq=seq,
                over_number=1,
                ball_in_over=ball,
                bowler_id=bowler.id,
                batter_id=batter.id,
                non_striker_id=non_striker.id,
                runs_off_bat=1,
            )
            for seq, ball in enumerate([1, 2, 3, 4, 5, 6], start=1)
        ]
        from apps.deliveries.services.write import append_or_revise

        append_or_revise(innings, drafts, source=make_provider(), suppress_events=True)

        report = rebuild_match(match)
        assert report.changed is True
        # No result is derived for an unfinished match.
        assert MatchResult.objects.filter(match=match).count() == 0


@pytest.mark.django_db(transaction=True)
class TestRebuildCorrectionEvents:
    """Corrections after completion publish ``correction.applied`` (both paths)."""

    @pytest.fixture(autouse=True)
    def _clear_events(self) -> None:
        core_events.clear_subscriptions()
        yield
        core_events.clear_subscriptions()

    def _completed_match(self) -> dict[str, Any]:
        provider = make_provider()
        match = make_match()
        t1 = make_team("India")
        t2 = make_team("Australia")
        make_match_teams(match, t1, t2)
        i1 = make_innings(match, t1, t2, innings_number=1)
        i2 = make_innings(match, t2, t1, innings_number=2)
        bowler = make_player("Jasprit Bumrah")
        batter = make_player("Virat Kohli")
        non_striker = make_player("Rohit Sharma")

        def load(innings: Any, specs: list[tuple[int, int]]) -> None:
            drafts = [
                DeliveryDraft(
                    delivery_seq=seq,
                    over_number=1,
                    ball_in_over=ball,
                    bowler_id=bowler.id,
                    batter_id=batter.id,
                    non_striker_id=non_striker.id,
                    runs_off_bat=runs,
                )
                for seq, (ball, runs) in enumerate(specs, start=1)
            ]
            from apps.deliveries.services.write import append_or_revise

            append_or_revise(innings, drafts, source=provider, suppress_events=True)

        load(i1, [(1, 1), (2, 1), (3, 1), (4, 1), (5, 1), (6, 1)])
        load(i2, [(1, 4), (2, 4), (3, 4), (4, 4), (5, 1), (6, 1)])
        rebuild_match(match)
        return {
            "provider": provider,
            "match": match,
            "i1": i1,
            "bowler": bowler,
            "batter": batter,
            "non_striker": non_striker,
        }

    def test_innings_rebuild_publishes_correction_on_completed_match(self) -> None:
        ctx = self._completed_match()
        corrected: list[dict[str, object]] = []
        core_events.subscribe(core_events.TOPIC_CORRECTION_APPLIED, lambda p: corrected.append(p))

        changed = DeliveryDraft(
            delivery_seq=6,
            over_number=1,
            ball_in_over=6,
            bowler_id=ctx["bowler"].id,
            batter_id=ctx["batter"].id,
            non_striker_id=ctx["non_striker"].id,
            runs_off_bat=4,
        )
        from apps.deliveries.services.write import append_or_revise

        append_or_revise(ctx["i1"], [changed], source=ctx["provider"], suppress_events=True)

        report = rebuild_innings(ctx["i1"])
        assert report.changed is True
        assert len(corrected) == 1
        assert corrected[0]["innings_id"] == str(ctx["i1"].id)

    def test_match_rebuild_publishes_correction_after_completion(self) -> None:
        ctx = self._completed_match()
        corrected: list[dict[str, object]] = []
        core_events.subscribe(core_events.TOPIC_CORRECTION_APPLIED, lambda p: corrected.append(p))

        changed = DeliveryDraft(
            delivery_seq=6,
            over_number=1,
            ball_in_over=6,
            bowler_id=ctx["bowler"].id,
            batter_id=ctx["batter"].id,
            non_striker_id=ctx["non_striker"].id,
            runs_off_bat=4,
        )
        from apps.deliveries.services.write import append_or_revise

        append_or_revise(ctx["i1"], [changed], source=ctx["provider"], suppress_events=True)

        report = rebuild_match(ctx["match"])
        assert report.changed is True
        assert len(corrected) == 1
        assert MatchResult.objects.get(match=ctx["match"]).result_type == "WIN"


# --------------------------------------------------------------------------- #
# Cricsheet adapter branches
# --------------------------------------------------------------------------- #


def _parsed(**overrides: Any) -> CricsheetMatch:
    delivery = CricsheetDelivery(
        delivery_seq=1,
        over_number=1,
        ball_in_over=1,
        batter="Rohit Sharma",
        bowler="Mitchell Starc",
        non_striker="Virat Kohli",
        runs_off_bat=1,
        wides=0,
        noballs=0,
        byes=0,
        leg_byes=0,
        penalty_runs=0,
        is_wicket=False,
        dismissal_type=None,
        dismissed_player=None,
        bowler_credited_wicket=False,
        fielders=(),
        retired_player=None,
        retirement_type=None,
    )
    base = CricsheetMatch(
        match_id="123456",
        content_sha256="x" * 64,
        date=datetime.date(2023, 10, 25),
        gender="M",
        match_type="T20I",
        teams=("India", "Australia"),
        toss_winner="India",
        toss_decision="bat",
        venue=None,
        city=None,
        overs_limit=20,
        outcome_winner=None,
        outcome_by_runs=None,
        outcome_by_wickets=None,
        revision=1,
        player_registry={},
        team_registry={},
        players_by_team={"India": ("Rohit Sharma",), "Australia": ("Mitchell Starc",)},
        innings=(CricsheetInnings(team="India", number=1, deliveries=(delivery,)),),
    )
    return replace(base, **overrides)


@pytest.mark.django_db(transaction=True)
class TestCricsheetAdapterBranches:
    @pytest.fixture(autouse=True)
    def _ensure_provider(self) -> object:
        return make_provider()

    def test_dry_run_writes_nothing(self) -> None:
        report = run_pipeline(
            pipeline_name="cricsheet",
            factory_args={"path": str(FIXTURE), "dry_run": True},
        )
        assert report.status == "succeeded"
        assert report.written == 0
        assert report.read == 12
        from apps.matches.models import Match

        assert Match.objects.filter(provider_match_id="123456").count() == 0

    def test_upsert_toss_skips_without_toss(self) -> None:
        from apps.matches.models import Toss

        match = make_match()
        make_match_teams(match, make_team("India"), make_team("Australia"))
        _upsert_toss(match, _parsed(toss_winner=None, toss_decision=None))
        assert Toss.objects.filter(match=match).count() == 0

    def test_upsert_toss_skips_unknown_winner(self) -> None:
        from apps.matches.models import Toss

        match = make_match()
        make_match_teams(match, make_team("India"), make_team("Australia"))
        _upsert_toss(match, _parsed(toss_winner="Zimbabwe"))
        assert Toss.objects.filter(match=match).count() == 0

    def test_upsert_players_skips_unknown_team(self) -> None:
        from apps.matches.models import MatchPlayer

        match = make_match()
        make_match_teams(match, make_team("India"), make_team("Australia"))
        _upsert_players(match, _parsed(players_by_team={"Zimbabwe": ("Someone",)}), {})
        assert MatchPlayer.objects.filter(match=match).count() == 0

    def test_upsert_innings_skips_unknown_batting_team(self) -> None:
        match = make_match()
        make_match_teams(match, make_team("India"), make_team("Australia"))
        # A parsed innings whose team is not in the match is skipped (165).
        parsed = _parsed(innings=(CricsheetInnings(team="Zimbabwe", number=1, deliveries=()),))
        assert _upsert_innings(match, parsed) == {}

    def test_pipeline_skips_innings_not_in_the_match(self, tmp_path: Any) -> None:
        # info.teams is India/Australia but the second innings bats "Zimbabwe":
        # that innings is skipped during upsert (165) and again in the write
        # loop (304) without failing the import.
        text = """
info:
  dates: ["2023-10-25"]
  gender: male
  match_type: T20I
  teams: [India, Australia]
  registry: {people: {}, teams: {}}
  players: {India: ["A", "B"], Australia: ["C", "D"]}
  outcome: {}
innings:
- team: India
  deliveries:
  - batter: A
    bowler: C
    non_striker: B
    runs: {batter: 1, extras: 0, total: 1}
    wicket: false
    over: 1
    ball: 0
- team: Zimbabwe
  deliveries:
  - batter: A
    bowler: C
    non_striker: B
    runs: {batter: 0, extras: 0, total: 0}
    wicket: false
    over: 1
    ball: 0
"""
        path = _write_yaml(tmp_path, text, name="999998.yaml")
        report = run_pipeline(pipeline_name="cricsheet", factory_args={"path": str(path)})
        assert report.status == "succeeded"
        assert report.written == 2  # both parsed deliveries are counted
        from apps.deliveries.models import Delivery
        from apps.matches.models import Match

        match = Match.objects.get(provider_match_id="999998")
        assert list(match.innings.values_list("innings_number", flat=True)) == [1]
        # Only the India innings' ball was written.
        assert Delivery.objects.filter(match_id=match.id, is_current=True).count() == 1

    def test_reconcile_skips_missing_expected_and_missing_summary(self) -> None:
        match = make_match()
        t1 = make_team("India")
        t2 = make_team("Australia")
        make_match_teams(match, t1, t2)
        i1 = make_innings(match, t1, t2, innings_number=1)
        make_innings(match, t2, t1, innings_number=2)

        # Innings 1 matches the parsed file but has no summary yet (226);
        # innings 2 exists in the DB but not in the parsed file (223).
        # Neither may raise.
        _reconcile(match, _parsed(), None)
        assert i1 is not None

    def test_reconcile_mismatch_still_writes_dlq(self) -> None:
        from apps.innings.models import InningsSummary

        match = make_match()
        t1 = make_team("India")
        t2 = make_team("Australia")
        make_match_teams(match, t1, t2)
        i1 = make_innings(match, t1, t2, innings_number=1)
        InningsSummary.objects.create(innings=i1, runs=999, wickets=1)

        _reconcile(match, _parsed(), None)

        from apps.ingestion.models import IngestionError

        (row,) = IngestionError.objects.filter(stage="reconcile")
        assert row.error_class == "reconciliation_mismatch"


# --------------------------------------------------------------------------- #
# Cricsheet parser branches (pure)
# --------------------------------------------------------------------------- #


class TestCricsheetParserBranches:
    def test_non_integer_revision_raises(self, tmp_path: Any) -> None:
        # The base card has no meta block; add a malformed one.
        text = _minimal_yaml().replace("info:", "meta:\n  revision: xyz\ninfo:")
        path = _write_yaml(tmp_path, text)
        with pytest.raises(CricsheetParseError, match="expected an integer"):
            parse_cricsheet_yaml(path)

    def test_wicket_without_player_out_raises(self, tmp_path: Any) -> None:
        text = _minimal_yaml().replace(
            "    wicket: false",
            "    wicket:\n      kind: caught",
        )
        path = _write_yaml(tmp_path, text)
        with pytest.raises(CricsheetParseError, match="kind and player_out"):
            parse_cricsheet_yaml(path)

    def test_missing_dates_raises(self, tmp_path: Any) -> None:
        text = _minimal_yaml().replace('  dates: ["2023-10-25"]', "  dates: []")
        path = _write_yaml(tmp_path, text)
        with pytest.raises(CricsheetParseError, match="missing dates"):
            parse_cricsheet_yaml(path)

    def test_bad_date_raises(self, tmp_path: Any) -> None:
        text = _minimal_yaml().replace('  dates: ["2023-10-25"]', '  dates: ["not-a-date"]')
        path = _write_yaml(tmp_path, text)
        with pytest.raises(CricsheetParseError, match="bad date"):
            parse_cricsheet_yaml(path)

    def test_delivery_missing_over_raises(self, tmp_path: Any) -> None:
        text = _minimal_yaml().replace("    over: 1\n", "")
        path = _write_yaml(tmp_path, text)
        with pytest.raises(CricsheetParseError, match="missing over"):
            parse_cricsheet_yaml(path)
