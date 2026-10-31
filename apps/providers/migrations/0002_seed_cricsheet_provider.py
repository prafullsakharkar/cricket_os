"""Seed the Cricsheet data provider row.

Reversible and idempotent. ``licensing_notes`` records the verified licence
status (cricsheet.org/licensing, checked 2026-10-30): most data is ODC-By and
the XML match data is CC-BY 4.0; both require attribution.
"""

from __future__ import annotations

from django.db import migrations

LICENSING_NOTES = (
    "Cricsheet data is licensed under two licenses: most data under the Open "
    "Data Commons Attribution License (ODC-By); the XML match data alone under "
    "the Creative Commons Attribution 4.0 International (CC-BY 4.0). Both "
    "require attribution. Verified 2026-10-30 at https://cricsheet.org/licensing/."
)


def seed(apps, schema_editor) -> None:
    DataProvider = apps.get_model("providers", "DataProvider")
    DataProvider.objects.update_or_create(
        code="cricsheet",
        defaults={
            "name": "Cricsheet",
            "base_url": "https://cricsheet.org",
            "supports_ball_by_ball": True,
            "licensing_notes": LICENSING_NOTES,
        },
    )


def unseed(apps, schema_editor) -> None:
    # Raw SQL (see venues.0002 unseed for why the ORM collector is avoided).
    # Only remove the seed row while nothing references it: importer-created
    # provider_entity_map rows PROTECT the provider, and the reverse of 0001
    # (which drops the map table) runs *after* this migration on a full
    # reverse plan, so an unconditional delete fails on an importer-populated
    # DB. Leaving a referenced provider in place is the correct PROTECT
    # semantics; the table drop in 0001's reverse clears it either way.
    schema_editor.execute(
        "DELETE FROM cricket.data_provider AS d WHERE d.code = 'cricsheet' "
        "AND NOT EXISTS (SELECT 1 FROM cricket.provider_entity_map AS pem "
        "WHERE pem.provider_id = d.id)"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("providers", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
