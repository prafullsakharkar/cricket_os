"""Seed the four role Groups: viewer / operator / data_steward / admin.

Permission matrix (plan Group 5, Task 5.3):

    viewer        view_* on the 17 reference-data models only
    operator      view + change on the reference-data models (no delete/merge)
    data_steward  add/change/delete/view on the reference models
                  + players.merge_players + administration.view_auditlog
    admin         every permission in the system (incl. providers + audit)

This data migration runs *during* ``migrate`` — before the ``post_migrate``
signals that normally create ContentTypes and default Permissions — so it
creates the ContentTypes and Permissions it needs itself (``get_or_create``;
the later ``create_contenttypes`` / ``create_permissions`` receivers are
idempotent and will find the same rows). Reversible: the reverse deletes the
four Groups (their M2M rows go with them; the Permission rows stay).
"""

from django.db import migrations

#: The 17 reference-data models the viewer/operator/data_steward roles scope
#: over (Phase B apps). ``admin`` gets every permission in the system.
REFERENCE_MODELS = [
    ("venues", "country"),
    ("venues", "governingbody"),
    ("venues", "venue"),
    ("venues", "ground"),
    ("teams", "team"),
    ("teams", "teammembership"),
    ("teams", "squad"),
    ("teams", "squadmember"),
    ("players", "player"),
    ("players", "playermerge"),
    ("players", "playerduplicatecandidate"),
    ("competitions", "competition"),
    ("competitions", "season"),
    ("competitions", "series"),
    ("competitions", "matchstage"),
    ("competitions", "formatcatalogue"),
]

#: Every model in the system at migration time (reference data + Django
#: contrib models). ContentTypes and default Permissions are created for ALL
#: of them here — the migration runs *before* the ``post_migrate`` signals
#: that would normally create them, and ``admin`` must receive every
#: permission, including providers/administration and contrib (logentry,
#: user, session, contenttype).
ALL_MODELS = [
    *REFERENCE_MODELS,
    ("providers", "dataprovider"),
    ("providers", "providerentitymap"),
    ("administration", "auditlog"),
    ("auth", "permission"),
    ("auth", "group"),
    ("auth", "user"),
    ("contenttypes", "contenttype"),
    ("sessions", "session"),
    ("admin", "logentry"),
]

GROUPS = ("viewer", "operator", "data_steward", "admin")

PERMISSION_NAMES = {
    "add": "Can add {verbose}",
    "change": "Can change {verbose}",
    "delete": "Can delete {verbose}",
    "view": "Can view {verbose}",
}


def seed_groups(apps, schema_editor):  # noqa: ARG001
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")

    def content_type(app_label: str, model: str):
        return ContentType.objects.get_or_create(
            app_label=app_label, model=model
        )[0]

    def default_perms(
        app_label: str, model: str, codenames: tuple[str, ...]
    ) -> list[Permission]:
        ct = content_type(app_label, model)
        perms: list[Permission] = []
        for codename in codenames:
            perms.append(
                Permission.objects.get_or_create(
                    content_type=ct,
                    codename=f"{codename}_{model}",
                    defaults={
                        "name": PERMISSION_NAMES[codename].format(verbose=model)
                    },
                )[0]
            )
        return perms

    #: ContentTypes + default CRUD permissions for every model in the system.
    all_perms: dict[str, list[Permission]] = {}
    for app_label, model in ALL_MODELS:
        all_perms[f"{app_label}.{model}"] = default_perms(
            app_label, model, ("add", "change", "delete", "view")
        )

    player_ct = content_type("players", "player")
    merge_players_perm = Permission.objects.get_or_create(
        content_type=player_ct,
        codename="merge_players",
        defaults={"name": "Can merge duplicate players"},
    )[0]
    audit_ct = content_type("administration", "auditlog")
    view_auditlog_perm = Permission.objects.get_or_create(
        content_type=audit_ct,
        codename="view_auditlog",
        defaults={"name": "Can view audit log"},
    )[0]

    reference_perms = [
        perm
        for key, perms in all_perms.items()
        for perm in perms
        if key in {f"{a}.{m}" for a, m in REFERENCE_MODELS}
    ]

    def assign(name: str, perms: list[Permission]) -> None:
        group = Group.objects.get_or_create(name=name)[0]
        group.permissions.set(perms)

    assign(
        "viewer",
        [p for p in reference_perms if p.codename.startswith("view_")],
    )
    assign(
        "operator",
        [
            p
            for p in reference_perms
            if p.codename.startswith(("view_", "change_"))
        ],
    )
    assign(
        "data_steward",
        [*reference_perms, merge_players_perm, view_auditlog_perm],
    )
    # Every permission that exists at migration time. Test-only apps installed
    # later (tests.dummy_app) add permissions outside the role scope; they are
    # intentionally not in the admin group (see tests/admin/test_permissions.py).
    assign("admin", list(Permission.objects.all()))


def unseed_groups(apps, schema_editor):  # noqa: ARG001
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name__in=GROUPS).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("administration", "0001_initial"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("venues", "0002_seed_countries_governing_bodies"),
        ("teams", "0001_initial"),
        ("players", "0002_initial"),
        ("competitions", "0002_seed_format_catalogue"),
        ("providers", "0002_seed_cricsheet_provider"),
    ]

    operations = [
        migrations.RunPython(seed_groups, unseed_groups),
    ]
