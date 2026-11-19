"""Phase C model/DDL surface: tables exist, constraints hold, delivery is a
partitioned table with no DEFAULT partition and match_date in every unique key.
"""

from __future__ import annotations

import datetime
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, transaction

from apps.core.enums import InningsEventType
from apps.innings.models import Innings, InningsEvent, InningsSummary
from apps.matches.models import Match, MatchPlayer, MatchResult, MatchTeam, Toss
from apps.teams.models import Team

from .factories import make_match, make_match_teams, make_player, make_team

pytestmark = pytest.mark.django_db

#: Every Phase C table (DATABASE_SCHEMA §3.5–3.8), excluding `over` (created by
#: Django) is included; partition children are NOT (they are not in the doc).
PHASE_C_TABLES = [
    "match",
    "match_team",
    "toss",
    "match_player",
    "match_official",
    "match_result",
    "match_award",
    "innings",
    "innings_summary",
    "innings_event",
    "over",
    "delivery",
    "batting_entry",
    "bowling_entry",
    "fall_of_wicket",
    "partnership",
    "player_match_stat",
    "import_job",
    "ingestion_error",
    "sync_checkpoint",
    "data_correction",
]


def expect_integrity_error(call: object) -> None:
    """Run ``call`` inside a savepoint so the failed statement does not poison
    the surrounding test transaction."""
    with pytest.raises(IntegrityError), transaction.atomic():
        call()  # type: ignore[operator]


def _table_exists(table: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM pg_tables WHERE schemaname = 'cricket' AND tablename = %s",
            [table],
        )
        return cursor.fetchone() is not None


class TestTablesExist:
    def test_all_phase_c_tables_present(self) -> None:
        missing = [t for t in PHASE_C_TABLES if not _table_exists(t)]
        assert missing == [], f"missing tables: {missing}"


