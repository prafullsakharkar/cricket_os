"""Match read API (G7): shell, scorecard, innings, deliveries.

Covers CRICKET_API_SPECIFICATION §3.2–§3.4 / §3.9 / §4.1–§4.2:

* ``GET /matches/{id}/`` — match shell + state, ``{"data": ...}`` envelope;
* ``GET /matches/{id}/scorecard`` — innings-indexed cards with
  ``meta.ball_data`` / ``meta.revision`` / ``meta.commentary=false``,
  ``?innings=N`` filter, 400 on out-of-range innings;
* ``GET /matches/{id}/innings`` — innings list & progression;
* ``GET /matches/{id}/innings/{n}/deliveries`` — ball-by-ball, cursor on
  ``delivery_seq`` (limit default 50 max 100, from_seq/to_seq/over filters),
  ``is_current=true`` rows only, partition-pruned on ``match_date``;
* state-dependent cache policy (§3.9): live ``max-age=5, swr=5`` vs terminal
  ``max-age=300, swr=600``, strong ETag + ``If-None-Match`` → ``304``;
* summary-only matches (``batch_complete=false``, no delivery rows) report
  ``meta.ball_data=false`` and their provider-written cards.

Matches are built the canonical way (``append_or_revise`` + ``rebuild_match``)
so the read path is exercised against engine-derived rows.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.deliveries.services.validation import DeliveryDraft
from apps.deliveries.services.write import append_or_revise
from apps.innings.models import Innings, InningsSummary
from apps.matches.selectors.reads import deliveries_queryset
from apps.scorecards.models import BattingEntry
from apps.scorecards.services.rebuild import rebuild_match

from .factories import (
    make_innings,
    make_match,
    make_match_teams,
    make_player,
    make_provider,
    make_team,
)


def _ball(
    seq: int,
    over: int,
    ball_in_over: int,
    runs: int = 0,
    *,
    bowler_id: Any,
    batter_id: Any,
    non_striker_id: Any,
    wicket: str | None = None,
) -> DeliveryDraft:
    kw: dict[str, object] = {
        "delivery_seq": seq,
        "over_number": over,
        "ball_in_over": ball_in_over,
        "runs_off_bat": runs,
    }
    if wicket:
        kw.update(
            is_wicket=True,
            dismissal_type=wicket,
            dismissed_player_id=batter_id,
            bowler_credited_wicket=True,
        )
    base = DeliveryDraft(
        delivery_seq=seq,
        over_number=over,
        ball_in_over=ball_in_over,
        bowler_id=bowler_id,
        batter_id=batter_id,
        non_striker_id=non_striker_id,
    )
    return replace(base, **kw)  # type: ignore[arg-type]


def _load(innings: Innings, ctx: dict[str, Any], specs: list[tuple[int, int, int]]) -> None:
    drafts = [
        _ball(
            seq,
            over,
            ball,
            runs,
            bowler_id=ctx["bowler"].id,
            batter_id=ctx["batter"].id,
            non_striker_id=ctx["non_striker"].id,
        )
        for seq, (over, ball, runs) in enumerate(specs, start=1)
    ]
    append_or_revise(innings, drafts, source=ctx["provider"], suppress_events=True)


@pytest.mark.django_db(transaction=True)
class TestCompletedMatch:
    """A finished match with two innings of deliveries (India 6/0 → Aus 18/0)."""

    @pytest.fixture(autouse=True)
    def _build_match(self) -> None:
        ctx: dict[str, Any] = {
            "provider": make_provider(),
            "match": make_match(),
            "t1": make_team("India"),
            "t2": make_team("Australia"),
            "bowler": make_player("Jasprit Bumrah"),
            "batter": make_player("Virat Kohli"),
            "non_striker": make_player("Rohit Sharma"),
        }
        make_match_teams(ctx["match"], ctx["t1"], ctx["t2"])
        ctx["i1"] = make_innings(ctx["match"], ctx["t1"], ctx["t2"], innings_number=1)
        ctx["i2"] = make_innings(ctx["match"], ctx["t2"], ctx["t1"], innings_number=2)
        _load(ctx["i1"], ctx, [(1, 1, 1), (1, 2, 1), (1, 3, 1), (1, 4, 1), (1, 5, 1), (1, 6, 1)])
        _load(ctx["i2"], ctx, [(1, 1, 4), (1, 2, 4), (1, 3, 4), (1, 4, 4), (1, 5, 1), (1, 6, 1)])
        rebuild_match(ctx["match"])
        ctx["match"].refresh_from_db()
        self.ctx = ctx  # type: ignore[attr-defined]
        self.client = APIClient()  # type: ignore[attr-defined]

    @property
    def match_url(self) -> str:
        return f"/api/v1/matches/{self.ctx['match'].id}/"  # type: ignore[attr-defined]

    # -- match shell -------------------------------------------------------- #
    def test_match_shell_envelope_and_shape(self) -> None:
        response = self.client.get(self.match_url)  # type: ignore[attr-defined]
        assert response.status_code == 200
        data = response.json()["data"]

        assert data["id"] == str(self.ctx["match"].id)
        assert data["format"] == "T20I"
        assert data["status"] == "COMPLETED"
        assert data["result"]["type"] == "WIN"
        assert data["result"]["winner"]["name"] == "Australia"
        assert data["result"]["margin"] == {"type": "wickets", "value": 10}
        assert data["toss"]["decision"] is None  # no toss written in this scenario
        assert [t["team"]["name"] for t in data["teams"]] == ["India", "Australia"]
        scores = {t["team"]["name"]: t["score"] for t in data["teams"]}
        assert scores["India"]["runs"] == 6
        assert scores["Australia"]["runs"] == 18

    def test_unknown_match_returns_404_envelope(self) -> None:
        response = self.client.get(  # type: ignore[attr-defined]
            "/api/v1/matches/00000000-0000-0000-0000-000000000000/"
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    # -- scorecard ---------------------------------------------------------- #
    def test_scorecard_innings_indexed_cards(self) -> None:
        response = self.client.get(f"{self.match_url}scorecard")  # type: ignore[attr-defined]
        assert response.status_code == 200
        data = response.json()["data"]

        assert data["meta"] == {
            "ball_data": True,
            "commentary": False,
            "revision": self.ctx["match"].revision,
            "event_seq": 0,
        }
        assert [i["innings_number"] for i in data["innings"]] == [1, 2]

        innings_1 = data["innings"][0]
        assert innings_1["batting_team"]["name"] == "India"
        assert innings_1["totals"]["runs"] == 6
        assert innings_1["totals"]["wickets"] == 0

        kohli = [b for b in innings_1["batting"] if b["player"]["name"] == "Virat Kohli"]
        assert kohli and kohli[0]["runs"] == 6 and kohli[0]["balls"] == 6
        assert kohli[0]["is_out"] is False and kohli[0]["dismissal"] is None

        (bumrah_line,) = [
            b for b in innings_1["bowling"] if b["player"]["name"] == "Jasprit Bumrah"
        ]
        assert (bumrah_line["overs"], bumrah_line["runs_conceded"]) == ("1.0", 6)

        # A closed innings 1 never completes the match by itself: no FOW here.
        assert innings_1["fall_of_wickets"] == []

    def test_scorecard_innings_filter(self) -> None:
        response = self.client.get(f"{self.match_url}scorecard?innings=2")  # type: ignore[attr-defined]
        data = response.json()["data"]
        assert [i["innings_number"] for i in data["innings"]] == [2]
        assert data["innings"][0]["batting_team"]["name"] == "Australia"

    def test_scorecard_innings_out_of_range_is_400(self) -> None:
        response = self.client.get(f"{self.match_url}scorecard?innings=5")  # type: ignore[attr-defined]
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_query"

    # -- innings list ------------------------------------------------------- #
    def test_innings_list_and_progression(self) -> None:
        response = self.client.get(f"{self.match_url}innings")  # type: ignore[attr-defined]
        assert response.status_code == 200
        data = response.json()["data"]

        assert data["match_id"] == str(self.ctx["match"].id)
        assert data["meta"]["ball_data"] is True
        assert data["meta"]["commentary"] is False
        assert [i["innings_number"] for i in data["innings"]] == [1, 2]
        first = data["innings"][0]
        assert first["batting_team"]["name"] == "India"
        assert first["bowling_team"]["name"] == "Australia"
        assert first["totals"]["runs"] == 6

    # -- deliveries --------------------------------------------------------- #
    def test_deliveries_default_page_and_meta(self) -> None:
        url = f"{self.match_url}innings/1/deliveries"
        response = self.client.get(url)  # type: ignore[attr-defined]
        assert response.status_code == 200
        payload = response.json()

        assert payload["meta"]["ball_data"] is True
        assert payload["meta"]["commentary"] is False
        assert payload["meta"]["limit"] == 50
        assert payload["meta"]["has_more"] is False
        assert payload["meta"]["next_cursor"] is None

        rows = payload["data"]
        assert [r["delivery_seq"] for r in rows] == list(range(1, 7))
        first = rows[0]
        assert first["over"] == 1 and first["ball"] == 1
        assert first["bowler"]["name"] == "Jasprit Bumrah"
        assert first["batter"]["name"] == "Virat Kohli"
        assert first["runs"] == {
            "total": 1,
            "batter": 1,
            "extras": 0,
            "wides": 0,
            "noballs": 0,
            "byes": 0,
            "leg_byes": 0,
            "penalty": 0,
        }
        assert first["wicket"] is None
        assert first["retirement"] is None
        assert first["revision"] == 1

    def test_deliveries_cursor_pagination(self) -> None:
        url = f"{self.match_url}innings/1/deliveries"
        page_1 = self.client.get(f"{url}?limit=5")  # type: ignore[attr-defined]
        body = page_1.json()
        assert body["meta"]["limit"] == 5
        assert body["meta"]["has_more"] is True
        assert [r["delivery_seq"] for r in body["data"]] == [1, 2, 3, 4, 5]
        cursor = body["meta"]["next_cursor"]
        assert cursor is not None

        page_2 = self.client.get(f"{url}?limit=5&cursor={cursor}")  # type: ignore[attr-defined]
        body_2 = page_2.json()
        assert [r["delivery_seq"] for r in body_2["data"]] == [6]
        assert body_2["meta"]["has_more"] is False
        assert body_2["meta"]["next_cursor"] is None

    def test_deliveries_seq_and_over_filters(self) -> None:
        url = f"{self.match_url}innings/1/deliveries"
        seqs = self.client.get(f"{url}?from_seq=2&to_seq=4").json()["data"]  # type: ignore[attr-defined]
        assert [r["delivery_seq"] for r in seqs] == [2, 3, 4]

        # All deliveries are in over 1; a nonexistent over is empty.
        over_1 = self.client.get(f"{url}?over=1").json()["data"]  # type: ignore[attr-defined]
        over_2 = self.client.get(f"{url}?over=2").json()["data"]  # type: ignore[attr-defined]
        assert len(over_1) == 6
        assert over_2 == []

    def test_deliveries_bad_params_are_400(self) -> None:
        url = f"{self.match_url}innings/1/deliveries"
        for query in ("?limit=abc", "?from_seq=0", "?cursor=!!!", "?over=0"):
            response = self.client.get(f"{url}{query}")  # type: ignore[attr-defined]
            assert response.status_code == 400, query
            assert response.json()["error"]["code"] == "invalid_query", query

    def test_deliveries_unknown_innings_is_404(self) -> None:
        response = self.client.get(f"{self.match_url}innings/9/deliveries")  # type: ignore[attr-defined]
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    def test_deliveries_reads_current_rows_only(self) -> None:
        # Correct ball 6 (1 run -> 4): the old revision 1 row is superseded.
        ctx = self.ctx  # type: ignore[attr-defined]
        corrected = _ball(
            6,
            1,
            6,
            4,
            bowler_id=ctx["bowler"].id,
            batter_id=ctx["batter"].id,
            non_striker_id=ctx["non_striker"].id,
        )
        append_or_revise(ctx["i1"], [corrected], source=ctx["provider"], suppress_events=True)
        rebuild_match(ctx["match"])

        rows = self.client.get(f"{self.match_url}innings/1/deliveries").json()["data"]  # type: ignore[attr-defined]
        assert len(rows) == 6  # superseded row is not returned
        assert rows[-1]["runs"]["total"] == 4
        assert rows[-1]["revision"] == 2

    def test_deliveries_query_prunes_on_match_date_partition(self) -> None:
        ctx = self.ctx  # type: ignore[attr-defined]
        qs = deliveries_queryset(ctx["match"], ctx["i1"])
        sql = str(qs.query)
        assert "match_date" in sql
        assert "is_current" in sql

    # -- cache policy ------------------------------------------------------- #
    def test_completed_match_cache_policy_and_etag_304(self) -> None:
        response = self.client.get(self.match_url)  # type: ignore[attr-defined]
        assert response["Cache-Control"] == (
            "public, max-age=300, s-maxage=3600, stale-while-revalidate=600, "
            "stale-if-error=86400"
        )
        etag = response["ETag"]
        assert etag.startswith('"') and etag.endswith('"')

        cached = self.client.get(self.match_url, HTTP_IF_NONE_MATCH=etag)  # type: ignore[attr-defined]
        assert cached.status_code == 304
        assert cached.content == b""
        assert cached["Cache-Control"] == response["Cache-Control"]


@pytest.mark.django_db(transaction=True)
class TestLiveAndSummaryOnly:
    """Live matches use seconds-level TTLs; summary-only matches lack ball data."""

    def test_live_match_uses_seconds_level_ttl(self) -> None:
        match = make_match()
        t1 = make_team("India")
        t2 = make_team("Australia")
        make_match_teams(match, t1, t2)
        innings = make_innings(match, t1, t2, innings_number=1)
        provider = make_provider()
        bowler = make_player("Jasprit Bumrah")
        batter = make_player("Virat Kohli")
        non_striker = make_player("Rohit Sharma")
        drafts = [
            _ball(
                1,
                1,
                1,
                1,
                bowler_id=bowler.id,
                batter_id=batter.id,
                non_striker_id=non_striker.id,
            )
        ]
        append_or_revise(innings, drafts, source=provider, suppress_events=True)
        # No rebuild: the match stays live (SCHEDULED).
        match.refresh_from_db()

        response = APIClient().get(f"/api/v1/matches/{match.id}/")
        assert response.status_code == 200
        assert response["Cache-Control"] == "public, max-age=5, stale-while-revalidate=5"
        assert response.json()["data"]["status"] == "SCHEDULED"

    def test_summary_only_match_scorecard_reports_no_ball_data(self) -> None:
        match = make_match()
        t1 = make_team("India")
        t2 = make_team("Australia")
        make_match_teams(match, t1, t2)
        i1 = make_innings(match, t1, t2, innings_number=1)
        i2 = make_innings(match, t2, t1, innings_number=2)
        InningsSummary.objects.create(innings=i1, runs=150, wickets=5, batch_complete=False)
        InningsSummary.objects.create(innings=i2, runs=151, wickets=4, batch_complete=False)
        batter = make_player("Virat Kohli")
        BattingEntry.objects.create(innings=i1, player=batter, runs=55, balls=40)

        response = APIClient().get(f"/api/v1/matches/{match.id}/scorecard")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["meta"]["ball_data"] is False
        assert data["meta"]["commentary"] is False
        assert data["innings"][0]["totals"]["runs"] == 150
        names = [b["player"]["name"] for b in data["innings"][0]["batting"]]
        assert names == ["Virat Kohli"]

        # The innings endpoint reports the same ball_data flag.
        innings = APIClient().get(f"/api/v1/matches/{match.id}/innings").json()["data"]
        assert innings["meta"]["ball_data"] is False
