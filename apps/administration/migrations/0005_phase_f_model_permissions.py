"""Grant the ``admin`` role permissions for the Phase F models.

Phase F adds the §3.7 aggregate models (``statistics``) and the ``rankings``
app; their ContentTypes and CRUD permissions are created by their app
migrations. The admin group is defined as "every permission in the system"
(0002_seed_groups), so this migration (running after both leaf migrations)
extends it; the other roles stay scoped exactly as before.

Reversible: removes the Phase F permissions from the admin group.
"""

from __future__ import annotations

from django.db import migrations

#: Phase F models (app_label, model) that need admin permissions.
PHASE_F_MODELS = [
    ("statistics", "playercareerstat"),
    ("statistics", "teamstat"),
    ("statistics", "headtohead"),
    ("statistics", "leaderboard"),
    ("statistics", "formtrend"),
    ("statistics", "tournamentstandings"),
    ("statistics", "statisticswatermark"),
    ("rankings", "playerranking"),
]

CODETYPES = ("add", "change", "delete", "view")


def grant_admin(apps, schema_editor):  # noqa: ARG001
    Group = apps.get_model("auth", "Group")
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")

    admin = Group.objects.get_or_create(name="admin")[0]
    perms: list[Permission] = []
    for app_label, model in PHASE_F_MODELS:
        ct, _ = ContentType.objects.get_or_create(app_label=app_label, model=model)
        for codename in CODETYPES:
            perm, _ = Permission.objects.get_or_create(
                content_type=ct,
                codename=f"{codename}_{model}",
            )
            perms.append(perm)
    admin.permissions.add(*perms)


def revoke_admin(apps, schema_editor):  # noqa: ARG001
    Group = apps.get_model("auth", "Group")
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")

    admin = Group.objects.filter(name="admin").first()
    if admin is None:
        return
    for app_label, model in PHASE_F_MODELS:
        ct = ContentType.objects.filter(app_label=app_label, model=model).first()
        if ct is None:
            continue
        codenames = [f"{c}_{model}" for c in CODETYPES]
        perms = Permission.objects.filter(content_type=ct, codename__in=codenames)
        admin.permissions.remove(*perms)


class Migration(migrations.Migration):
    dependencies = [
        ("administration", "0004_phase_d_model_permissions"),
        ("statistics", "0002_phase_f_aggregates"),
        ("rankings", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(grant_admin, revoke_admin),
    ]
