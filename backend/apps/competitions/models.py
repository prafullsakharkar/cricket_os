"""Competition, season, series, match stage and the format catalogue.

Columns and constraint/index names match CRICKET_DATABASE_SCHEMA §3.4 plus the
``format_catalogue`` seed (§3.2). ``FormatCatalogue`` lives here (decision D3:
moved from the ``matches`` module in ARCHITECTURE §2, updated there in the same
change). ``source_provider_id`` is a plain UUID column (D2).
"""

from __future__ import annotations

from django.db import models

from apps.core.enums import CompetitionType, FormatType
from apps.core.models import UUIDPrimaryKeyModel

_SERIES_TYPES = ("bilateral", "triangular", "tournament", "league", "icc_event")
_STAGE_TYPES = (
    "group",
    "super_league",
    "quarter_final",
    "semi_final",
    "final",
    "playoff",
    "league",
)


class Competition(UUIDPrimaryKeyModel):
    name = models.TextField()
    short_name = models.TextField(null=True, blank=True)
    # Enum-like columns; the DDL types are `text` (manifest reports enums as text).
    competition_type = models.TextField(choices=CompetitionType.choices)
    # db_index=False: the DDL declares no index on governing_body_id.
    governing_body = models.ForeignKey(
        "venues.GoverningBody",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="competitions",
        db_index=False,
    )
    format_type = models.TextField(choices=FormatType.choices, null=True, blank=True)
    is_international = models.BooleanField(default=False)
    gender = models.CharField(max_length=1, default="M")
    provider_competition_id = models.TextField(null=True, blank=True)
    source_provider_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "competition"
        constraints = [
            models.UniqueConstraint(fields=["name"], name="ux_competition_name"),
            models.UniqueConstraint(
                fields=["source_provider_id", "provider_competition_id"],
                condition=models.Q(provider_competition_id__isnull=False),
                name="ux_competition_provider",
            ),
            models.CheckConstraint(
                condition=models.Q(competition_type__in=CompetitionType.values),
                name="ck_competition_competition_type_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(format_type__in=FormatType.values),
                name="ck_competition_format_type_valid",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class Season(UUIDPrimaryKeyModel):
    # db_index=False: ux_season already covers (competition_id, label).
    competition = models.ForeignKey(
        "competitions.Competition",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="seasons",
        db_index=False,
    )
    label = models.TextField()
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "season"
        constraints = [
            models.UniqueConstraint(fields=["competition", "label"], name="ux_season"),
        ]

    def __str__(self) -> str:
        return f"{self.competition} {self.label}"


class Series(UUIDPrimaryKeyModel):
    name = models.TextField()
    short_name = models.TextField(null=True, blank=True)
    # db_index=False: the DDL declares no index on these FKs.
    competition = models.ForeignKey(
        "competitions.Competition",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="series",
        db_index=False,
    )
    season = models.ForeignKey(
        "competitions.Season",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="series",
        db_index=False,
    )
    # Enum-like column; the DDL type is `text`.
    series_type = models.TextField(default="bilateral")
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    host_country = models.ForeignKey(
        "venues.Country",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="hosted_series",
        db_index=False,
    )
    is_tournament = models.BooleanField(default=False)
    points_system = models.JSONField(null=True, blank=True)
    provider_series_id = models.TextField(null=True, blank=True)
    source_provider_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "series"
        constraints = [
            models.UniqueConstraint(
                fields=["source_provider_id", "provider_series_id"],
                condition=models.Q(provider_series_id__isnull=False),
                name="ux_series_provider",
            ),
            models.CheckConstraint(
                condition=models.Q(series_type__in=_SERIES_TYPES),
                name="ck_series_series_type_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["start_date", "end_date"], name="ix_series_dates"),
        ]

    def __str__(self) -> str:
        return self.name


class MatchStage(UUIDPrimaryKeyModel):
    # db_index=False: ux_stage already covers (series_id, name).
    series = models.ForeignKey(
        "competitions.Series", on_delete=models.PROTECT, related_name="stages", db_index=False
    )
    name = models.TextField()
    stage_type = models.TextField()
    group_name = models.TextField(null=True, blank=True)
    sequence = models.IntegerField(null=True, blank=True)

    class Meta:
        db_table = "match_stage"
        constraints = [
            models.UniqueConstraint(fields=["series", "name"], name="ux_stage"),
            models.CheckConstraint(
                condition=models.Q(stage_type__in=_STAGE_TYPES),
                name="ck_match_stage_stage_type_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.series}: {self.name}"


class FormatCatalogue(models.Model):
    """Per-format rules the scoring service reads (DATABASE_SCHEMA §3.2).

    ``overs_limit`` is in overs; the innings ball limit is
    ``overs_limit * balls_per_over`` (The Hundred: 20 x 5). ``format`` is the
    primary key (matches the reference DDL's ``format`` primary key); it is a
    ``text`` column (the DDL type), not varchar.
    """

    format = models.TextField(primary_key=True, choices=FormatType.choices)
    max_innings = models.SmallIntegerField()
    overs_limit = models.IntegerField(null=True, blank=True)
    balls_per_over = models.SmallIntegerField(default=6)
    allows_declaration = models.BooleanField(default=False)
    allows_follow_on = models.BooleanField(default=False)
    allows_draw = models.BooleanField(default=False)
    allows_super_over = models.BooleanField(default=True)

    class Meta:
        db_table = "format_catalogue"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(format__in=FormatType.values),
                name="ck_format_catalogue_format_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(max_innings__in=(2, 4)),
                name="ck_format_catalogue_max_innings_valid",
            ),
        ]

    def __str__(self) -> str:
        return self.format


__all__ = ["Competition", "FormatCatalogue", "MatchStage", "Season", "Series"]
