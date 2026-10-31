"""Every Phase B migration reverses cleanly (forward/backward, incl. seeds).

Postgres DDL is transactional, so the forward -> backward -> forward cycles
below run inside the test transaction and are rolled back at test end — they
cannot disturb the session's migrated test database or other tests.
"""

from __future__ import annotations

import pytest
from django.apps import apps as django_apps
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.django_db

PHASE_B_APPS = [
    "venues",
    "teams",
    "players",
    "competitions",
    "providers",
    "administration",
]

#: Leaf migration node per Phase B app (the state `manage.py migrate` produces).
LEAVES = [
    ("venues", "0002_seed_countries_governing_bodies"),
    ("teams", "0001_initial"),
    ("players", "0002_initial"),
    ("competitions", "0002_seed_format_catalogue"),
    ("providers", "0002_seed_cricsheet_provider"),
    ("administration", "0001_initial"),
]

#: The 19 reference-data tables (ci/schema_drift_scope.txt).
SCOPED_TABLES = [
    "country",
    "governing_body",
    "venue",
    "ground",
    "format_catalogue",
    "team",
    "player",
    "player_merge",
    "player_duplicate_candidate",
    "team_membership",
    "squad",
    "squad_member",
    "competition",
    "season",
    "series",
    "match_stage",
    "data_provider",
    "provider_entity_map",
    "audit_log",
]


def _executor() -> MigrationExecutor:
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    return executor


def _migrate(targets: list[tuple[str, str | None]]) -> None:
    """Apply/unapply with a fresh executor so the loader re-reads the recorder.

    ``MigrationExecutor`` snapshots ``applied_migrations`` when the loader is
    built; without a fresh executor a second migrate call would compute a plan
    against the stale snapshot.
    """
    _executor().migrate(targets)


def _table_exists(cursor, table: str) -> bool:
    cursor.execute(
        "SELECT 1 FROM pg_tables WHERE schemaname = 'cricket' AND tablename = %s",
        [table],
    )
    return cursor.fetchone() is not None


def test_full_cycle_unapply_reapply_is_symmetric() -> None:
    """Unapplying all six apps drops the 19 tables; re-applying restores them."""
    executor = _executor()
    assert all(node in executor.loader.applied_migrations for node in LEAVES)

    # 1) Unapply everything for the Phase B apps (reverse plan, dependency
    #    order handled by the executor).
    _migrate([(app, None) for app in PHASE_B_APPS])

    with connection.cursor() as cursor:
        for table in SCOPED_TABLES:
            assert not _table_exists(cursor, table), f"table {table} still exists"

    # 2) Re-apply the full forward plan.
    _migrate(LEAVES)
    with connection.cursor() as cursor:
        for table in SCOPED_TABLES:
            assert _table_exists(cursor, table), f"table {table} missing"

    # 3) The generated column and seeds survive the round-trip.
    assert django_apps.get_model("venues", "Country").objects.count() == 11
    assert django_apps.get_model("venues", "GoverningBody").objects.count() == 13
    assert django_apps.get_model("competitions", "FormatCatalogue").objects.count() == 9
    assert (
        django_apps.get_model("providers", "DataProvider").objects.filter(code="cricsheet").count()
        == 1
    )


@pytest.mark.parametrize(
    ("app", "seed_migration", "model_label", "seed_count"),
    [
        ("venues", "0002_seed_countries_governing_bodies", "venues.Country", 11),
        ("venues", "0002_seed_countries_governing_bodies", "venues.GoverningBody", 13),
        ("competitions", "0002_seed_format_catalogue", "competitions.FormatCatalogue", 9),
        ("providers", "0002_seed_cricsheet_provider", "providers.DataProvider", 1),
    ],
)
def test_seed_migration_backward_removes_rows_and_forward_restores_them(
    app: str, seed_migration: str, model_label: str, seed_count: int
) -> None:
    app_label, model_name = model_label.split(".")
    model = django_apps.get_model(app_label, model_name)

    # Leaf state: seeded.
    assert model.objects.count() == seed_count

    # Backward to 0001_initial: the seed rows are removed.
    _migrate([(app, "0001_initial")])
    assert model.objects.count() == 0

    # Forward to the seed migration again: the rows come back.
    _migrate([(app, seed_migration)])
    assert model.objects.count() == seed_count


def test_unapply_and_reapply_keeps_name_key_generated_column() -> None:
    """The persisted name_key generated column survives the round-trip."""
    import datetime

    _migrate([(app, None) for app in PHASE_B_APPS])
    _migrate(LEAVES)

    Player = django_apps.get_model("players", "Player")
    player = Player.objects.create(
        full_name="Virat Kohli", date_of_birth=datetime.date(1988, 11, 5)
    )
    days = (datetime.date(1988, 11, 5) - datetime.date(1, 1, 1)).days
    assert player.name_key == f"virat kohli|{days}"
