"""Pagination-style audit across every public list endpoint (API_SPEC §5).

Final rule (recorded in the Phase E ledger — the roadmap's "cursor
pagination everywhere" loses to the spec):

* **cursor** for time-ordered feeds: ``/matches/live|upcoming|recent``,
  ``/matches/{id}/innings/{n}/deliveries``, ``/players`` without ``?q=``;
* **page-number** for bounded reference tables and ranked search:
  ``/teams``, ``/venues``, ``/competitions``, ``/series``, ``/players?q=``.

The audit asserts the meta keys each endpoint actually emits so a future
regression cannot silently swap styles.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.deliveries.services.validation import DeliveryDraft
from apps.deliveries.services.write import append_or_revise
from apps.scorecards.services.rebuild import rebuild_match
from tests.phase_c.factories import (
    make_innings,
    make_match,
    make_match_teams,
    make_player,
    make_provider,
    make_team,
)

pytestmark = pytest.mark.django_db(transaction=True)

CURSOR_KEYS = {"limit", "has_more", "next_cursor"}
PAGE_KEYS = {"page", "page_size", "total_count", "total_pages"}


def _is_cursor(meta: dict[str, Any]) -> bool:
    # ``has_more`` is shared by both styles; cursor is identified by
    # ``next_cursor`` and the absence of page-number keys.
    return "next_cursor" in meta and not PAGE_KEYS & set(meta)


def _is_page(meta: dict[str, Any]) -> bool:
    return set(meta) >= PAGE_KEYS and "next_cursor" not in meta


@pytest.fixture
def feeds() -> dict[str, Any]:
    """One live, one upcoming, one recent match plus a match with deliveries."""
    live = make_match(status="IN_PROGRESS")
    upcoming = make_match(status="SCHEDULED")
    recent = make_match(status="COMPLETED")

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
    innings = make_innings(ctx["match"], ctx["t1"], ctx["t2"], innings_number=1)
    drafts = [
        replace(
            DeliveryDraft(
                delivery_seq=seq,
                over_number=1,
                ball_in_over=ball,
                bowler_id=ctx["bowler"].id,
                batter_id=ctx["batter"].id,
                non_striker_id=ctx["non_striker"].id,
            ),
            runs_off_bat=runs,
        )
        for seq, (ball, runs) in enumerate([(1, 1), (2, 1), (3, 1), (4, 1)], start=1)
    ]
    append_or_revise(innings, drafts, source=ctx["provider"], suppress_events=True)
    rebuild_match(ctx["match"])
    return {"live": live, "upcoming": upcoming, "recent": recent, "ctx": ctx}


def test_time_ordered_feeds_use_cursor_pagination(feeds: dict[str, Any]) -> None:
    client = APIClient()
    for path in ("/api/v1/matches/live", "/api/v1/matches/upcoming", "/api/v1/matches/recent"):
        body = client.get(path, data={"limit": 5}).json()
        assert _is_cursor(body["meta"]), path
        assert "page" not in body["meta"], path


def test_deliveries_feed_uses_cursor_pagination(feeds: dict[str, Any]) -> None:
    ctx = feeds["ctx"]
    body = (
        APIClient()
        .get(f"/api/v1/matches/{ctx['match'].id}/innings/1/deliveries", data={"limit": 2})
        .json()
    )
    assert _is_cursor(body["meta"])


def test_reference_lists_use_page_number_pagination() -> None:
    client = APIClient()
    for path in (
        "/api/v1/teams",
        "/api/v1/venues",
        "/api/v1/competitions",
        "/api/v1/series",
    ):
        body = client.get(path).json()
        assert _is_page(body["meta"]), path
        assert "next_cursor" not in body["meta"], path


def test_player_browser_uses_cursor_and_ranked_search_uses_pages() -> None:
    # Spec §3.5: browsing is keyset on (full_name, id); ranked search is
    # page-number on trigram similarity.
    browse = APIClient().get("/api/v1/players").json()
    assert _is_cursor(browse["meta"])
    ranked = APIClient().get("/api/v1/players", data={"q": "Virat"}).json()
    assert _is_page(ranked["meta"])
