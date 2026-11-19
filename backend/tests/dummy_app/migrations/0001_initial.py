"""Hand-written initial migration for the test-only dummy app."""

import apps.core.uuid7
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("players", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="DummyPlayerRef",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=apps.core.uuid7.uuid7,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("label", models.TextField(blank=True, default="")),
                (
                    "player",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="dummy_refs",
                        to="players.player",
                    ),
                ),
            ],
            options={
                "db_table": "dummy_player_ref",
            },
        ),
    ]
