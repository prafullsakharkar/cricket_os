"""Canonical enumerations (DATABASE_SCHEMA §3.1).

These ``TextChoices`` mirror the PostgreSQL ``ENUM`` types created in the
initial migration. Values match the SQL labels **exactly and in order**;
``tests/test_enums.py`` guards against drift by parsing the schema document.
"""

from __future__ import annotations

from django.db import models


class TeamType(models.TextChoices):
    INTERNATIONAL = "international", "International"
    FRANCHISE = "franchise", "Franchise"
    DOMESTIC = "domestic", "Domestic"
    WOMEN_INTERNATIONAL = "women_international", "Women's international"
    WOMEN_FRANCHISE = "women_franchise", "Women's franchise"
    OTHER = "other", "Other"


class FormatType(models.TextChoices):
    TEST = "TEST", "Test"
    ODI = "ODI", "ODI"
    T20I = "T20I", "T20I"
    T20 = "T20", "T20"
    FIRST_CLASS = "FIRST_CLASS", "First-class"
    LIST_A = "LIST_A", "List A"
    THE_HUNDRED = "THE_HUNDRED", "The Hundred"
    T10 = "T10", "T10"
    OTHER = "OTHER", "Other"


class MatchStatus(models.TextChoices):
    """Match **lifecycle** only; the outcome lives in :class:`ResultType`."""

    SCHEDULED = "SCHEDULED", "Scheduled"
    TOSS = "TOSS", "Toss"
    IN_PROGRESS = "IN_PROGRESS", "In progress"
    INNINGS_BREAK = "INNINGS_BREAK", "Innings break"
    DRINKS = "DRINKS", "Drinks"
    RAIN_DELAY = "RAIN_DELAY", "Rain delay"
    BAD_LIGHT = "BAD_LIGHT", "Bad light"
    STUMPS = "STUMPS", "Stumps"
    DAY_BREAK = "DAY_BREAK", "Day break"
    COMPLETED = "COMPLETED", "Completed"
    ABANDONED = "ABANDONED", "Abandoned"
    CANCELLED = "CANCELLED", "Cancelled"
    FORFEITED = "FORFEITED", "Forfeited"
    POSTPONED = "POSTPONED", "Postponed"


class InningsStatus(models.TextChoices):
    NOT_STARTED = "NOT_STARTED", "Not started"
    IN_PROGRESS = "IN_PROGRESS", "In progress"
    DECLARED = "DECLARED", "Declared"
    ALL_OUT = "ALL_OUT", "All out"
    COMPLETED = "COMPLETED", "Completed"
    FORFEITED = "FORFEITED", "Forfeited"


class ResultType(models.TextChoices):
    WIN = "WIN", "Win"
    TIE = "TIE", "Tie"
    DRAW = "DRAW", "Draw"
    NO_RESULT = "NO_RESULT", "No result"
    ABANDONED = "ABANDONED", "Abandoned"
    CANCELLED = "CANCELLED", "Cancelled"
    FORFEIT = "FORFEIT", "Forfeit"
    SUPER_OVER_TIE_BREAK = "SUPER_OVER_TIE_BREAK", "Super over tie-break"


class DismissalType(models.TextChoices):
    BOWLED = "BOWLED", "Bowled"
    CAUGHT = "CAUGHT", "Caught"
    CAUGHT_AND_BOWLED = "CAUGHT_AND_BOWLED", "Caught and bowled"
    LBW = "LBW", "Leg before wicket"
    RUN_OUT = "RUN_OUT", "Run out"
    STUMPED = "STUMPED", "Stumped"
    HIT_WICKET = "HIT_WICKET", "Hit wicket"
    OBSTRUCTING_THE_FIELD = "OBSTRUCTING_THE_FIELD", "Obstructing the field"
    TIMED_OUT = "TIMED_OUT", "Timed out"
    HANDLED_THE_BALL = "HANDLED_THE_BALL", "Handled the ball"
    HIT_THE_BALL_TWICE = "HIT_THE_BALL_TWICE", "Hit the ball twice"
    RETIRED_HURT = "RETIRED_HURT", "Retired hurt"
    RETIRED_OUT = "RETIRED_OUT", "Retired out"
    RETIRED_NOT_OUT = "RETIRED_NOT_OUT", "Retired not out"
    MANKAD = "MANKAD", "Mankad"


class RetirementType(models.TextChoices):
    HURT = "HURT", "Hurt"
    NOT_OUT = "NOT_OUT", "Not out"


class InningsEventType(models.TextChoices):
    RETIREMENT_HURT = "RETIREMENT_HURT", "Retirement (hurt)"
    RETIREMENT_NOT_OUT = "RETIREMENT_NOT_OUT", "Retirement (not out)"
    RESUME = "RESUME", "Resume"
    OTHER = "OTHER", "Other"


class BatStyle(models.TextChoices):
    RIGHT_HAND = "RIGHT_HAND", "Right hand"
    LEFT_HAND = "LEFT_HAND", "Left hand"


class BowlStyle(models.TextChoices):
    RIGHT_ARM_FAST = "RIGHT_ARM_FAST", "Right-arm fast"
    RIGHT_ARM_MEDIUM = "RIGHT_ARM_MEDIUM", "Right-arm medium"
    RIGHT_ARM_OFFBREAK = "RIGHT_ARM_OFFBREAK", "Right-arm offbreak"
    RIGHT_ARM_LEGBREAK = "RIGHT_ARM_LEGBREAK", "Right-arm legbreak"
    LEFT_ARM_FAST = "LEFT_ARM_FAST", "Left-arm fast"
    LEFT_ARM_MEDIUM = "LEFT_ARM_MEDIUM", "Left-arm medium"
    LEFT_ARM_ORTHODOX = "LEFT_ARM_ORTHODOX", "Left-arm orthodox"
    LEFT_ARM_CHINAMAN = "LEFT_ARM_CHINAMAN", "Left-arm chinaman"
    NONE = "NONE", "None"


class PlayerRole(models.TextChoices):
    BATTER = "BATTER", "Batter"
    BOWLER = "BOWLER", "Bowler"
    ALL_ROUNDER = "ALL_ROUNDER", "All-rounder"
    WICKETKEEPER_BATTER = "WICKETKEEPER_BATTER", "Wicketkeeper-batter"
    BATTING_ALL_ROUNDER = "BATTING_ALL_ROUNDER", "Batting all-rounder"
    BOWLING_ALL_ROUNDER = "BOWLING_ALL_ROUNDER", "Bowling all-rounder"
    ALL_ROUNDER_WK = "ALL_ROUNDER_WK", "All-rounder wicketkeeper"


class StatScope(models.TextChoices):
    CAREER = "CAREER", "Career"
    FORMAT = "FORMAT", "Format"
    SEASON = "SEASON", "Season"
    SERIES = "SERIES", "Series"
    TEAM = "TEAM", "Team"
    VENUE = "VENUE", "Venue"
    OPPOSITION = "OPPOSITION", "Opposition"
    TOURNAMENT = "TOURNAMENT", "Tournament"


class RankingCategory(models.TextChoices):
    BATTING = "BATTING", "Batting"
    BOWLING = "BOWLING", "Bowling"
    ALL_ROUNDER = "ALL_ROUNDER", "All-rounder"
    TEAM = "TEAM", "Team"


class CompetitionType(models.TextChoices):
    INTERNATIONAL = "INTERNATIONAL", "International"
    LEAGUE = "LEAGUE", "League"
    DOMESTIC = "DOMESTIC", "Domestic"
    MULTI_NATION = "MULTI_NATION", "Multi-nation"
    WOMEN = "WOMEN", "Women"
