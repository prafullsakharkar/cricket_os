"""Add ``reference`` to ``fantasy_point_adjustment`` (plan Task 5).

Every adjustment requires an external reference (ticket/order/correction id);
a void entry's reference is the id of the adjustment it offsets. Safe as a
plain NOT NULL add because the table is empty (the feature is new in Phase H).

Reversible: drops the column.
"""

from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("fantasy", "0004_fantasycalcaudit_version_hash_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="fantasypointadjustment",
            name="reference",
            field=models.TextField(),
        ),
    ]
