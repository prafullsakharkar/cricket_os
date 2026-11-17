"""Phase I Task 3: the identity-linking batch (`link_all`) and its command.

* ``link_all`` over the recorded live-provider corpus classifies every entry
  (auto-linked / already-linked / candidate-pending / stub-created), is
  idempotent on re-run, and reports the steward workload estimate;
* ``--dry-run`` computes the same classification without writing any map row,
  player or duplicate-candidate row;
* the management command end-to-end writes the dated coverage report
  (`reports/phase_i/identity_coverage.md`) with residual duplicates == 0.
"""

from __future__ import annotations

import datetime

import pytest
from django.core.management import call_command

from apps.core.enums import TeamType
from apps.players.models import Player, PlayerDuplicateCandidate
from apps.providers.linking import (
    OUTCOME_ALREADY_LINKED,
    OUTCOME_AUTO_LINKED,
    OUTCOME_CANDIDATE_PENDING,
    OUTCOME_STUB_CREATED,
    LinkSummary,
    link_all,
    link_entity,
)
from apps.providers.models import DataProvider, ProviderEntityMap
from apps.teams.models import Team, TeamMembership
from apps.venues.models import Country

pytestmark = pytest.mark.django_db


def _corpus() -> list[dict[str, object]]:
    from pathlib import Path

    import yaml

    path = Path(__file__).resolve().parents[1] / "fixtures" / "provider_live" / "players.yaml"
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)["corpus"]


def _seed_players() -> dict[str, object]:
    """The three cricsheet-imported players the corpus links onto."""
    from tests.phase_c.factories import make_player

    country, _ = Country.objects.get_or_create(
        iso_code="IN", defaults={"name": "India", "iso3": "IND"}
    )
    team = Team.objects.create(
        name="India", team_type=TeamType.INTERNATIONAL, country_id=country.id
    )
    rows = [
        ("Virat Kohli", "V Kohli", datetime.date(1988, 11, 5)),
        ("Rohit Sharma", "RG Sharma", datetime.date(1987, 4, 30)),
        ("Jasprit Bumrah", "JB", datetime.date(1993, 12, 6)),
    ]
    players = {}
    for full_name, unique_name, dob in rows:
        player = make_player(
            full_name=full_name,
            unique_name=unique_name,
            date_of_birth=dob,
            gender="M",
            birth_country=country,
        )
        TeamMembership.objects.create(team=team, player=player)
        players[full_name] = player
    return {"team": team, "players": players}


def _entries(corpus: list[dict[str, object]], team: Team) -> list[tuple[str, str, dict]]:
    """Mirror the command's corpus normalisation (dates + team ids)."""
    from apps.providers.management.commands.link_provider_identities import _coerce_dates

    entries = []
    for entry in corpus:
        attributes = dict(entry["attributes"])  # type: ignore[arg-type]
        attributes["team_ids"] = [str(team.id)]
        attributes = _coerce_dates(attributes)
        entries.append((entry["entity_type"], entry["provider_entity_id"], attributes))  # type: ignore[arg-type]
    return entries


def _replay() -> DataProvider:
    replay, _ = DataProvider.objects.get_or_create(code="replay", defaults={"name": "Replay"})
    return replay


def test_link_all_classifies_full_corpus_as_auto_linked() -> None:
    seeded = _seed_players()
    replay = _replay()
    entries = _entries(_corpus(), seeded["team"])  # type: ignore[arg-type]

    summary = link_all(replay, entries)

    assert summary.provider_code == "replay"
    assert summary.total_linked == 3
    assert summary.totals[OUTCOME_AUTO_LINKED] == 3
    assert summary.totals.get(OUTCOME_CANDIDATE_PENDING, 0) == 0
    assert summary.totals.get(OUTCOME_STUB_CREATED, 0) == 0
    assert summary.steward_workload_minutes() == 0

    # One map row per corpus entry, all pointing at the existing players.
    for entry in _corpus():
        row = ProviderEntityMap.objects.get(
            provider=replay,
            entity_type=entry["entity_type"],
            provider_entity_id=entry["provider_entity_id"],
        )
        assert row.verified is False
    assert Player.objects.count() == 3
    assert PlayerDuplicateCandidate.objects.count() == 0


