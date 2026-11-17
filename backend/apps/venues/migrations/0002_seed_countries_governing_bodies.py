"""Seed the ICC and the full-member national boards (reference data).

Reversible and idempotent: forward uses ``get_or_create`` on the unique
fields, reverse deletes exactly the rows this migration created.
"""

from __future__ import annotations

from django.db import migrations

COUNTRIES = [
    ("GB", "GBR", "England", "England"),
    ("AU", "AUS", "Australia", "Australia"),
    ("IN", "IND", "India", "India"),
    ("PK", "PAK", "Pakistan", "Pakistan"),
    ("NZ", "NZL", "New Zealand", "New Zealand"),
    ("ZA", "ZAF", "South Africa", "South Africa"),
    ("LK", "LKA", "Sri Lanka", "Sri Lanka"),
    ("BD", "BGD", "Bangladesh", "Bangladesh"),
    ("AF", "AFG", "Afghanistan", "Afghanistan"),
    ("ZW", "ZWE", "Zimbabwe", "Zimbabwe"),
    ("IE", "IRL", "Ireland", "Ireland"),
]

# (name, abbreviation, country_name, type); country_name None for multi-nation.
GOVERNING_BODIES = [
    ("International Cricket Council", "ICC", None, "international"),
    ("England and Wales Cricket Board", "ECB", "England", "national"),
    ("Cricket Australia", "CA", "Australia", "national"),
    ("Board of Control for Cricket in India", "BCCI", "India", "national"),
    ("Pakistan Cricket Board", "PCB", "Pakistan", "national"),
    ("New Zealand Cricket", "NZC", "New Zealand", "national"),
    ("Cricket South Africa", "CSA", "South Africa", "national"),
    ("Sri Lanka Cricket", "SLC", "Sri Lanka", "national"),
    ("Bangladesh Cricket Board", "BCB", "Bangladesh", "national"),
    ("Afghanistan Cricket Board", "ACB", "Afghanistan", "national"),
    ("Zimbabwe Cricket", "ZC", "Zimbabwe", "national"),
    # Multi-nation body (several Caribbean nations) — no single country.
    ("Cricket West Indies", "CWI", None, "national"),
    ("Cricket Ireland", "CI", "Ireland", "national"),
]


def seed(apps, schema_editor) -> None:
    Country = apps.get_model("venues", "Country")
    GoverningBody = apps.get_model("venues", "GoverningBody")
    for iso_code, iso3, name, short_name in COUNTRIES:
        Country.objects.get_or_create(
            name=name,
            defaults={"iso_code": iso_code, "iso3": iso3, "short_name": short_name},
        )
    by_name = {c.name: c for c in Country.objects.all()}
    for name, abbreviation, country_name, gb_type in GOVERNING_BODIES:
        GoverningBody.objects.get_or_create(
            name=name,
            defaults={
                "abbreviation": abbreviation,
                "country": by_name.get(country_name),
                "type": gb_type,
            },
        )


def unseed(apps, schema_editor) -> None:
    # Raw SQL on purpose: the ORM's deletion collector joins related models
    # (team/competition) whose FKs resolve to different historical model
    # classes under Django's `preserve=True` state mutation, raising
    # "Cannot query ... Must be ... instance" during a bulk reverse migrate.
    gb_names = ",".join(f"'{name}'" for name, *_ in GOVERNING_BODIES)
    schema_editor.execute(
        f"DELETE FROM cricket.governing_body WHERE name IN ({gb_names})"
    )
    country_names = ",".join(f"'{c[2]}'" for c in COUNTRIES)
    schema_editor.execute(
        f"DELETE FROM cricket.country WHERE name IN ({country_names})"
    )
    # Django creates FKs as DEFERRABLE INITIALLY DEFERRED; the raw deletes leave
    # deferred RI checks pending, which blocks a later DROP TABLE of these
    # tables ("pending trigger events"). Force the checks now (transaction-scoped).
    schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")


class Migration(migrations.Migration):
    dependencies = [
        ("venues", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
