"""Phase F Group 5: provider rankings import + read API.

Covers the brief's rankings requirements: a fixture-backed adapter that serves
**recorded** rows (in-repo corpus, never a live feed), the import pipeline that
upserts by the schema's ``ux_ranking`` key (idempotent on re-import), trend
derivation from the previous snapshot, best-rank/best-rating history, DLQ
reporting for unresolved provider players (dependency-inverted: ``rankings``
never imports ``ingestion``), the ``FEATURE_RANKINGS`` licence gate, and the
``/rankings`` + ``/players/{id}/rankings`` endpoints (API_SPEC §3.7).
"""

from __future__ import annotations

from datetime import date

import pytest

from apps.core.errors import LicenseRequired
from apps.players.models import Player
from apps.providers.models import ProviderEntityMap
from apps.rankings.adapters import FixtureRankingsAdapter
from apps.rankings.models import PlayerRanking
from apps.rankings.services.import_rankings import import_rankings

from ..phase_c.factories import make_player, make_provider, make_team

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _register_fixture_adapter() -> None:
    """Re-register the fixture adapter when a prior test cleared the registry.

    Phase D's ``clear_registry()`` test-isolation wipes every adapter; ``ready``
    runs only once, so tests that need an adapter register it explicitly.
    """
    from apps.providers.registry import list_providers, register_adapter

    if "fixture" not in list_providers():
        register_adapter("fixture", FixtureRankingsAdapter)


def _map_player(provider: object, player: Player, provider_id: str) -> None:
    ProviderEntityMap.objects.create(
        provider=provider,
        entity_type="player",
        internal_id=player.id,
        provider_entity_id=provider_id,
        provider_unique_name=player.full_name,
        verified=True,
    )


def _seed_corpus() -> tuple[object, list[Player]]:
    """Create the fixture provider + mapped players for the men.yaml corpus."""
    provider = make_provider("fixture")
    # The fixture names teams; create them so rows carry a team link.
    make_team("India")
    make_team("New Zealand")
    rohit = make_player("Rohit Sharma")
    kane = make_player("Kane Williamson")
    bumrah = make_player("Jasprit Bumrah")
    for player, pid in (
        (rohit, "rohit-sharma"),
        (kane, "kane-williamson"),
        (bumrah, "jasprit-bumrah"),
    ):
        _map_player(provider, player, pid)
    return provider, [rohit, kane, bumrah]


# --------------------------------------------------------------------------- #
# Adapter
# --------------------------------------------------------------------------- #


def test_fixture_adapter_serves_recorded_rows_per_gender() -> None:
    rows = list(FixtureRankingsAdapter().get_rankings("M"))
    assert len(rows) == 6
    first = rows[0]
    assert first["provider_player_id"] == "rohit-sharma"
    assert first["format_type"] == "TEST"
    assert first["category"] == "BATTING"
    assert first["as_of_date"] == date(2026, 11, 1)
    # Unknown genders have no recorded corpus.
    assert list(FixtureRankingsAdapter().get_rankings("F")) == []


# --------------------------------------------------------------------------- #
# Import pipeline
# --------------------------------------------------------------------------- #


def test_import_upserts_by_ux_ranking_key_and_is_idempotent() -> None:
    provider, _players = _seed_corpus()

    first = import_rankings(provider)
    assert first["imported"] == 5  # 5 resolved rows, 1 unresolved skipped
    assert first["skipped_unresolved"] == 1

    second = import_rankings(provider)
    assert second["imported"] == 0
    assert second["updated"] == 5

    assert PlayerRanking.objects.count() == 5
    # One row per (player, gender, format, category, as_of_date) — no dupes.
    keys = PlayerRanking.objects.values_list(
        "player_id", "gender", "format_type", "category", "as_of_date"
    )
    assert len(set(keys)) == 5


