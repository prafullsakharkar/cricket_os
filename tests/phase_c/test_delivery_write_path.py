"""Delivery write path: boundary validation, idempotent append/revise.

Covers ``deliveries.services.validation`` and ``deliveries.services.write``
against the real partitioned table:

* every DATA_INGESTION §6 boundary rule raises a typed error (never coerced);
* ``append_or_revise`` is order-independent, idempotent, partition-safe and
  writes a ``DataCorrection`` audit row when content is superseded;
* the bulk fast path (empty innings) and the careful slow path (corrections);
* ``match_date`` is always the match START date (service-enforced);
* events ``delivery.appended`` / ``delivery.corrected`` publish after commit,
  and are suppressed with ``suppress_events=True``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from uuid import uuid4

import pytest
from django.db import connection

from apps.core import events as core_events
from apps.core.enums import DismissalType, RetirementType
from apps.deliveries.models import Delivery
from apps.deliveries.services.validation import (
    DeliveryDraft,
    DeliveryValidationError,
    validate_drafts,
)
from apps.deliveries.services.write import append_or_revise, match_date_for
from apps.synchronization.models import DataCorrection

from .factories import (
    make_innings,
    make_match,
    make_match_teams,
    make_player,
    make_provider,
    make_team,
)

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #


def make_bowler() -> object:
    return make_player("Jasprit Bumrah")


def make_batter() -> object:
    return make_player("Virat Kohli")


def make_non_striker() -> object:
    return make_player("Rohit Sharma")


def make_wicket_setup() -> tuple[object, object, object, object]:
    """bowler, batter, non-striker, dismissed (the batter usually)."""
    return make_bowler(), make_batter(), make_non_striker(), make_batter()


def make_context() -> dict[str, object]:
    """A ready-to-use match + innings + player set for one batting side."""
    match = make_match()
    team1 = make_team("India")
    team2 = make_team("Australia")
    make_match_teams(match, team1, team2)
    innings = make_innings(match, team1, team2)
    bowler, batter, non_striker, dismissed = make_wicket_setup()
    return {
        "match": match,
        "team1": team1,
        "team2": team2,
        "innings": innings,
        "bowler": bowler,
        "batter": batter,
        "non_striker": non_striker,
        "dismissed": dismissed,
    }


def draft(seq: int = 1, **overrides: object) -> DeliveryDraft:
    """A default legal delivery draft (dot ball, no wicket)."""
    base = DeliveryDraft(
        delivery_seq=seq,
        over_number=1,
        ball_in_over=seq,
        bowler_id=uuid4(),
        batter_id=uuid4(),
        non_striker_id=uuid4(),
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def draft_with_players(ctx: dict[str, object], seq: int = 1, **overrides: object) -> DeliveryDraft:
    base = DeliveryDraft(
        delivery_seq=seq,
        over_number=1,
        ball_in_over=seq,
        bowler_id=ctx["bowler"].id,  # type: ignore[attr-defined]
        batter_id=ctx["batter"].id,  # type: ignore[attr-defined]
        non_striker_id=ctx["non_striker"].id,  # type: ignore[attr-defined]
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def current_rows(innings_id: object) -> list[dict[str, object]]:
    return list(
        Delivery.objects.filter(innings_id=innings_id, is_current=True)
        .order_by("delivery_seq")
        .values(
            "delivery_seq",
            "revision",
            "runs_off_bat",
            "is_wicket",
            "is_current",
            "match_date",
            "dismissal_type",
        )
    )


# --------------------------------------------------------------------------- #
# Boundary validation (DATA_INGESTION §6) — unit, no DB
# --------------------------------------------------------------------------- #


class TestValidateDrafts:
    def test_valid_batch_passes(self) -> None:
        validate_drafts([draft(1), draft(2), draft(3)])

    def test_gaps_are_allowed(self) -> None:
        validate_drafts([draft(1), draft(5), draft(9)])

    def test_duplicate_seq_rejected(self) -> None:
        with pytest.raises(DeliveryValidationError) as exc:
            validate_drafts([draft(2), draft(2)])
        assert exc.value.code == "duplicate_delivery_seq"
        assert exc.value.delivery_seq == 2

    def test_runs_out_of_range_rejected(self) -> None:
        with pytest.raises(DeliveryValidationError) as exc:
            validate_drafts([draft(1, runs_off_bat=7)])
        assert exc.value.code == "runs_out_of_range"

    def test_negative_runs_rejected(self) -> None:
        with pytest.raises(DeliveryValidationError) as exc:
            validate_drafts([draft(1, runs_off_bat=-1)])
        assert exc.value.code == "runs_out_of_range"

    def test_negative_extras_rejected(self) -> None:
        with pytest.raises(DeliveryValidationError) as exc:
            validate_drafts([draft(1, wides=-1)])
        assert exc.value.code == "negative_extras"

    def test_wicket_requires_dismissal(self) -> None:
        with pytest.raises(DeliveryValidationError) as exc:
            validate_drafts([draft(1, is_wicket=True)])
        assert exc.value.code == "wicket_requires_dismissal"

    def test_wicket_requires_dismissed_player(self) -> None:
        with pytest.raises(DeliveryValidationError) as exc:
            validate_drafts(
                [
                    draft(
                        1,
                        is_wicket=True,
                        dismissal_type=DismissalType.CAUGHT,
                        dismissed_player_id=None,
                    )
                ]
            )
        assert exc.value.code == "wicket_requires_dismissal"

    def test_dismissal_without_wicket_rejected(self) -> None:
        with pytest.raises(DeliveryValidationError) as exc:
            validate_drafts([draft(1, dismissal_type=DismissalType.CAUGHT)])
        assert exc.value.code == "dismissal_without_wicket"

    def test_unknown_dismissal_enum_rejected(self) -> None:
        with pytest.raises(DeliveryValidationError) as exc:
            validate_drafts(
                [
                    draft(
                        1,
                        is_wicket=True,
                        dismissed_player_id=uuid4(),
                        dismissal_type="WICKETKEEPER_CATCH",
                    )
                ]
            )
        assert exc.value.code == "invalid_enum"

    def test_unknown_retirement_enum_rejected(self) -> None:
        with pytest.raises(DeliveryValidationError) as exc:
            validate_drafts([draft(1, retired_player_id=uuid4(), retirement_type="ANGRY")])
        assert exc.value.code == "invalid_enum"

    def test_retirement_type_without_player_rejected(self) -> None:
        with pytest.raises(DeliveryValidationError) as exc:
            validate_drafts([draft(1, retirement_type=RetirementType.HURT)])
        assert exc.value.code == "retirement_inconsistent"

    def test_retirement_cannot_be_a_wicket(self) -> None:
        with pytest.raises(DeliveryValidationError) as exc:
            validate_drafts(
                [
                    draft(
                        1,
                        is_wicket=True,
                        dismissed_player_id=uuid4(),
                        dismissal_type=DismissalType.BOWLED,
                        retired_player_id=uuid4(),
                        retirement_type=RetirementType.HURT,
                    )
                ]
            )
        assert exc.value.code == "retirement_inconsistent"

    def test_retirement_never_carries_dismissal_type(self) -> None:
        with pytest.raises(DeliveryValidationError) as exc:
            validate_drafts(
                [
                    draft(
                        1,
                        is_wicket=True,
                        dismissed_player_id=uuid4(),
                        dismissal_type=DismissalType.BOWLED,
                        retired_player_id=uuid4(),
                        retirement_type=RetirementType.HURT,
                    )
                ]
            )
        assert exc.value.code == "retirement_inconsistent"

    def test_retired_out_without_wicket_rejected(self) -> None:
        """RETIRED_OUT is a dismissal; a dismissal without is_wicket fails the
        wicket-consistency rule (ck_delivery_wicket) before it reaches the DB."""
        with pytest.raises(DeliveryValidationError) as exc:
            validate_drafts(
                [
                    draft(
                        1,
                        dismissed_player_id=uuid4(),
                        dismissal_type=DismissalType.RETIRED_OUT,
                    )
                ]
            )
        assert exc.value.code == "dismissal_without_wicket"

    def test_valid_retired_out_is_accepted(self) -> None:
        validate_drafts(
            [
                draft(
                    1,
                    is_wicket=True,
                    dismissed_player_id=uuid4(),
                    dismissal_type=DismissalType.RETIRED_OUT,
                    bowler_credited_wicket=False,
                )
            ]
        )

    def test_valid_hurt_retirement_is_accepted(self) -> None:
        validate_drafts([draft(1, retired_player_id=uuid4(), retirement_type=RetirementType.HURT)])

    def test_invalid_seq_rejected(self) -> None:
        with pytest.raises(DeliveryValidationError) as exc:
            validate_drafts([draft(0)])
        assert exc.value.code == "invalid_seq"

    def test_error_carries_innings_and_seq(self) -> None:
        with pytest.raises(DeliveryValidationError) as exc:
            validate_drafts([draft(4, runs_off_bat=9)])
        assert exc.value.delivery_seq == 4


# --------------------------------------------------------------------------- #
# append_or_revise — bulk path, idempotency, supersede, match_date
# --------------------------------------------------------------------------- #


class TestAppendOrRevise:
    def test_bulk_fast_path_appends_all_and_derives_match_date(self) -> None:
        ctx = make_context()
        rows = [draft_with_players(ctx, seq=i) for i in range(1, 11)]
        report = append_or_revise(ctx["innings"], rows, source=make_provider())

        assert report["appended"] == list(range(1, 11))
        assert report["corrected"] == []
        assert report["noop"] == []
        assert Delivery.objects.filter(innings_id=ctx["innings"].id).count() == 10
        # match_date == match START date, never derived from ball content.
        expected_date = ctx["match"].start_time_utc.date()  # type: ignore[attr-defined]
        assert report["match_date"] == expected_date
        assert match_date_for(ctx["innings"]) == expected_date
        for row in current_rows(ctx["innings"].id):
            assert row["match_date"] == expected_date

    def test_generated_columns_are_computed(self) -> None:
        ctx = make_context()
        rows = [
            draft_with_players(ctx, seq=1, runs_off_bat=4),
            draft_with_players(ctx, seq=2, wides=1),
            draft_with_players(ctx, seq=3, noballs=1, runs_off_bat=6),
        ]
        append_or_revise(ctx["innings"], rows, source=make_provider())
        stored = Delivery.objects.filter(innings_id=ctx["innings"].id).order_by("delivery_seq")
        values = [(d.total_runs, d.is_wide, d.is_noball, d.is_legal) for d in stored]
        assert values == [
            (4, False, False, True),
            (1, True, False, False),
            (7, False, True, False),
        ]

    def test_wicket_ball_persists_dismissal(self) -> None:
        ctx = make_context()
        rows = [
            draft_with_players(
                ctx,
                seq=1,
                runs_off_bat=0,
                is_wicket=True,
                dismissal_type=DismissalType.CAUGHT,
                dismissed_player_id=ctx["dismissed"].id,  # type: ignore[attr-defined]
                bowler_credited_wicket=False,
                fielder1_id=ctx["non_striker"].id,  # type: ignore[attr-defined]
            )
        ]
        append_or_revise(ctx["innings"], rows, source=make_provider())
        (row,) = current_rows(ctx["innings"].id)
        assert row["is_wicket"] is True
        assert row["dismissal_type"] == DismissalType.CAUGHT

    def test_reimport_identical_is_noop(self) -> None:
        ctx = make_context()
        provider = make_provider()
        rows = [draft_with_players(ctx, seq=i) for i in range(1, 6)]
        append_or_revise(ctx["innings"], rows, source=provider)
        report = append_or_revise(ctx["innings"], rows, source=provider)

        assert report["appended"] == []
        assert report["corrected"] == []
        assert report["noop"] == list(range(1, 6))
        assert Delivery.objects.filter(innings_id=ctx["innings"].id).count() == 5
        assert DataCorrection.objects.filter(entity_type="delivery").count() == 0
        for row in current_rows(ctx["innings"].id):
            assert row["revision"] == 1
            assert row["is_current"] is True

    def test_changed_content_supersedes_and_bumps_revision(self) -> None:
        ctx = make_context()
        provider = make_provider()
        v1 = [draft_with_players(ctx, seq=1, runs_off_bat=2), draft_with_players(ctx, seq=2)]
        append_or_revise(ctx["innings"], v1, source=provider)

        v2 = [draft_with_players(ctx, seq=1, runs_off_bat=4), draft_with_players(ctx, seq=2)]
        report = append_or_revise(ctx["innings"], v2, source=provider)

        assert report["corrected"] == [1]
        assert report["noop"] == [2]
        # Exactly one CURRENT row per ball, revision bumped only on the change.
        rows = current_rows(ctx["innings"].id)
        by_seq = {r["delivery_seq"]: r for r in rows}
        assert by_seq[1]["revision"] == 2
        assert by_seq[1]["runs_off_bat"] == 4
        assert by_seq[2]["revision"] == 1
        # The superseded row is retained (history), flagged not-current.
        all_rows = Delivery.objects.filter(innings_id=ctx["innings"].id).order_by(
            "delivery_seq", "revision"
        )
        assert all_rows.count() == 3
        superseded = all_rows.filter(is_current=False)
        assert superseded.count() == 1
        assert superseded.first().superseded_at is not None  # type: ignore[union-attr]
        # One current row per (innings, seq) — the partial unique invariant.
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT delivery_seq, count(*) FROM cricket.delivery "
                "WHERE innings_id = %s AND is_current GROUP BY delivery_seq",
                [str(ctx["innings"].id)],
            )
            counts = dict(cursor.fetchall())
        assert set(counts.values()) == {1}

    def test_correction_audit_row_written(self) -> None:
        ctx = make_context()
        provider = make_provider()
        append_or_revise(
            ctx["innings"], [draft_with_players(ctx, seq=1, runs_off_bat=1)], source=provider
        )
        append_or_revise(
            ctx["innings"], [draft_with_players(ctx, seq=1, runs_off_bat=6)], source=provider
        )

        corrections = DataCorrection.objects.filter(entity_type="delivery")
        assert corrections.count() == 1
        corr = corrections.first()
        assert corr.provider == provider  # type: ignore[attr-defined]
        assert corr.source_revision == 1  # type: ignore[attr-defined]
        assert corr.target_revision == 2  # type: ignore[attr-defined]
        assert corr.old_value["runs_off_bat"] == 1  # type: ignore[index]
        assert corr.new_value["runs_off_bat"] == 6  # type: ignore[index]
        assert corr.reason == "provider content revision"  # type: ignore[attr-defined]

    def test_stale_revision_is_noop(self) -> None:
        ctx = make_context()
        provider = make_provider()
        # Provider revision 2 is already imported (content = 4 runs).
        append_or_revise(
            ctx["innings"],
            [draft_with_players(ctx, seq=1, runs_off_bat=4, revision=2)],
            source=provider,
        )
        # An older provider revision (1) arriving late must not clobber newer data.
        stale = replace(draft_with_players(ctx, seq=1, runs_off_bat=2), revision=1)
        report = append_or_revise(ctx["innings"], [stale], source=provider)

        assert report["noop"] == [1]
        (row,) = current_rows(ctx["innings"].id)
        assert row["revision"] == 2
        assert row["runs_off_bat"] == 4

    def test_same_revision_different_content_is_a_correction(self) -> None:
        """A provider revision is a content version, not a trust boundary: the
        same revision arriving with different content is still a correction
        (the content-hash idempotency key differs), and supersedes to a new
        platform revision."""
        ctx = make_context()
        provider = make_provider()
        append_or_revise(
            ctx["innings"], [draft_with_players(ctx, seq=1, runs_off_bat=2)], source=provider
        )
        changed = draft_with_players(ctx, seq=1, runs_off_bat=4)  # same revision, new content
        report = append_or_revise(ctx["innings"], [changed], source=provider)

        assert report["corrected"] == [1]
        (row,) = current_rows(ctx["innings"].id)
        assert row["revision"] == 2
        assert row["runs_off_bat"] == 4

    def test_appends_new_ball_on_slow_path(self) -> None:
        ctx = make_context()
        provider = make_provider()
        append_or_revise(ctx["innings"], [draft_with_players(ctx, seq=1)], source=provider)
        report = append_or_revise(ctx["innings"], [draft_with_players(ctx, seq=2)], source=provider)

        assert report["appended"] == [2]
        assert Delivery.objects.filter(innings_id=ctx["innings"].id).count() == 2

    def test_shuffled_order_produces_identical_state(self) -> None:
        ctx = make_context()
        provider = make_provider()
        rows = [
            draft_with_players(ctx, seq=1, runs_off_bat=1),
            draft_with_players(ctx, seq=2, wides=1),
            draft_with_players(ctx, seq=3, runs_off_bat=4),
        ]
        shuffled = [rows[2], rows[0], rows[1]]
        append_or_revise(ctx["innings"], shuffled, source=provider)
        # Same state as importing in order: seqs, revisions, one row each.
        rows_in_db = current_rows(ctx["innings"].id)
        assert [r["delivery_seq"] for r in rows_in_db] == [1, 2, 3]
        assert all(r["revision"] == 1 for r in rows_in_db)

    def test_bulk_path_with_large_batch(self) -> None:
        """The 10k+ deliveries/s target: a single batched bulk_create path."""
        ctx = make_context()
        rows = [draft_with_players(ctx, seq=i) for i in range(1, 501)]
        report = append_or_revise(ctx["innings"], rows, source=make_provider())
        assert len(report["appended"]) == 500
        assert Delivery.objects.filter(innings_id=ctx["innings"].id).count() == 500

    def test_validation_failure_leaves_no_rows(self) -> None:
        """A bad batch must not write anything (validate before insert)."""
        ctx = make_context()
        with pytest.raises(DeliveryValidationError):
            append_or_revise(
                ctx["innings"],
                [draft_with_players(ctx, seq=1), draft_with_players(ctx, seq=2, runs_off_bat=9)],
            )
        assert Delivery.objects.filter(innings_id=ctx["innings"].id).count() == 0


# --------------------------------------------------------------------------- #
# Events (publish after commit; suppressible)
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _clear_events() -> None:
    core_events.clear_subscriptions()
    yield
    core_events.clear_subscriptions()


def _collect(topic: str) -> Callable[[], list[dict[str, object]]]:
    received: list[dict[str, object]] = []
    core_events.subscribe(topic, lambda p: received.append(p))
    return lambda: received


@pytest.mark.django_db(transaction=True)
class TestEvents:
    def test_appended_event_published_after_commit(self) -> None:
        ctx = make_context()
        received = _collect(core_events.TOPIC_DELIVERY_APPENDED)
        append_or_revise(ctx["innings"], [draft_with_players(ctx, seq=1)], source=make_provider())
        assert len(received()) == 1
        payload = received()[0]
        assert payload["innings_id"] == str(ctx["innings"].id)
        assert payload["appended"] == [1]
        assert payload["source"] == "cricsheet"

    def test_corrected_event_published(self) -> None:
        ctx = make_context()
        provider = make_provider()
        corrected = _collect(core_events.TOPIC_DELIVERY_CORRECTED)
        append_or_revise(
            ctx["innings"], [draft_with_players(ctx, seq=1, runs_off_bat=1)], source=provider
        )
        append_or_revise(
            ctx["innings"], [draft_with_players(ctx, seq=1, runs_off_bat=4)], source=provider
        )
        assert len(corrected()) == 1
        assert corrected()[0]["corrected"] == [1]

    def test_suppress_events_blocks_both(self) -> None:
        ctx = make_context()
        provider = make_provider()
        appended = _collect(core_events.TOPIC_DELIVERY_APPENDED)
        corrected = _collect(core_events.TOPIC_DELIVERY_CORRECTED)
        append_or_revise(
            ctx["innings"],
            [draft_with_players(ctx, seq=1, runs_off_bat=1)],
            source=provider,
            suppress_events=True,
        )
        append_or_revise(
            ctx["innings"],
            [draft_with_players(ctx, seq=1, runs_off_bat=4)],
            source=provider,
            suppress_events=True,
        )
        assert appended() == []
        assert corrected() == []

    def test_noop_import_publishes_no_events(self) -> None:
        ctx = make_context()
        provider = make_provider()
        received = _collect(core_events.TOPIC_DELIVERY_APPENDED)
        rows = [draft_with_players(ctx, seq=1)]
        append_or_revise(ctx["innings"], rows, source=provider)
        assert len(received()) == 1
        append_or_revise(ctx["innings"], rows, source=provider)
        assert len(received()) == 1, "idempotent re-import must not re-publish"
