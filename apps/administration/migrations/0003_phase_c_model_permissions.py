"""Grant the ``admin`` role every permission for the Phase C models.

``admin`` is defined as "every permission in the system" (0002_seed_groups).
Phase C adds seven new apps; their ContentTypes and default CRUD permissions
are created by the ``post_migrate`` signals when those apps migrate, but the
admin group's permission set was snapshotted at Phase B time. This migration
runs after the Phase C leaf migrations (declared in ``dependencies``) and adds
the new models' permissions to the admin group. The viewer / operator /
data_steward roles stay scoped to reference data exactly as before; the DLQ
triage permissions for the operational roles are granted in the DLQ admin
(Phase C group 8) via ``ingestion``/``synchronization`` admin configuration.

Reversible: removes the granted Phase C permissions from the admin group
(the Permission rows themselves are left in place).
"""

from __future__ import annotations

from django.db import migrations

#: Phase C models (app_label, model) that need admin permissions. Django
#: creates ContentTypes/permissions for unmanaged models too (they are
#: registered in the app registry), so `delivery` is included like the rest.
PHASE_C_MODELS = [
    ("matches", "match"),
    ("matches", "matchteam"),
    ("matches", "toss"),
    ("matches", "matchplayer"),
    ("matches", "matchofficial"),
    ("matches", "matchresult"),
    ("matches", "matchaward"),
    ("innings", "innings"),
    ("innings", "inningsevent"),
    ("innings", "inningssummary"),
    ("deliveries", "over"),
    ("deliveries", "delivery"),
    ("scorecards", "battingentry"),
    ("scorecards", "bowlingentry"),
    ("scorecards", "fallofwicket"),
    ("scorecards", "partnership"),
    ("statistics", "playermatchstat"),
    ("ingestion", "importjob"),
    ("ingestion", "ingestionerror"),
    ("synchronization", "synccheckpoint"),
    ("synchronization", "datacorrection"),
]

CODETYPES = ("add", "change", "delete", "view")


def grant_admin(apps, schema_editor):  # noqa: ARG001
    Group = apps.get_model("auth", "Group")
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")

    admin = Group.objects.get_or_create(name="admin")[0]
    phase_c_perms: list[Permission] = []
    for app_label, model in PHASE_C_MODELS:
        ct, _ = ContentType.objects.get_or_create(app_label=app_label, model=model)
        for codename in CODETYPES:
            perm, _ = Permission.objects.get_or_create(
                content_type=ct,
                codename=f"{codename}_{model}",
            )
            phase_c_perms.append(perm)
    admin.permissions.add(*phase_c_perms)


def revoke_admin(apps, schema_editor):  # noqa: ARG001
    Group = apps.get_model("auth", "Group")
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")

    admin = Group.objects.filter(name="admin").first()
    if admin is None:
        return
    for app_label, model in PHASE_C_MODELS:
        ct = ContentType.objects.filter(app_label=app_label, model=model).first()
        if ct is None:
            continue
        codenames = [f"{c}_{model}" for c in CODETYPES]
        perms = Permission.objects.filter(content_type=ct, codename__in=codenames)
        admin.permissions.remove(*perms)


class Migration(migrations.Migration):
    dependencies = [
        ("administration", "0002_seed_groups"),
        ("matches", "0001_initial"),
        ("innings", "0002_initial"),
        ("deliveries", "0003_delivery_partitioned_table"),
        ("scorecards", "0001_initial"),
        ("statistics", "0001_initial"),
        ("ingestion", "0001_initial"),
        ("synchronization", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(grant_admin, revoke_admin),
    ]
