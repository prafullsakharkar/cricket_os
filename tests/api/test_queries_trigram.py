"""Query-count ceilings and the trigram-operator contract (Task 7.4).

The search selectors must compile to the ``%`` operator — the index-backed
form that uses the GIN trigram indexes (``ix_team_name_trgm`` /
``ix_player_name_trgm``). A ``similarity(col, val) >= x`` predicate cannot use
those indexes (verified on PG 17.11: it plans a sequential scan), so the
regression guard is the compiled SQL, not the planner's chosen access path
(which varies with table statistics — with ``enable_seqscan=off`` the planner
may prefer a btree index scan once FK-column stats are populated).
"""

from __future__ import annotations

from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.players.selectors import list_players
from apps.teams.selectors import list_teams
from tests.api.conftest import SERIES

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


class TestQueryCounts:
    def test_teams_list_at_most_two_queries(self, api_client: Any, seed_reference: Any) -> None:
        with CaptureQueriesContext(connection) as queries:
            api_client.get("/api/v1/teams")
        assert len(queries) <= 2  # data + count

    def test_team_detail_at_most_two_queries(self, api_client: Any, seed_reference: Any) -> None:
        with CaptureQueriesContext(connection) as queries:
            api_client.get(f"/api/v1/teams/{seed_reference.team.pk}/")
        assert len(queries) <= 2

    def test_players_list_at_most_four_queries(self, api_client: Any, seed_reference: Any) -> None:
        with CaptureQueriesContext(connection) as queries:
            api_client.get("/api/v1/players")
        assert len(queries) <= 4

    def test_players_list_with_q_at_most_four_queries(
        self, api_client: Any, seed_reference: Any
    ) -> None:
        with CaptureQueriesContext(connection) as queries:
            api_client.get("/api/v1/players?q=davison")
        assert len(queries) <= 4

    def test_player_detail_at_most_three_queries(
        self, api_client: Any, seed_reference: Any
    ) -> None:
        with CaptureQueriesContext(connection) as queries:
            api_client.get(f"/api/v1/players/{seed_reference.player.pk}/")
        assert len(queries) <= 3

    def test_series_detail_at_most_three_queries(
        self, api_client: Any, seed_reference: Any
    ) -> None:
        with CaptureQueriesContext(connection) as queries:
            api_client.get(f"/api/v1/series/{SERIES}/")
        assert len(queries) <= 3

    def test_competition_detail_at_most_three_queries(
        self, api_client: Any, seed_reference: Any
    ) -> None:
        with CaptureQueriesContext(connection) as queries:
            api_client.get(f"/api/v1/competitions/{seed_reference.competition.pk}/")
        assert len(queries) <= 3


class TestTrigramOperator:
    """The search selectors compile to ``%`` (GIN-indexable), not the
    ``similarity() >= x`` predicate. Django's ``as_sql()`` escapes the
    operator as ``%%``; the placeholder follows (``%s``).
    """

    @staticmethod
    def _sql(queryset: Any) -> str:
        compiler = queryset.query.get_compiler(connection=connection)
        sql, _params = compiler.as_sql()
        return sql

    def test_team_q_uses_trigram_operator(self) -> None:
        sql = self._sql(list_teams(q="canada"))
        assert '"name" %% %s' in sql, sql
        assert "similarity(" in sql.lower()  # ranking annotation retained

    def test_player_q_uses_trigram_operator(self) -> None:
        sql = self._sql(list_players(q="davison"))
        assert '"full_name" %% %s' in sql, sql
        assert "similarity(" in sql.lower()

    def test_competition_q_uses_trigram_operator(self) -> None:
        from apps.competitions.selectors import list_competitions

        sql = self._sql(list_competitions(q="cup"))
        assert '"name" %% %s' in sql, sql
        assert "similarity(" in sql.lower()


class TestCursorStability:
    def test_no_rows_skipped_or_duplicated_across_pages(self, api_client: Any) -> None:
        from apps.players.models import Player

        for i in range(12):
            Player.objects.create(full_name=f"Player {i:02d}", gender="M")

        page1 = api_client.get("/api/v1/players?limit=5").json()
        assert page1["meta"]["has_more"] is True

        # Insert a row that sorts early between pages (the keyset boundary must
        # keep the second page stable regardless).
        Player.objects.create(full_name="Player 03bis", gender="M")

        page2 = api_client.get(
            f"/api/v1/players?limit=5&cursor={page1['meta']['next_cursor']}"
        ).json()

        ids1 = [row["id"] for row in page1["data"]]
        ids2 = [row["id"] for row in page2["data"]]
        assert len(ids2) == 5
        # No duplicate within or across pages, and no row sorted before the
        # boundary appears in page 2 (the inserted 03bis sorts inside page 1).
        assert len(set(ids1) & set(ids2)) == 0
        assert len(set(ids1)) == len(ids1)
        names1 = [row["full_name"] for row in page1["data"]]
        names2 = [row["full_name"] for row in page2["data"]]
        assert "Player 03bis" not in names2
        # Page 2 continues strictly after page 1's last name.
        assert names2[0] > names1[-1]


__all__: list[str] = []