def test_link_all_is_idempotent_on_rerun() -> None:
    seeded = _seed_players()
    replay = _replay()
    entries = _entries(_corpus(), seeded["team"])  # type: ignore[arg-type]

    first = link_all(replay, entries)
    second = link_all(replay, entries)

    assert first.totals[OUTCOME_AUTO_LINKED] == 3
    assert second.totals[OUTCOME_ALREADY_LINKED] == 3
    assert second.totals.get(OUTCOME_AUTO_LINKED, 0) == 0
    assert (
        ProviderEntityMap.objects.filter(
            provider=replay, entity_type="player", provider_entity_id__startswith="live-"
        ).count()
        == 3
    )
    assert Player.objects.count() == 3


def test_link_all_dry_run_writes_nothing() -> None:
    seeded = _seed_players()
    replay = _replay()
    entries = _entries(_corpus(), seeded["team"])  # type: ignore[arg-type]

    summary = link_all(replay, entries, dry_run=True)

    # Classification is computed without writing.
    assert summary.totals[OUTCOME_AUTO_LINKED] == 3
    assert summary.dry_run is True
    assert ProviderEntityMap.objects.filter(provider=replay).count() == 0
    assert Player.objects.count() == 3
    assert PlayerDuplicateCandidate.objects.count() == 0


def test_link_all_counts_pending_candidates_and_workload() -> None:
    seeded = _seed_players()
    replay = _replay()
    existing = seeded["players"]["Virat Kohli"]  # type: ignore[index]

    # A near-duplicate (name + team, no DoB) lands in the review band.
    pending_attrs: dict[str, object] = {
        "full_name": "Virat Kohli",
        "birth_country": "India",
        "gender": "M",
        "team_ids": [str(seeded["team"].id)],  # type: ignore[union-attr]
    }
    summary = LinkSummary(provider_code="replay", dry_run=False)
    outcome = link_entity(replay, "player", "live-pending", pending_attrs)
    assert outcome.outcome == OUTCOME_CANDIDATE_PENDING

    summary.totals[OUTCOME_CANDIDATE_PENDING] = 1
    summary.candidate_ids.append(str(outcome.internal_id))

    assert summary.steward_workload_minutes() == 2  # 1 × 2 min/review (ASSUMED)
    assert summary.steward_workload_minutes(5) == 5

    stub = Player.objects.get(id=outcome.internal_id)
    flagged = PlayerDuplicateCandidate.objects.filter(candidate_player=stub, status="open")
    assert flagged.count() >= 1
    assert flagged.first().canonical_player_id == existing.id


def test_command_end_to_end_links_corpus_and_writes_report(tmp_path, monkeypatch) -> None:
    from apps.providers.management.commands.link_provider_identities import (
        DEFAULT_CORPUS,
    )

    _seed_players()
    _replay()
    monkeypatch.setattr(
        "apps.providers.management.commands.link_provider_identities.REPORT_PATH",
        tmp_path / "identity_coverage.md",
    )

    call_command("link_provider_identities", "--provider", "replay", corpus=str(DEFAULT_CORPUS))

    report = (tmp_path / "identity_coverage.md").read_text(encoding="utf-8")
    assert "auto_linked | 3" in report
    assert "Residual duplicates in corpus: **0**" in report
    assert "UNVERIFIED" in report
    assert (
        ProviderEntityMap.objects.filter(provider=DataProvider.objects.get(code="replay")).count()
        == 3
    )


def test_command_dry_run_writes_nothing_and_still_reports(tmp_path, monkeypatch) -> None:
    from apps.providers.management.commands.link_provider_identities import (
        DEFAULT_CORPUS,
    )

    _seed_players()
    replay = _replay()
    monkeypatch.setattr(
        "apps.providers.management.commands.link_provider_identities.REPORT_PATH",
        tmp_path / "identity_coverage.md",
    )

    call_command(
        "link_provider_identities",
        "--provider",
        "replay",
        "--dry-run",
        corpus=str(DEFAULT_CORPUS),
    )

    assert ProviderEntityMap.objects.filter(provider=replay).count() == 0
    assert Player.objects.count() == 3
    report = (tmp_path / "identity_coverage.md").read_text(encoding="utf-8")
    assert "auto_linked | 3" in report
    assert "DRY-RUN" in report


def test_command_errors_when_provider_missing() -> None:
    from django.core.management.base import CommandError

    with pytest.raises(CommandError, match="no DataProvider"):
        call_command("link_provider_identities", "--provider", "nope")
