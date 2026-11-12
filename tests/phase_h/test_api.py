"""Fantasy public read API (Task 7).

Three read-only endpoints behind ``FANTASY_PUBLIC_API_ENABLED`` (off by
default; the product/legal gate is ABSENT):

* ``GET /api/v1/fantasy/rulesets`` (+ version detail) — public (non-draft)
  ruleset catalogue, ``max-age=3600``, ``?fields=`` sparse fields;
* ``GET /api/v1/fantasy/points`` — page-number paginated per-player rows with
  ``match`` / ``player`` / ``ruleset_version`` filters, ETag/304;
* ``GET /api/v1/fantasy/matches/{id}`` — all players for the primary version,
  ordered, component breakdown, ``max-age=120``.

Contract: every response carries ``meta`` with ``label: "derived"`` and the
third-party notice; points responses carry the calc identity (``calc_run_id``,
``status`` final|provisional, ``is_partial``, ``unsupported_rules``, ``as_of``,
``ruleset`` id/version/hash). Draft versions are never public. Merged players
answer 301. Flag-off answers 404. Reads never touch ``delivery``.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from django.contrib.auth.models import Group, User
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from apps.fantasy import authoring
from apps.fantasy.api.views import FANTASY_LABEL, FANTASY_NOTICE
from apps.fantasy.engine import point_rule
from apps.fantasy.models import FantasyCalcAudit
from apps.fantasy.services import recompute
from apps.players.models import PlayerMerge
from apps.statistics.models import PlayerMatchStat

from ..phase_c.factories import make_player
from .factories import add_stat, make_completed_match, make_ruleset_version

pytestmark = pytest.mark.django_db

RULES = (point_rule("runs", "per_unit", "1.00", "batting"),)

_USER_SEQ = 0


def _admin() -> User:
    global _USER_SEQ
    _USER_SEQ += 1
    user = User.objects.create_user(username=f"fantasy-admin-{_USER_SEQ}", password="pw")
    user.groups.add(Group.objects.get(name="admin"))
    return user


def _frozen_default_version(
    *, format_scope: str = "T20I", rules: tuple = RULES
) -> tuple[object, object]:
    """A real authoring-created version: default set on the draft, then frozen."""
    ruleset = authoring.create_ruleset(name="public ruleset", actor=_admin())
    version = authoring.create_version(
        ruleset=ruleset, rules=rules, format_scope=format_scope, actor=_admin()
    )
    authoring.set_default_version(version, actor=_admin())
    authoring.freeze_version(version, actor=_admin())
    return ruleset, version


def _seed(*, format_type: str = "T20I", runs: int = 58, balls: int = 42) -> tuple:
    """A completed match with one player, computed under a frozen default version."""
    match, team1, _ = make_completed_match(format_type=format_type)
    player, _ = add_stat(match, team1, runs=runs, balls=balls, out=True)
    ruleset, version = _frozen_default_version(format_scope=format_type)
    recompute.recompute_match(match)
    return match, team1, player, ruleset, version


# --------------------------------------------------------------------------- #
# Rulesets catalogue
# --------------------------------------------------------------------------- #


class TestRulesets:
    def test_list_golden_envelope(self, api_client: object) -> None:
        _match, _team1, _player, ruleset, version = _seed()

        response = api_client.get("/api/v1/fantasy/rulesets")

        assert response.status_code == 200
        assert "max-age=3600" in response["Cache-Control"]
        body = response.json()
        assert body["meta"]["label"] == FANTASY_LABEL
        assert body["meta"]["notice"] == FANTASY_NOTICE
        assert body["meta"]["count"] == 1
        entry = body["data"][0]
        assert entry["id"] == str(ruleset.id)
        assert entry["name"] == "public ruleset"
        assert entry["provider_scope"] is None
        v = entry["versions"][0]
        assert v["id"] == str(version.id)
        assert v["version"] == 1
        assert v["format_scope"] == "T20I"
        assert v["status"] == "frozen"
        assert v["is_default"] is True
        assert v["auto_recompute"] is True
        assert v["content_hash"] == version.content_hash
        assert v["policies"] == {
            "super_over": "exclude",
            "abandoned": "no_points",
            "reduced_overs": "compute",
            "retirement_hurt": "not_out",
            "substitutes": "exclude",
        }
        datetime.fromisoformat(v["effective_from"])

    def test_drafts_never_public(self, api_client: object) -> None:
        from .factories import make_ruleset_version

        make_ruleset_version()  # a draft version (never frozen)
        response = api_client.get("/api/v1/fantasy/rulesets")
        body = response.json()
        assert body["data"] == []  # the draft ruleset is not in the public catalogue

    def test_list_sparse_fields(self, api_client: object) -> None:
        _seed()
        ok = api_client.get("/api/v1/fantasy/rulesets?fields=id,name")
        assert ok.status_code == 200
        assert set(ok.json()["data"][0]) == {"id", "name"}
        bad = api_client.get("/api/v1/fantasy/rulesets?fields=id,bogus")
        assert bad.status_code == 400
        assert bad.json()["error"]["code"] == "invalid_query"

    def test_version_detail_includes_rules(self, api_client: object) -> None:
        _match, _team1, _player, ruleset, version = _seed()

        response = api_client.get(
            f"/api/v1/fantasy/rulesets/{ruleset.id}/versions/{version.version}"
        )

        assert response.status_code == 200
        assert "max-age=3600" in response["Cache-Control"]
        body = response.json()
        data = body["data"]
        assert data["id"] == str(version.id)
        assert data["content_hash"] == version.content_hash
        assert body["meta"]["ruleset"]["id"] == str(ruleset.id)
        assert body["meta"]["ruleset"]["name"] == "public ruleset"
        rule = data["rules"][0]
        assert rule["event_type"] == "runs"
        assert rule["role_scope"] == "batting"
        assert rule["points"] == "1.00"
        assert rule["condition"] == {"operator": "per_unit"}

    def test_version_detail_draft_404(self, api_client: object) -> None:
        from .factories import make_ruleset_version

        _ruleset, version = make_ruleset_version()
        response = api_client.get(
            f"/api/v1/fantasy/rulesets/{version.ruleset_id}/versions/{version.version}"
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    def test_rulesets_etag_and_304(self, api_client: object) -> None:
        _seed()
        first = api_client.get("/api/v1/fantasy/rulesets")
        etag = first["ETag"]
        assert first.status_code == 200
        second = api_client.get("/api/v1/fantasy/rulesets", HTTP_IF_NONE_MATCH=etag)
        assert second.status_code == 304


# --------------------------------------------------------------------------- #
# /fantasy/points
# --------------------------------------------------------------------------- #


class TestPoints:
    def test_golden_envelope_and_meta(self, api_client: object) -> None:
        match, _team1, player, ruleset, version = _seed()

        response = api_client.get(f"/api/v1/fantasy/points?match={match.id}")

        assert response.status_code == 200
        body = response.json()
        row = body["data"][0]
        assert row["match_id"] == str(match.id)
        assert row["player_id"] == str(player.id)
        assert row["player_name"] == player.full_name
        assert row["ruleset_version_id"] == str(version.id)
        assert row["version"] == 1
        assert row["format_scope"] == "T20I"
        assert row["total_points"] == "58.00"
        assert row["breakdown"] == {"runs": "58.00"}
        audit = FantasyCalcAudit.objects.get(match_id=match.id, ruleset_version_id=version.id)
        assert row["calc_run_id"] == str(audit.id)
        assert row["status"] == "final"
        assert row["is_partial"] is False
        assert row["unsupported_rules"] == []
        datetime.fromisoformat(row["computed_at"])

        meta = body["meta"]
        assert meta["label"] == FANTASY_LABEL
        assert meta["notice"] == FANTASY_NOTICE
        assert meta["calc_run_id"] == str(audit.id)
        assert meta["status"] == "final"
        assert meta["is_partial"] is False
        assert meta["unsupported_rules"] == []
        assert meta["ruleset"] == {
            "id": str(ruleset.id),
            "version": 1,
            "hash": version.content_hash,
        }
        datetime.fromisoformat(meta["as_of"])
        assert meta["page"] == 1
        assert meta["page_size"] == 25
        assert meta["total_count"] == 1
        assert meta["total_pages"] == 1
        assert meta["has_more"] is False

    def test_requires_at_least_one_filter(self, api_client: object) -> None:
        response = api_client.get("/api/v1/fantasy/points")
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_query"

    def test_filters_by_player_and_ruleset_version(self, api_client: object) -> None:
        match, _team1, player, _ruleset, version = _seed()

        by_player = api_client.get(f"/api/v1/fantasy/points?player={player.id}")
        assert by_player.status_code == 200
        assert by_player.json()["data"][0]["match_id"] == str(match.id)

        by_version = api_client.get(
            f"/api/v1/fantasy/points?match={match.id}&ruleset_version={version.id}"
        )
        assert by_version.status_code == 200
        assert by_version.json()["data"][0]["total_points"] == "58.00"

    def test_bad_filter_uuid_400(self, api_client: object) -> None:
        response = api_client.get("/api/v1/fantasy/points?match=not-a-uuid")
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_query"

    def test_draft_version_filter_404(self, api_client: object) -> None:
        from .factories import make_ruleset_version

        match, _team1, _player, _ruleset, _version = _seed()
        _draft_ruleset, draft = make_ruleset_version()
        response = api_client.get(
            f"/api/v1/fantasy/points?match={match.id}&ruleset_version={draft.id}"
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    def test_unknown_match_returns_empty_page(self, api_client: object) -> None:
        import uuid as uuid_mod

        response = api_client.get(f"/api/v1/fantasy/points?match={uuid_mod.uuid4()}")
        assert response.status_code == 200
        body = response.json()
        assert body["data"] == []
        assert body["meta"]["total_count"] == 0
        assert body["meta"]["calc_run_id"] is None
        assert body["meta"]["ruleset"] is None

    def test_page_number_pagination(self, api_client: object) -> None:
        match, _team1, _p1, _r1, _v1 = _seed(runs=58)
        _p2, _ = add_stat(match, _team1, runs=12, balls=8, out=True)
        recompute.recompute_match(match)
        assert FantasyCalcAudit.objects.filter(match_id=match.id).count() >= 1

        page1 = api_client.get(f"/api/v1/fantasy/points?match={match.id}&page_size=1")
        assert page1.status_code == 200
        meta = page1.json()["meta"]
        assert meta["page"] == 1
        assert meta["page_size"] == 1
        assert meta["total_count"] == 2
        assert meta["total_pages"] == 2
        assert meta["has_more"] is True
        assert len(page1.json()["data"]) == 1

        page2 = api_client.get(f"/api/v1/fantasy/points?match={match.id}&page_size=1&page=2")
        assert page2.status_code == 200
        assert page2.json()["meta"]["page"] == 2
        assert len(page2.json()["data"]) == 1

    def test_invalid_page_404(self, api_client: object) -> None:
        _seed()
        response = api_client.get("/api/v1/fantasy/points?match=x&page=abc")
        assert response.status_code in (400, 404)  # DRF paginator reports 404
        assert response.json()["error"]["code"] in ("invalid_query", "not_found")

    def test_etag_and_304(self, api_client: object) -> None:
        match, _team1, _player, _ruleset, _version = _seed()
        url = f"/api/v1/fantasy/points?match={match.id}"
        first = api_client.get(url)
        assert first.status_code == 200
        etag = first["ETag"]
        assert etag
        second = api_client.get(url, HTTP_IF_NONE_MATCH=etag)
        assert second.status_code == 304

    def test_fantasy_computed_invalidates_read_cache(self, api_client: object) -> None:
        match, _team1, player, _ruleset, _version = _seed()
        url = f"/api/v1/fantasy/points?match={match.id}"

        assert api_client.get(url).json()["data"][0]["total_points"] == "58.00"
        stat = PlayerMatchStat.objects.get(match_id=match.id, player_id=player.id)
        stat.batting = [{**stat.batting[0], "runs": 70, "balls": 50}]
        stat.save(update_fields=["batting"])
        recompute.recompute_match(match, trigger="correction.applied")
        # The recompute publishes ``fantasy.computed`` on commit; dispatch the
        # invalidation synchronously (the Phase E tests do the same) and verify
        # the read cache is actually invalidated.
        from apps.core import events as core_events
        from apps.core.cache import invalidation

        invalidation.invalidate_event(
            core_events.TOPIC_FANTASY_COMPUTED, {"match_id": str(match.id)}
        )

        assert api_client.get(url).json()["data"][0]["total_points"] == "70.00"


# --------------------------------------------------------------------------- #
# /fantasy/matches/{id}
# --------------------------------------------------------------------------- #


class TestMatchPoints:
    def test_golden_envelope_and_meta(self, api_client: object) -> None:
        match, _team1, player, ruleset, version = _seed()

        response = api_client.get(f"/api/v1/fantasy/matches/{match.id}")

        assert response.status_code == 200
        assert "max-age=120" in response["Cache-Control"]
        body = response.json()
        data = body["data"]
        assert data["match_id"] == str(match.id)
        assert data["format"] == "T20I"
        assert data["status"] == "COMPLETED"
        assert data["version"] == {
            "id": str(version.id),
            "version": 1,
            "format_scope": "T20I",
            "content_hash": version.content_hash,
            "is_default": True,
        }
        row = data["players"][0]
        assert row["player_id"] == str(player.id)
        assert row["player_name"] == player.full_name
        assert row["total_points"] == "58.00"
        assert row["breakdown"] == {"runs": "58.00"}

        audit = FantasyCalcAudit.objects.get(match_id=match.id, ruleset_version_id=version.id)
        meta = body["meta"]
        assert meta["label"] == FANTASY_LABEL
        assert meta["notice"] == FANTASY_NOTICE
        assert meta["calc_run_id"] == str(audit.id)
        assert meta["status"] == "final"
        assert meta["is_partial"] is False
        assert meta["unsupported_rules"] == []
        assert meta["ruleset"] == {
            "id": str(ruleset.id),
            "version": 1,
            "hash": version.content_hash,
        }
        datetime.fromisoformat(meta["as_of"])

    def test_players_ordered_by_total_desc(self, api_client: object) -> None:
        match, team1, _p1, _ruleset, _version = _seed()
        low, _ = add_stat(match, team1, runs=10, balls=8, out=True)
        recompute.recompute_match(match)

        response = api_client.get(f"/api/v1/fantasy/matches/{match.id}")
        rows = response.json()["data"]["players"]
        assert rows[0]["total_points"] == "58.00"
        assert rows[1]["player_id"] == str(low.id)

    def test_unknown_match_404(self, api_client: object) -> None:
        import uuid as uuid_mod

        response = api_client.get(f"/api/v1/fantasy/matches/{uuid_mod.uuid4()}")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


class TestCoverageBranches:
    """API branches the golden tests don't reach (Task 10 gate)."""

    def test_malformed_player_param_is_400_not_301(self, api_client: object) -> None:
        """A non-uuid ``?player=`` falls through the merged-redirect check and
        the normal validation answers 400 invalid_query."""
        response = api_client.get("/api/v1/fantasy/points?player=not-a-uuid")
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_query"

    def test_version_detail_unknown_ruleset_404(self, api_client: object) -> None:
        import uuid as uuid_mod

        response = api_client.get(f"/api/v1/fantasy/rulesets/{uuid_mod.uuid4()}/versions/1")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    def test_version_detail_unknown_version_404(self, api_client: object) -> None:
        _match, _team1, _player, ruleset, _version = _seed()
        response = api_client.get(f"/api/v1/fantasy/rulesets/{ruleset.id}/versions/99")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    def test_malformed_ruleset_version_param_404(self, api_client: object) -> None:
        """A non-uuid ``?ruleset_version=`` cannot be a public version -> 404."""
        response = api_client.get("/api/v1/fantasy/points?ruleset_version=not-a-uuid")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    def test_match_presented_under_latest_calc_run_version(self, api_client: object) -> None:
        """With no frozen default, the primary version falls back to the latest
        non-provisional calc run's version (a draft, computed on demand)."""
        match, team1, _ = make_completed_match()
        add_stat(match, team1, runs=58, balls=42, out=True)
        # A draft (auto-recompute) version with NO frozen default.
        _, version = make_ruleset_version(auto_recompute=True)
        recompute.recompute_match(match)

        response = api_client.get(f"/api/v1/fantasy/matches/{match.id}")

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["version"]["id"] == str(version.id)
        assert body["data"]["players"][0]["total_points"] == "58.00"

    def test_match_without_public_version_returns_empty_payload(self, api_client: object) -> None:
        """A completed match with no frozen default and no calc runs has no
        primary version: the endpoint answers an explicit empty payload."""
        match, team1, _ = make_completed_match()
        add_stat(match, team1, runs=58, balls=42, out=True)
        # No ruleset version at all -> no frozen default, no calc run.

        response = api_client.get(f"/api/v1/fantasy/matches/{match.id}")

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["version"] is None
        assert body["data"]["players"] == []
        assert body["meta"]["status"] is None
        assert body["meta"]["calc_run_id"] is None

    def test_points_row_without_calc_run_has_null_identity(self, api_client: object) -> None:
        """A read-model row not produced by a calc run (calc_run NULL) renders
        status None instead of guessing final/provisional."""
        from datetime import timedelta

        from django.utils import timezone

        from apps.fantasy.models import PlayerMatchPerformance

        match, _team1, _player, _ruleset, version = _seed()
        extra, _ = add_stat(match, _team1, runs=10, balls=6, out=True)
        PlayerMatchPerformance.objects.create(
            match_id=match.id,
            player_id=extra.id,
            ruleset_version_id=version.id,
            total_points=10,
            breakdown={"runs": "10.00"},
            computed_at=timezone.now() + timedelta(days=1),  # latest row
        )

        response = api_client.get(f"/api/v1/fantasy/points?match={match.id}")

        assert response.status_code == 200
        rows = {row["player_id"]: row for row in response.json()["data"]}
        assert rows[str(extra.id)]["status"] is None
        assert rows[str(extra.id)]["calc_run_id"] is None
        # The meta summarises the latest row -> the calc-run-less one.
        assert response.json()["meta"]["status"] is None
        assert response.json()["meta"]["calc_run_id"] is None


