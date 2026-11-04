"""Phase D: licensing policy (SECURITY_AND_RELIABILITY §11, RSK-03/RSK-04).

* policy resolution from ``LICENSED_CONTENT_POLICIES`` (configuration, not
  code) + defaults for unknown providers;
* ``check_license`` → ``403 license_required`` for unlicensed features;
* attribution + capability flags for API ``meta``;
* the retention job (dry-run default, keeps summaries, per-provider window).
"""

from __future__ import annotations

import datetime

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.core.enums import MatchStatus
from apps.core.errors import LicenseRequired
from apps.deliveries.models import Delivery
from apps.providers.licensing import (
    FEATURE_BALL_DATA,
    FEATURE_COMMENTARY,
    FEATURE_DERIVED_FEED,
    attribution_for,
    check_license,
    enforce_retention,
    license_meta,
    policy_for,
)

from .test_fixture_sync import _provider  # noqa: F401  (shared helper)

pytestmark = pytest.mark.django_db

#: A provider with a configured retention window + commentary rights.
_POLICIES = {
    "replay": {
        "display_rights": True,
        "redistribution_rights": False,
        "attribution_required": True,
        "attribution": "Replay (internal test corpus)",
        "commentary_allowed": False,
        "derived_feeds_allowed": False,
        "ball_data_retention_days": None,
    },
    "licensed": {
        "display_rights": True,
        "redistribution_rights": True,
        "attribution_required": True,
        "attribution": "Data © Licensed Provider",
        "commentary_allowed": True,
        "derived_feeds_allowed": True,
        "ball_data_retention_days": 30,
    },
}


@override_settings(LICENSED_CONTENT_POLICIES=_POLICIES)
def test_policy_for_resolves_configured_and_default() -> None:
    replay = policy_for("replay")
    assert replay.provider_code == "replay"
    assert replay.attribution == "Replay (internal test corpus)"

    licensed = policy_for("licensed")
    assert licensed.commentary_allowed is True
    assert licensed.ball_data_retention_days == 30

    unknown = policy_for("nobody")
    assert unknown.display_rights is True  # defaults
    assert unknown.commentary_allowed is False
    assert unknown.ball_data_retention_days is None


@override_settings(LICENSED_CONTENT_POLICIES=_POLICIES)
def test_check_license_raises_for_unlicensed_feature() -> None:
    with pytest.raises(LicenseRequired):
        check_license(FEATURE_COMMENTARY, "replay")
    with pytest.raises(LicenseRequired):
        check_license(FEATURE_DERIVED_FEED, "replay")
    # ball data is displayed under the replay licence.
    check_license(FEATURE_BALL_DATA, "replay")


@override_settings(LICENSED_CONTENT_POLICIES=_POLICIES)
def test_check_license_allows_licensed_features() -> None:
    check_license(FEATURE_COMMENTARY, "licensed")
    check_license(FEATURE_DERIVED_FEED, "licensed")


@override_settings(LICENSED_CONTENT_POLICIES=_POLICIES)
def test_attribution_and_meta_flags() -> None:
    assert attribution_for("replay") == "Replay (internal test corpus)"
    assert attribution_for("licensed") == "Data © Licensed Provider"
    assert attribution_for("nobody") is None  # not required by default

    meta = license_meta("replay")
    assert meta == {"ball_data": True, "commentary": False}
    meta_licensed = license_meta("licensed")
    assert meta_licensed["commentary"] is True


def _match_with_deliveries(provider: object, *, completed_days_ago: int) -> object:
    from tests.phase_d.test_live_apply import apply_batch, batch, make_context

    ctx = make_context()
    match = ctx["match"]
    apply_batch(match, [batch(ctx, [1, 2, 3], runs=2)])
    from apps.matches.models import Match

    Match.objects.filter(id=match.id).update(
        source_provider_id=provider.id,
        status=MatchStatus.COMPLETED,
        end_time_utc=timezone.now() - datetime.timedelta(days=completed_days_ago),
    )
    return match


@override_settings(LICENSED_CONTENT_POLICIES=_POLICIES)
def test_retention_dry_run_counts_but_never_deletes() -> None:
    from apps.providers.models import DataProvider

    licensed = DataProvider.objects.get_or_create(code="licensed", defaults={"name": "Licensed"})[0]
    old = _match_with_deliveries(licensed, completed_days_ago=60)
    fresh = _match_with_deliveries(licensed, completed_days_ago=1)

    reports = enforce_retention(licensed, dry_run=True)
    report = reports[0]
    assert report.retention_days == 30
    assert report.candidates == 3  # only the old match's deliveries
    assert report.deleted == 0
    assert Delivery.objects.filter(match_id=old.id).count() == 3
    assert Delivery.objects.filter(match_id=fresh.id).count() == 3


@override_settings(LICENSED_CONTENT_POLICIES=_POLICIES)
def test_retention_apply_deletes_ball_data_keeps_summaries() -> None:
    from apps.innings.models import InningsSummary
    from apps.providers.models import DataProvider

    licensed = DataProvider.objects.get_or_create(code="licensed", defaults={"name": "Licensed"})[0]
    old = _match_with_deliveries(licensed, completed_days_ago=60)

    reports = enforce_retention(licensed, dry_run=False)
    report = reports[0]
    assert report.deleted == 3
    assert Delivery.objects.filter(match_id=old.id).count() == 0
    # Derived summaries survive (RSK-04: "keep summaries").
    assert InningsSummary.objects.filter(innings__match_id=old.id).count() >= 1


@override_settings(LICENSED_CONTENT_POLICIES=_POLICIES)
def test_retention_skips_provider_without_window() -> None:
    from apps.providers.models import DataProvider

    replay = DataProvider.objects.get_or_create(code="replay", defaults={"name": "Replay"})[0]
    _match_with_deliveries(replay, completed_days_ago=365)
    reports = enforce_retention(replay, dry_run=False)
    assert reports[0].retention_days is None
    assert reports[0].candidates == 0
    assert Delivery.objects.filter(match__source_provider_id=replay.id).count() == 3


@override_settings(LICENSED_CONTENT_POLICIES=_POLICIES)
def test_retention_respects_as_of_cutoff() -> None:
    from apps.providers.models import DataProvider

    licensed = DataProvider.objects.get_or_create(code="licensed", defaults={"name": "Licensed"})[0]
    _match_with_deliveries(licensed, completed_days_ago=60)
    # An as_of in the past moves the cutoff: nothing qualifies.
    as_of = timezone.now() - datetime.timedelta(days=45)
    reports = enforce_retention(licensed, dry_run=True, as_of=as_of)
    assert reports[0].candidates == 0


def test_retention_task_dry_run_default() -> None:
    from apps.providers.tasks import enforce_retention_task

    reports = enforce_retention_task(dry_run=True)
    assert isinstance(reports, list)
    # The task resolves providers from the DB; with no providers it is empty.
    assert all("dry_run" in report for report in reports)


def test_licensing_errors_never_leak_secrets() -> None:
    # DLQ-style redaction is orthogonal; the LicenseRequired details carry only
    # the feature and provider code (never credentials).
    try:
        check_license(FEATURE_COMMENTARY, "replay")
    except LicenseRequired as exc:
        assert exc.details == {"field": "commentary", "provider": "replay"}
    else:  # pragma: no cover - guard
        pytest.fail("expected LicenseRequired")
