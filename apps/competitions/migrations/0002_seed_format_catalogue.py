"""Seed the format_catalogue with the nine rows from DATABASE_SCHEMA §3.2.

Reversible and idempotent (update-or-create on the primary key). The rows are
the authority the scoring service reads; apps/scoring/config.py mirrors them
and a test guards the two never drift apart.
"""

from __future__ import annotations

from django.db import migrations

# (format, max_innings, overs_limit, balls_per_over, allows_declaration,
#  allows_follow_on, allows_draw, allows_super_over)
FORMAT_ROWS = [
    ("TEST", 4, None, 6, True, True, True, False),
    ("FIRST_CLASS", 4, None, 6, True, True, True, False),
    ("ODI", 2, 50, 6, False, False, False, True),
    ("LIST_A", 2, 50, 6, False, False, False, True),
    ("T20I", 2, 20, 6, False, False, False, True),
    ("T20", 2, 20, 6, False, False, False, True),
    ("T10", 2, 10, 6, False, False, False, True),
    ("THE_HUNDRED", 2, 20, 5, False, False, False, True),
    ("OTHER", 2, None, 6, False, False, False, True),
]

FIELDS = (
    "max_innings",
    "overs_limit",
    "balls_per_over",
    "allows_declaration",
    "allows_follow_on",
    "allows_draw",
    "allows_super_over",
)


def seed(apps, schema_editor) -> None:
    FormatCatalogue = apps.get_model("competitions", "FormatCatalogue")
    for fmt, *values in FORMAT_ROWS:
        FormatCatalogue.objects.update_or_create(
            format=fmt,
            defaults=dict(zip(FIELDS, values, strict=True)),
        )


def unseed(apps, schema_editor) -> None:
    # Raw SQL (see venues.0002 unseed for why the ORM collector is avoided).
    formats = ",".join(f"'{row[0]}'" for row in FORMAT_ROWS)
    schema_editor.execute(
        f"DELETE FROM cricket.format_catalogue WHERE format IN ({formats})"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("competitions", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