# --------------------------------------------------------------------------- #
# Cross-cutting
# --------------------------------------------------------------------------- #


class TestCrossCutting:
    def test_merged_player_points_answers_301(self, api_client: object) -> None:
        match, _team1, _player, _ruleset, _version = _seed()
        canonical = make_player("Canonical")
        merged = make_player("Merged Away")
        PlayerMerge.objects.create(canonical_player=canonical, merged_player=merged)

        response = api_client.get(f"/api/v1/fantasy/points?match={match.id}&player={merged.id}")

        assert response.status_code == 301
        location = response["Location"]
        assert f"player={canonical.id}" in location
        assert f"match={match.id}" in location  # other params preserved

    def test_flag_off_answers_404(self, api_client: object) -> None:
        match, _team1, _player, _ruleset, _version = _seed()

        with override_settings(FANTASY_PUBLIC_API_ENABLED=False):
            for path in (
                "/api/v1/fantasy/rulesets",
                f"/api/v1/fantasy/points?match={match.id}",
                f"/api/v1/fantasy/matches/{match.id}",
            ):
                response = api_client.get(path)
                assert response.status_code == 404, path
                assert response.json()["error"]["code"] == "not_found"

    def test_guards_query_counts_and_no_delivery(self, api_client: object) -> None:
        match, _team1, _player, _ruleset, _version = _seed()
        cases = (
            "/api/v1/fantasy/rulesets",
            f"/api/v1/fantasy/points?match={match.id}",
            f"/api/v1/fantasy/matches/{match.id}",
        )
        for path in cases:
            with CaptureQueriesContext(connection) as queries:
                response = api_client.get(path)
            assert response.status_code == 200, path
            assert len(queries) <= 8, f"{path}: {len(queries)} queries"
            sql = " ".join(str(q) for q in queries)
            assert "delivery" not in sql  # fantasy reads never touch ball data
