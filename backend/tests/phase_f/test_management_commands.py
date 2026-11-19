"""Phase F Group 9: Tier-2 backfill + rankings import management commands.

Both commands are Phase F deliverables whose backfill numbers go into the final
report, so their behaviour (argument validation, dry-run, per-match builder
integration, idempotent import, ``--as-of`` replay) is tested directly through
``call_command``.
"""

from __future__ import annotations

from io import StringIO
from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.innings.models import Innings
from apps.rankings.models import PlayerRanking
from apps.statistics.models import StatisticsWatermark
from apps.statistics.services.recompute import _match_source_digest

from ..phase_c.factories import make_player
from .test_rankings import (
    _register_fixture_adapter,  # noqa: F401 (autouse)
    _seed_corpus,
)
from .test_recompute import make_completed_match, make_stat

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------- #
# rebuild_statistics
# --------------------------------------------------------------------------- #


def _run(*args: object) -> tuple[str, str]:
    stdout, stderr = StringIO(), StringIO()
    call_command("rebuild_statistics", *args, stdout=stdout, stderr=stderr)
    return stdout.getvalue(), stderr.getvalue()


def test_rebuild_requires_ids_for_scoped_rebuilds() -> None:
    with pytest.raises(CommandError, match="requires --ids"):
        call_command("rebuild_statistics", "player")
    with pytest.raises(CommandError, match="does not take --ids"):
        call_command("rebuild_statistics", "all", "--ids", "00000000-0000-0000-0000-000000000001")


def test_rebuild_rejects_malformed_since() -> None:
    with pytest.raises(CommandError, match="YYYY-MM-DD"):
        call_command("rebuild_statistics", "all", "--since", "01-01-2026")


def test_rebuild_dry_run_reports_without_writing() -> None:
    match, _t1, _t2 = make_completed_match()
    out, _err = _run("all", "--dry-run")
    assert "would recompute 1 matches" in out
    assert StatisticsWatermark.objects.filter(match_id=match.id).count() == 0


def test_rebuild_all_recomputes_and_watermarks() -> None:
    match, team1, _team2 = make_completed_match()
    p = make_player("Backfill Batter")
    make_stat(match, p, team1, runs=25, balls=15, out=True)

    out, _err = _run("all")
    assert "recomputed 1 matches" in out
    watermark = StatisticsWatermark.objects.get(match_id=match.id)
    assert watermark.status == "done"
    # The backfill digest must agree with the incremental one (Group 2 oracle).
    assert watermark.source_digest == _match_source_digest(match)


def test_rebuild_builds_missing_tier1_rows() -> None:
    match, team1, team2 = make_completed_match()
    Innings.objects.create(match=match, innings_number=1, batting_team=team1, bowling_team=team2)

    with (
        mock.patch(
            "apps.statistics.services.stats.build_match_stats", return_value=None
        ) as builder,
        mock.patch("apps.statistics.services.stats._is_partial", return_value=False) as partial,
    ):
        out, _err = _run("all")
    assert "built 1 missing stat rows" in out
    builder.assert_called_once()
    partial.assert_called_once()


def test_rebuild_warns_when_builder_fails() -> None:
    match, team1, team2 = make_completed_match()
    Innings.objects.create(match=match, innings_number=1, batting_team=team1, bowling_team=team2)

    with mock.patch(
        "apps.statistics.services.stats.build_match_stats",
        side_effect=RuntimeError("boom"),
    ):
        out, err = _run("all")
    assert "warning: build_match_stats failed" in err
    assert "built 0 missing stat rows" in out


def test_rebuild_scope_team_by_ids() -> None:
    match, team1, _team2 = make_completed_match()
    p = make_player("Scope Batter")
    make_stat(match, p, team1, runs=10)
    out, _err = _run("team", "--ids", str(team1.id))
    assert "recomputed 1 matches" in out


# --------------------------------------------------------------------------- #
# import_rankings
# --------------------------------------------------------------------------- #


def _import(*args: object) -> str:
    stdout = StringIO()
    call_command("import_rankings", *args, stdout=stdout)
    return stdout.getvalue()


def test_import_command_imports_the_corpus() -> None:
    provider, _players = _seed_corpus()
    out = _import("--provider", "fixture")
    assert "total=6 imported=5 updated=0 skipped_unresolved=1" in out
    assert PlayerRanking.objects.filter(source_provider_id=provider.id).count() == 5


def test_import_command_rejects_bad_gender_and_date() -> None:
    with pytest.raises(CommandError, match="M or F"):
        call_command("import_rankings", "--gender", "X")
    with pytest.raises(CommandError, match="YYYY-MM-DD"):
        call_command("import_rankings", "--as-of", "yesterday")


def test_import_command_unknown_provider() -> None:
    with pytest.raises(CommandError, match="no provider with code"):
        call_command("import_rankings", "--provider", "does-not-exist")


def test_import_command_as_of_replays_snapshot_only() -> None:
    provider, _players = _seed_corpus()
    out = _import("--provider", "fixture", "--as-of", "2026-11-01")
    assert "total=4 imported=3 updated=0 skipped_unresolved=1" in out
    # The 2026-11-08 snapshot rows are not imported.
    assert PlayerRanking.objects.filter(source_provider_id=provider.id).count() == 3