class TestDeliveryPartitioning:
    def test_delivery_is_range_partitioned_by_match_date(self) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT partstrat, pg_get_partkeydef(c.oid) "
                "FROM pg_partitioned_table p JOIN pg_class c ON c.oid = p.partrelid "
                "WHERE c.relname = 'delivery' AND c.relnamespace = 'cricket'::regnamespace"
            )
            rows = cursor.fetchall()
        assert len(rows) == 1
        strat, keydef = rows[0]
        assert strat == "r"
        assert keydef == "RANGE (match_date)"

    def test_no_default_partition(self) -> None:
        """The Phase C decision: no DEFAULT partition, no pre-created years."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_get_expr(c.relpartbound, c.oid) "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'cricket' AND c.relispartition "
                "AND c.relname LIKE 'delivery%'"
            )
            bounds = [row[0] for row in cursor.fetchall()]
        assert all(b and "DEFAULT" not in b for b in bounds)

    def test_every_unique_index_includes_match_date(self) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT i.indexrelid::regclass::text, pg_get_indexdef(i.indexrelid) "
                "FROM pg_index i "
                "WHERE i.indrelid = 'cricket.delivery'::regclass AND i.indisunique"
            )
            uniques = cursor.fetchall()
        assert len(uniques) >= 3
        for _, defn in uniques:
            assert "match_date" in defn

    def test_model_uses_composite_primary_key(self) -> None:
        from apps.deliveries.models import Delivery

        pk = Delivery._meta.pk
        assert pk.__class__.__name__ == "CompositePrimaryKey"
        assert sorted(f.name for f in pk.fields) == ["id", "match_date"]

    def test_generated_columns_exist(self) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT attname FROM pg_attribute a "
                "JOIN pg_class c ON c.oid = a.attrelid "
                "WHERE c.relname = 'delivery' AND c.relnamespace = 'cricket'::regnamespace "
                "AND attgenerated <> '' AND attnum > 0 AND NOT attisdropped"
            )
            generated = {row[0] for row in cursor.fetchall()}
        assert {"total_runs", "is_wide", "is_noball", "is_legal"} <= generated


class TestMatchConstraints:
    def test_partial_unique_provider_key(self) -> None:
        provider_uuid = uuid4()
        make_match(provider_match_id="m1", source_provider_id=provider_uuid)
        expect_integrity_error(
            lambda: make_match(provider_match_id="m1", source_provider_id=provider_uuid)
        )

    def test_null_provider_match_id_allowed_many_times(self) -> None:
        make_match()
        make_match()
        assert Match.objects.count() == 2

    def test_match_team_unique_and_number(self) -> None:
        match = make_match()
        team1 = make_team("India")
        team2 = make_team("Australia")
        MatchTeam.objects.create(match=match, team=team1, team_number=1)
        # Same team twice -> ux_match_team; same number twice -> ux_match_team_num.
        expect_integrity_error(
            lambda: MatchTeam.objects.create(match=match, team=team1, team_number=2)
        )
        expect_integrity_error(
            lambda: MatchTeam.objects.create(match=match, team=team2, team_number=1)
        )
        # The valid second team/number pair works.
        MatchTeam.objects.create(match=match, team=team2, team_number=2)

    def test_toss_one_to_one(self) -> None:
        match = make_match()
        team = make_team()
        Toss.objects.create(match=match, winner_team=team, decision="bat")
        expect_integrity_error(
            lambda: Toss.objects.create(match=match, winner_team=team, decision="bowl")
        )

    def test_toss_decision_check(self) -> None:
        match = make_match()
        team = make_team()
        expect_integrity_error(
            lambda: Toss.objects.create(match=match, winner_team=team, decision="field")
        )

    def test_match_player_unique(self) -> None:
        match = make_match()
        team = make_team()
        player = make_player()
        MatchPlayer.objects.create(match=match, player=player, team=team)
        expect_integrity_error(
            lambda: MatchPlayer.objects.create(match=match, player=player, team=team)
        )

    def test_match_result_one_to_one(self) -> None:
        match = make_match()
        MatchResult.objects.create(match=match, result_type="WIN")
        expect_integrity_error(lambda: MatchResult.objects.create(match=match, result_type="TIE"))


class TestInningsConstraints:
    def _match_and_teams(self) -> tuple[Match, Team, Team]:
        match = make_match()
        team1 = make_team("India")
        team2 = make_team("Australia")
        make_match_teams(match, team1, team2)
        return match, team1, team2

    def test_innings_unique_includes_super_over_number(self) -> None:
        match, team1, team2 = self._match_and_teams()
        Innings.objects.create(
            match=match,
            innings_number=3,
            batting_team=team1,
            bowling_team=team2,
            is_super_over=True,
            super_over_number=1,
        )
        # Same (match, number, is_super_over) but different super_over_number is OK.
        Innings.objects.create(
            match=match,
            innings_number=3,
            batting_team=team2,
            bowling_team=team1,
            is_super_over=True,
            super_over_number=2,
        )
        # Identical identity (including super_over_number NULL vs 0) is rejected.
        Innings.objects.create(
            match=match,
            innings_number=1,
            batting_team=team1,
            bowling_team=team2,
        )
        expect_integrity_error(
            lambda: Innings.objects.create(
                match=match,
                innings_number=1,
                batting_team=team2,
                bowling_team=team1,
                super_over_number=None,
            )
        )

    def test_innings_number_range(self) -> None:
        match, team1, team2 = self._match_and_teams()
        expect_integrity_error(
            lambda: Innings.objects.create(
                match=match,
                innings_number=5,
                batting_team=team1,
                bowling_team=team2,
            )
        )

    def test_innings_event_one_current_row_per_identity(self) -> None:
        match, team1, team2 = self._match_and_teams()
        innings = Innings.objects.create(
            match=match, innings_number=1, batting_team=team1, bowling_team=team2
        )
        player = make_player("Retiring Batter")
        InningsEvent.objects.create(
            innings=innings,
            event_type=InningsEventType.RETIREMENT_HURT,
            player=player,
            anchor_delivery_seq=12,
            is_current=True,
            revision=1,
        )
        # Same identity, still current -> rejected by ux_innings_event_current.
        expect_integrity_error(
            lambda: InningsEvent.objects.create(
                innings=innings,
                event_type=InningsEventType.RETIREMENT_HURT,
                player=player,
                anchor_delivery_seq=12,
                is_current=True,
                revision=2,
            )
        )
        # Same identity after superseding (is_current=False) is fine.
        InningsEvent.objects.filter(innings=innings).update(is_current=False)
        InningsEvent.objects.create(
            innings=innings,
            event_type=InningsEventType.RETIREMENT_HURT,
            player=player,
            anchor_delivery_seq=12,
            is_current=True,
            revision=2,
        )
        assert InningsEvent.objects.count() == 2

    def test_innings_event_requires_player_unless_other(self) -> None:
        match, team1, team2 = self._match_and_teams()
        innings = Innings.objects.create(
            match=match, innings_number=1, batting_team=team1, bowling_team=team2
        )
        # RESUME without a player is rejected by ck_innings_event_player.
        expect_integrity_error(
            lambda: InningsEvent.objects.create(innings=innings, event_type=InningsEventType.RESUME)
        )
        # OTHER without a player is allowed.
        InningsEvent.objects.create(innings=innings, event_type=InningsEventType.OTHER)

    def test_innings_summary_one_to_one(self) -> None:
        match, team1, team2 = self._match_and_teams()
        innings = Innings.objects.create(
            match=match, innings_number=1, batting_team=team1, bowling_team=team2
        )
        InningsSummary.objects.create(innings=innings, runs=150)
        expect_integrity_error(lambda: InningsSummary.objects.create(innings=innings, runs=99))


class TestMatchDateSemantics:
    def test_delivery_match_date_is_match_start_date(self) -> None:
        """match_date is the match START date (service-enforced; see write path).

        This test pins the denormalisation contract used by partition pruning.
        """
        from apps.deliveries.models import Delivery

        make_match(start_date=datetime.date(2026, 10, 25))
        assert Delivery._meta.get_field("match_date")
        # The write-path service enforces match_date == match.start_time_utc date
        # (covered in tests/phase_c/test_delivery_write_path.py); here we assert
        # the model column is non-nullable so a service can never forget it.
        field = Delivery._meta.get_field("match_date")
        assert field.null is False
