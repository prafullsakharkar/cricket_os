"""Grant permissions for the Phase I (Task 4) match de-dup models.

``administration.0002_seed_groups`` defines the four roles; each phase since
adds a migration that extends them for its new models. Phase I adds the
``match_duplicate_candidate`` and ``match_merge`` tables (``matches.0004``):

* ``admin`` — every CRUD permission for both models (additive; the group
  keeps what it already has);
* ``data_steward`` — ``view`` + ``change`` (the duplicate-candidate review and
  reversible merge/split actions are gated by the models' ``change``
  permission, mirroring the Phase B duplicate-candidate admin);
* ``operator`` — ``view`` (the queue is browseable by operators).

Reversible: removes exactly the permissions this migration added.
"""

from __future__ import annotations

from django.db import migrations

#: (app_label, model) pairs that need Phase I permissions.
PHASE_I_MODELS = [
    ("matches", "matchduplicatecandidate"),
    ("matches", "matchmerge"),
]

CODETYPES = ("add", "change", "delete", "view")


def _permissions(apps, model_keys, codenames):
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    perms: list = []
    for app_label, model in model_keys:
        ct, _ = ContentType.objects.get_or_create(app_label=app_label, model=model)
        for codename in codenames:
            perm, _ = Permission.objects.get_or_create(
                content_type=ct,
                codename=f"{codename}_{model}",
            )
            perms.append(perm)
    return perms


def grant(apps, schema_editor):  # noqa: ARG001
    Group = apps.get_model("auth", "Group")

    admin = Group.objects.get_or_create(name="admin")[0]
    admin.permissions.add(*_permissions(apps, PHASE_I_MODELS, CODETYPES))

    steward = Group.objects.get_or_create(name="data_steward")[0]
    steward.permissions.add(*_permissions(apps, PHASE_I_MODELS, ("view", "change")))

    operator = Group.objects.get_or_create(name="operator")[0]
    operator.permissions.add(*_permissions(apps, PHASE_I_MODELS, ("view",)))


def revoke(apps, schema_editor):  # noqa: ARG001
    Group = apps.get_model("auth", "Group")

    admin = Group.objects.filter(name="admin").first()
    if admin is not None:
        admin.permissions.remove(*_permissions(apps, PHASE_I_MODELS, CODETYPES))

    steward = Group.objects.filter(name="data_steward").first()
    if steward is not None:
        steward.permissions.remove(*_permissions(apps, PHASE_I_MODELS, ("view", "change")))

    operator = Group.objects.filter(name="operator").first()
    if operator is not None:
        operator.permissions.remove(*_permissions(apps, PHASE_I_MODELS, ("view",)))


class Migration(migrations.Migration):
    dependencies = [
        ("administration", "0006_phase_h_model_permissions"),
        ("matches", "0004_match_dedup"),
    ]

    operations = [
        migrations.RunPython(grant, revoke),
    ]