def test_trend_derived_from_previous_snapshot() -> None:
    provider, (rohit, kane, _bumrah) = _seed_corpus()
    import_rankings(provider)

    rohit_latest = PlayerRanking.objects.get(player_id=rohit.id, as_of_date=date(2026, 11, 8))
    kane_latest = PlayerRanking.objects.get(player_id=kane.id, as_of_date=date(2026, 11, 8))
    # Rohit 1 -> 2 (dropped one place); Kane 2 -> 1 (gained one place).
    assert rohit_latest.trend == "-1"
    assert kane_latest.trend == "+1"
    # First snapshot has no baseline.
    assert PlayerRanking.objects.get(player_id=kane.id, as_of_date=date(2026, 11, 1)).trend is None


def test_best_rank_and_rating_accumulate_from_history() -> None:
    provider, (rohit, kane, _bumrah) = _seed_corpus()
    import_rankings(provider)

    # Rohit: ranks 1 then 2 -> best 1; ratings 870 then 845 -> best 870.
    rohit_row = PlayerRanking.objects.get(player_id=rohit.id, as_of_date=date(2026, 11, 8))
    assert rohit_row.best_rank == 1
    assert rohit_row.best_rating == 870
    # Kane: ranks 2 then 1 -> best 1; ratings 855 then 900 -> best 900.
    kane_row = PlayerRanking.objects.get(player_id=kane.id, as_of_date=date(2026, 11, 8))
    assert kane_row.best_rank == 1
    assert kane_row.best_rating == 900


def test_unresolved_player_reports_to_dlq() -> None:
    from apps.ingestion.models import IngestionError
    from apps.ingestion.services.reporters import register_default_reporters

    register_default_reporters()
    provider, _players = _seed_corpus()

    import_rankings(provider)

    err = IngestionError.objects.filter(stage="rankings_import").get()
    assert err.error_class == "UnresolvedRankingPlayer"
    assert err.provider_id == provider.pk
    assert err.error_detail  # names the provider player id


def test_import_requires_rankings_licence() -> None:
    # ``cricsheet`` has no policy entry -> default policy, rankings not allowed.
    provider = make_provider("cricsheet")
    with pytest.raises(LicenseRequired) as exc:
        import_rankings(provider)
    assert exc.value.status_code == 403
    assert exc.value.code == "license_required"
    assert PlayerRanking.objects.count() == 0


def test_import_without_reporter_registered_is_silent_noop() -> None:
    # Without register_default_reporters, unresolved rows are skipped quietly.
    provider, _players = _seed_corpus()
    summary = import_rankings(provider)
    assert summary["skipped_unresolved"] == 1
    assert PlayerRanking.objects.count() == 5


# --------------------------------------------------------------------------- #
# Read API (API_SPEC §3.7)
# --------------------------------------------------------------------------- #


def test_rankings_endpoint_serves_latest_snapshot(api_client: object) -> None:
    provider, (rohit, kane, _bumrah) = _seed_corpus()
    import_rankings(provider)

    response = api_client.get("/api/v1/rankings?gender=M&format=TEST&category=BATTING&limit=10")
    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["source"] == "provider"
    assert body["meta"]["as_of_date"] == "2026-11-08"
    rows = body["data"]
    assert len(rows) == 2
    # Kane (rank 1) then Rohit (rank 2), ordered by rank.
    assert rows[0]["player"] == {"id": str(kane.id), "name": "Kane Williamson"}
    assert rows[0]["rank"] == 1
    assert rows[0]["rating"] == 900
    assert rows[0]["trend"] == "+1"
    assert rows[0]["best"] == {"rank": 1, "rating": 900}
    assert rows[1]["player"]["id"] == str(rohit.id)
    assert rows[1]["team"]["name"] == "India"


def test_player_rankings_endpoint_serves_history(api_client: object) -> None:
    provider, (rohit, _kane, _bumrah) = _seed_corpus()
    import_rankings(provider)

    response = api_client.get(f"/api/v1/players/{rohit.id}/rankings")
    assert response.status_code == 200
    body = response.json()
    history = body["data"]
    assert len(history) == 2  # two dated snapshots
    # Newest snapshot first.
    assert history[0]["as_of_date"] == "2026-11-08"
    assert history[0]["rank"] == 2
    assert history[1]["as_of_date"] == "2026-11-01"
    assert history[1]["rank"] == 1
