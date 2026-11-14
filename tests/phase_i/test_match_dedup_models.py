"""Phase I Task 4 (4a): match de-dup schema — models, manager, migration shape.

* ``match.merged_into_id`` partial index + ``exclude_merged()``;
* ``resolve_canonical`` follows the redirect chain and refuses cycles;
* ``match_duplicate_candidate`` unique pair + status check;
* ``match_merge`` one-to-one merged row + status check;
* the partial index exists in the migrated schema (migration-drift shape).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from django.db import IntegrityError, connection
from tests.phase_c.factories import make_match

from apps.core.errors import Conflict
from apps.matches.models import Match, MatchDuplicateCandidate, MatchMerge

pytestmark = pytest.mark.django_db


def _matches() -> list[Match]:
    day = datetime(2026, 10, 25, 9, 0, tzinfo=UTC)
    return [
        make_match(start_time_utc=day, match_number=1),
        make_match(start_time_utc=day, match_number=2),
    ]


def test_merged_into_partial_index_exists() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT indexdef FROM pg_indexes "
            "WHERE schemaname='cricket' AND tablename='match' AND indexname='ix_match_merged_into'"
        )
        row = cursor.fetchone()
    assert row is not None
    assert "WHERE" in row[0] and "merged_into_id IS NOT NULL" in row[0]
    assert row[0].startswith("CREATE INDEX")


def test_exclude_merged_hides_merged_away_rows() -> None:
    canonical, merged = _matches()
    merged.merged_into = canonical
    merged.save(update_fields=["merged_into"])

    visible = list(Match.objects.exclude_merged())
    assert canonical in visible
    assert merged not in visible
    # Default manager still sees everything (merge service operates on all rows).
    assert Match.objects.count() == 2


def test_resolve_canonical_follows_chain_and_refuses_cycle() -> None:
    a, b, c = [*_matches(), make_match()]
    b.merged_into = a
    b.save(update_fields=["merged_into"])
    c.merged_into = b
    c.save(update_fields=["merged_into"])

    assert c.resolve_canonical().id == a.id

    # A corrupt cycle is refused, not looped.
    a.merged_into = c
    a.save(update_fields=["merged_into"])
    with pytest.raises(Conflict, match="cycle"):
        c.resolve_canonical()


def test_duplicate_candidate_unique_pair_and_status_check() -> None:
    from django.db import transaction

    canonical, candidate = _matches()
    MatchDuplicateCandidate.objects.create(
        candidate_match=candidate,
        canonical_match=canonical,
        score="0.800",
        breakdown={"teams": 1.0},
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        MatchDuplicateCandidate.objects.create(
            candidate_match=candidate,
            canonical_match=canonical,
            score="0.800",
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        MatchDuplicateCandidate.objects.create(
            candidate_match=canonical,
            canonical_match=candidate,
            score="0.800",
            status="bogus",
        )


def test_match_merge_one_to_one_and_status_check() -> None:
    from django.db import transaction

    canonical, merged = _matches()
    MatchMerge.objects.create(
        merged_match=merged,
        canonical_match=canonical,
        before={"match_team": []},
        after={"match_team": []},
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        MatchMerge.objects.create(
            merged_match=merged,
            canonical_match=canonical,
            before={},
            after={},
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        MatchMerge.objects.create(
            merged_match=make_match(),
            canonical_match=canonical,
            before={},
            after={},
            status="bogus",
        )
