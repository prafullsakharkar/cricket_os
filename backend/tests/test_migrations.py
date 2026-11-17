"""Every Phase B migration reverses cleanly (forward/backward, incl. seeds).

Postgres DDL is transactional, so the forward -> backward -> forward cycles
below run inside the test transaction and are rolled back at test end — they
cannot disturb the session's migrated test database or other tests.
"""

from __future__ import annotations

from uuid import uuid4

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
    ("providers", "0003_seed_replay_provider"),
    ("administration", "0002_seed_groups"),
]

#: The 19 reference-data tables (scripts/ci/schema_drift_scope.txt).
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
    ("app", "seed_migration", "model_label", "leaf_count", "forward_count"),
    [
        ("venues", "0002_seed_countries_governing_bodies", "venues.Country", 11, 11),
        ("venues", "0002_seed_countries_governing_bodies", "venues.GoverningBody", 13, 13),
        ("competitions", "0002_seed_format_catalogue", "competitions.FormatCatalogue", 9, 9),
        # providers leaf is now 0003 (cricsheet + replay) -> leaf_count 2; the
        # 0002 forward step alone restores only cricsheet -> forward_count 1.
        ("providers", "0002_seed_cricsheet_provider", "providers.DataProvider", 2, 1),
        ("providers", "0003_seed_replay_provider", "providers.DataProvider", 2, 2),
        ("administration", "0002_seed_groups", "auth.Group", 4, 4),
    ],
)
def test_seed_migration_backward_removes_rows_and_forward_restores_them(
    app: str, seed_migration: str, model_label: str, leaf_count: int, forward_count: int
) -> None:
    app_label, model_name = model_label.split(".")
    model = django_apps.get_model(app_label, model_name)

    # Leaf state: seeded (the leaf may seed more rows than the migration under
    # test alone, e.g. providers.0003 seeds both cricsheet and replay).
    assert model.objects.count() == leaf_count

    # Backward to 0001_initial: the seed rows are removed.
    _migrate([(app, "0001_initial")])
    assert model.objects.count() == 0

    # Forward to the seed migration again: its rows come back.
    _migrate([(app, seed_migration)])
    assert model.objects.count() == forward_count


def test_phase_i_permission_grant_migration_round_trips() -> None:
    """administration.0007 grants then revokes the match de-dup permissions.

    Forward: admin gets every CRUD perm for the two new models, data_steward
    view+change, operator view. Backward to 0006: the grants are removed.
    """
    Group = django_apps.get_model("auth", "Group")
    ContentType = django_apps.get_model("contenttypes", "ContentType")
    Permission = django_apps.get_model("auth", "Permission")

    models = [("matches", "matchduplicatecandidate"), ("matches", "matchmerge")]

    def codenames_for(codenames: tuple[str, ...]) -> set[str]:
        found: set[str] = set()
        for app_label, model in models:
            ct = ContentType.objects.get(app_label=app_label, model=model)
            found.update(
                Permission.objects.filter(
                    content_type=ct, codename__in=[f"{c}_{model}" for c in codenames]
                ).values_list("codename", flat=True)
            )
        return found

    admin = Group.objects.get(name="admin")
    steward = Group.objects.get(name="data_steward")
    operator = Group.objects.get(name="operator")

    admin_all = codenames_for(("add", "change", "delete", "view"))
    assert admin_all <= set(admin.permissions.values_list("codename", flat=True))
    assert codenames_for(("view", "change")) <= set(
        steward.permissions.values_list("codename", flat=True)
    )
    assert codenames_for(("view",)) <= set(operator.permissions.values_list("codename", flat=True))

    _migrate([("administration", "0006_phase_h_model_permissions")])
    assert not (admin_all & set(admin.permissions.values_list("codename", flat=True)))
    assert not (
        codenames_for(("view", "change"))
        & set(steward.permissions.values_list("codename", flat=True))
    )

    _migrate([("administration", "0007_phase_i_model_permissions")])
    assert admin_all <= set(admin.permissions.values_list("codename", flat=True))


def test_full_cycle_on_importer_populated_db() -> None:
    """Full reverse/reapply still works when map rows reference the seed provider.

    The importer creates ``provider_entity_map`` rows pointing at the seeded
    ``cricsheet`` provider (PROTECT FK). providers.0002's reverse deletes the
    seed row *before* 0001's reverse drops the map table, so an unconditional
    delete would raise IntegrityError on a populated DB (this is what the
    "forward/backward on a DB populated by the importer fixture" requirement
    guards). The unseed must only remove a referenced provider when nothing
    references it.
    """

    DataProvider = django_apps.get_model("providers", "DataProvider")
    ProviderEntityMap = django_apps.get_model("providers", "ProviderEntityMap")
    provider = DataProvider.objects.get(code="cricsheet")
    ProviderEntityMap.objects.create(
        provider=provider,
        entity_type="player",
        internal_id=uuid4(),
        provider_entity_id="opt-populated-1",
    )

    _migrate([(app, None) for app in PHASE_B_APPS])
    _migrate(LEAVES)

    # Re-applied state is clean: the seed row is back and the map table is
    # re-created empty (the pre-reverse map row was dropped with its table).
    assert django_apps.get_model("providers", "DataProvider").objects.get(code="cricsheet")
    assert ProviderEntityMap.objects.filter(provider_entity_id="opt-populated-1").count() == 0


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
