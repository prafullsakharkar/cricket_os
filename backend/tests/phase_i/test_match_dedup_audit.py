"""Phase I Task 4 (4a): match merge/split/dup-review topics write audit rows."""

from __future__ import annotations

import pytest
from tests.phase_c.factories import make_match

from apps.administration.models import AuditLog
from apps.core import events

pytestmark = pytest.mark.django_db


def test_match_merged_topic_writes_audit_row() -> None:
    canonical = make_match()
    merged = make_match()
    events.publish(
        events.TOPIC_MATCH_MERGED,
        {
            "canonical_match_id": str(canonical.id),
            "merged_match_id": str(merged.id),
            "actor": "steward-1",
        },
        on_commit=False,
    )
    rows = AuditLog.objects.filter(action=events.TOPIC_MATCH_MERGED)
    assert rows.count() == 1
    row = rows.get()
    assert row.entity_type == "match"
    assert row.entity_id == canonical.id
    assert row.after == {
        "canonical_match_id": str(canonical.id),
        "merged_match_id": str(merged.id),
    }
    assert row.actor_label == "steward-1"


def test_match_split_topic_writes_audit_row() -> None:
    canonical = make_match()
    merged = make_match()
    events.publish(
        events.TOPIC_MATCH_SPLIT,
        {
            "merge_id": "m1",
            "canonical_match_id": str(canonical.id),
            "merged_match_id": str(merged.id),
            "actor": "steward-1",
        },
        on_commit=False,
    )
    row = AuditLog.objects.get(action=events.TOPIC_MATCH_SPLIT)
    assert row.entity_type == "match"
    assert row.entity_id == merged.id
    assert row.after["merge_id"] == "m1"


def test_match_dup_reviewed_topic_writes_audit_row() -> None:
    from apps.matches.models import MatchDuplicateCandidate

    canonical = make_match()
    candidate = make_match()
    dup = MatchDuplicateCandidate.objects.create(
        candidate_match=candidate,
        canonical_match=canonical,
        score="0.800",
    )
    events.publish(
        events.TOPIC_MATCH_DUP_REVIEWED,
        {
            "duplicate_candidate_id": str(dup.id),
            "candidate_match_id": str(candidate.id),
            "canonical_match_id": str(canonical.id),
            "decision": "rejected",
            "actor": "steward-1",
        },
        on_commit=False,
    )
    row = AuditLog.objects.get(action=events.TOPIC_MATCH_DUP_REVIEWED)
    assert row.entity_type == "match_duplicate_candidate"
    assert row.entity_id == dup.id
    assert row.after["decision"] == "rejected"


def test_match_merged_topic_invalidates_cache_namespaces() -> None:
    from apps.core.cache import invalidation

    namespaces = invalidation.namespaces_for(
        events.TOPIC_MATCH_MERGED,
        {"canonical_match_id": "c1", "merged_match_id": "m1"},
    )
    assert {
        "match:c1",
        "match:m1",
        "matches:live",
        "matches:upcoming",
        "matches:recent",
    } <= namespaces
