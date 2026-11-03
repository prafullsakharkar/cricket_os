"""Grant the ``admin`` role permissions for the Phase D models.

Phase D adds two new models (``match_event_log``, ``reconciliation_job``) whose
ContentTypes and CRUD permissions are created by their app migrations. The
admin group is defined as "every permission in the system" (0002_seed_groups),
so this migration (running after both leaf migrations) extends it; the other
roles stay scoped exactly as before.

Reversible: removes the Phase D permissions from the admin group.
"""

from __future__ import annotations

from django.db import migrations

#: Phase D models (app_label, model) that need admin permissions.
PHASE_D_MODELS = [
    ("matches", "matcheventlog"),
    ("synchronization", "reconciliationjob"),
]

CODETYPES = ("add", "change", "delete", "view")


def grant_admin(apps, schema_editor):  # noqa: ARG001
    Group = apps.get_model("auth", "Group")
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")

    admin = Group.objects.get_or_create(name="admin")[0]
    perms: list[Permission] = []
    for app_label, model in PHASE_D_MODELS:
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
    for app_label, model in PHASE_D_MODELS:
        ct = ContentType.objects.filter(app_label=app_label, model=model).first()
        if ct is None:
            continue
        codenames = [f"{c}_{model}" for c in CODETYPES]
        perms = Permission.objects.filter(content_type=ct, codename__in=codenames)
        admin.permissions.remove(*perms)


class Migration(migrations.Migration):
    dependencies = [
        ("administration", "0003_phase_c_model_permissions"),
        ("matches", "0003_phase_d_match_event_log"),
        ("synchronization", "0002_phase_d_reconciliation_job"),
    ]

    operations = [
        migrations.RunPython(grant_admin, revoke_admin),
    ]
