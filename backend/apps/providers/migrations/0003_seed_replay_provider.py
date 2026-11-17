"""Seed the Replay data provider row (Phase I Task 3).

Reversible and idempotent. The ReplayAdapter (`apps/providers/adapters/replay.py`)
is the internal fake provider used while the provider gate is unmet
(`docs/PROVIDER_DECISION.md`). Giving it its own ``DataProvider`` namespace
lets Phase I exercise the identity-resolution machinery (``resolve``,
``provider_entity_map``) across two real provider namespaces — cricsheet and
replay — on recorded data, which the continuity and linking-batch tests depend
on.

``licensing_notes`` records the status plainly: the replay corpus is the
internally-recorded test corpus, not a licensed feed.
"""

from __future__ import annotations

from django.db import migrations

LICENSING_NOTES = (
    "Internal ReplayAdapter corpus (Phase I pre-launch stand-in). Not a "
    "licensed feed; no external data is redistributed. Provider gate "
    "(docs/PROVIDER_DECISION.md) is NOT met."
)


def seed(apps, schema_editor) -> None:
    DataProvider = apps.get_model("providers", "DataProvider")
    DataProvider.objects.update_or_create(
        code="replay",
        defaults={
            "name": "Replay (live stand-in)",
            "supports_ball_by_ball": True,
            "supports_push": False,
            "licensing_notes": LICENSING_NOTES,
        },
    )


def unseed(apps, schema_editor) -> None:
    # Mirror providers.0002's unseed: only remove the seed row while nothing
    # references it. provider_entity_map rows PROTECT the provider, so a
    # populated DB keeps the referenced row (correct PROTECT semantics); the
    # reverse of 0001 drops the map table on a full reverse plan either way.
    schema_editor.execute(
        "DELETE FROM cricket.data_provider AS d WHERE d.code = 'replay' "
        "AND NOT EXISTS (SELECT 1 FROM cricket.provider_entity_map AS pem "
        "WHERE pem.provider_id = d.id)"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("providers", "0002_seed_cricsheet_provider"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
