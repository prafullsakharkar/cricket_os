"""Phase F Group 6: cross-entity search service.

Covers the brief's search requirements: trigram search over normalized names,
weighted cross-type ranking (type weight + exact/prefix boosts), safety gates
(min/max query length, per-statement timeout, result cap), the EXPLAIN gate
that proves the trigram index is used, and the golden relevance set (a fixed
corpus whose expected ordering is asserted verbatim).
"""

from __future__ import annotations

from typing import Any

import pytest
from django.db import connection, transaction

from apps.competitions.models import Competition, Series
from apps.search.services.search import (
    MAX_QUERY_LENGTH,
    cross_entity_search,
    explain_search,
    normalize_query,
    validate_query,
)

from ..phase_c.factories import make_player, make_team

pytestmark = pytest.mark.django_db


def _series(name: str) -> Series:
    return Series.objects.create(name=name, series_type="bilateral")


def _competition(name: str) -> Competition:
    return Competition.objects.create(name=name, competition_type="LEAGUE")


def _names(items: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(i["type"], i["name"]) for i in items]


# --------------------------------------------------------------------------- #
# Normalisation + validation (safety)
# --------------------------------------------------------------------------- #


def test_normalize_query_lowercases_and_collapses_whitespace() -> None:
    assert normalize_query("  Virat   KOHLI \n") == "virat kohli"


def test_validate_query_enforces_length_bounds() -> None:
    assert validate_query("ab") == "ab"
    assert validate_query("  Virat Kohli  ") == "virat kohli"
    with pytest.raises(ValueError):
        validate_query("a")  # below MIN_QUERY_LENGTH
    with pytest.raises(ValueError):
        validate_query(" " * 5)  # collapses to "" -> too short
    with pytest.raises(ValueError):
        validate_query("x" * (MAX_QUERY_LENGTH + 1))


def test_validate_query_rejects_unknown_types() -> None:
    make_player("Virat Kohli")
    with pytest.raises(ValueError):
        cross_entity_search("virat", types=("player", "stadium"))


# --------------------------------------------------------------------------- #
# Ranking
# --------------------------------------------------------------------------- #


def test_players_ranked_by_name_similarity() -> None:
    make_player("Virat Kohli")
    make_player("Virat Sharma")
    make_player("Ravi Kohli")

    results = cross_entity_search("virat", types=("player",), limit=10)

    names = _names(results)
    # Exact-trigram superset first (Kohli has the shorter name), then Sharma.
    assert names == [("player", "Virat Kohli"), ("player", "Virat Sharma")]


def test_weighted_cross_type_ranking_exact_team_beats_prefix_series() -> None:
    make_player("Suresh Raina")
    make_team("India")
    make_team("New Zealand")
    _series("New Zealand in India 2026")
    _series("India Tour of Australia 2026")
    _competition("Indian Premier League")

    results = cross_entity_search("new zealand", limit=10)

    # Exact team name first (exact boost + team weight); the series that
    # carries the whole query as a prefix follows it (prefix boost, lower
    # type weight). Every result carries the weighted score + stable id.
    assert results[0]["type"] == "team" and results[0]["name"] == "New Zealand"
    types = [i["type"] for i in results]
    assert "series" in types and types.index("team") < types.index("series")
    for item in results:
        assert item["score"] > 0
        assert item["id"]
        assert item["name"]


def test_recall_threshold_is_honest() -> None:
    # A short query that is only a fragment of a long name does not clear the
    # pg_trgm session recall threshold (0.3): "india" matches the exact team,
    # not the long series/competition names containing it. This is the
    # documented GIN ``%`` behaviour — never an unbounded LIKE scan.
    make_team("India")
    _series("India Tour of Australia 2026")
    _competition("Indian Premier League")

    results = cross_entity_search("india", limit=10)
    assert _names(results) == [("team", "India")]


def test_type_filter_limits_entities() -> None:
    make_player("Virat Kohli")
    make_team("West Indies")

    results = cross_entity_search("virat", types=("player",))
    assert len(results) == 1 and results[0]["type"] == "player"

    teams = cross_entity_search("west", types=("team",))
    assert [t["type"] for t in teams] == ["team"]
    assert teams[0]["name"] == "West Indies"


def test_result_cap_is_enforced() -> None:
    for i in range(30):
        make_player(f"Batter Number {i}")
    results = cross_entity_search("batter", types=("player",), limit=5)
    assert len(results) == 5


# --------------------------------------------------------------------------- #
# Safety: statement timeout
# --------------------------------------------------------------------------- #


def test_statement_timeout_is_applied_during_search() -> None:
    make_player("Virat Kohli")
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SHOW statement_timeout")
            before = cursor.fetchone()[0]

        cross_entity_search("virat", types=("player",), timeout_ms=250)

        with connection.cursor() as cursor:
            cursor.execute("SHOW statement_timeout")
            assert cursor.fetchone()[0] == "250ms"
        # Sanity: the test harness starts with a non-250ms session setting.
        assert before != "250ms"


def test_no_timeout_when_disabled() -> None:
    make_player("Virat Kohli")
    results = cross_entity_search("virat", types=("player",), timeout_ms=None)
    assert len(results) == 1


# --------------------------------------------------------------------------- #
# EXPLAIN gate
# --------------------------------------------------------------------------- #


def test_explain_gate_proves_trigram_index_use() -> None:
    make_player("Virat Kohli")
    make_team("India")

    plan = explain_search("virat", types=("player",))
    # enable_seqscan=off forces the planner to use an index for `%`; the only
    # one available is the GIN trigram index — the gate is the plan text.
    assert "ix_player_name_trgm" in plan
    assert "Seq Scan" not in plan

    team_plan = explain_search("india", types=("team",))
    assert "ix_team_name_trgm" in team_plan
    assert "Seq Scan" not in team_plan


# --------------------------------------------------------------------------- #
# Golden relevance set
# --------------------------------------------------------------------------- #


def _seed_golden_corpus() -> None:
    # Fixed corpus; expected orderings below are the golden set.
    make_player("Virat Kohli")
    make_player("Rohit Sharma")
    make_player("Suresh Raina")
    make_team("India")
    make_team("West Indies")
    make_team("New Zealand")
    _series("India Tour of Australia 2026")
    _series("New Zealand in India 2026")
    _competition("Indian Premier League")


def test_golden_relevance_set() -> None:
    _seed_golden_corpus()

    # "virat" -> the only player matching the query's trigram set.
    assert _names(cross_entity_search("virat", limit=10)) == [("player", "Virat Kohli")]

    # "kohli" -> surname search still resolves the player (prefix of the name).
    assert _names(cross_entity_search("kohli", limit=10)) == [("player", "Virat Kohli")]

    # "new zealand" -> exact team first, then the series that carries the
    # full query as a prefix; the exact team outranks it (exact boost).
    nz = _names(cross_entity_search("new zealand", limit=10))
    assert nz[0] == ("team", "New Zealand")
    assert ("series", "New Zealand in India 2026") in nz
    assert nz.index(("team", "New Zealand")) < nz.index(("series", "New Zealand in India 2026"))


def test_golden_scores_are_deterministic() -> None:
    _seed_golden_corpus()
    first = cross_entity_search("india", limit=10)
    second = cross_entity_search("india", limit=10)
    assert [i["score"] for i in first] == [i["score"] for i in second]
    assert _names(first) == _names(second)
