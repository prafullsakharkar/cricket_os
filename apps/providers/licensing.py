"""Licensing policy (SECURITY_AND_RELIABILITY §11, RISKS RSK-03/RSK-04).

A ``LicensedContentPolicy`` per provider records display rights, redistribution
rights, retention limits and attribution requirements. The policy is
**configuration, not code** (``settings.LICENSED_CONTENT_POLICIES``), so a
licence change never requires a redesign.

* :func:`policy_for` — resolve a provider code to its policy (defaults for
  providers without an entry);
* :func:`check_license` — raise ``403 license_required`` for a feature the
  licence does not cover (commentary / derived feeds / ball data);
* :func:`attribution_for` — the attribution string when the licence requires it
  (rendered in API ``meta``);
* :func:`license_meta` — the capability flags an endpoint reports in ``meta``
  (``ball_data``, ``commentary``);
* :func:`enforce_retention` — the scheduled job that enforces per-provider
  ball-data retention limits (dry-run by default; keeps derived summaries).

Retention deletes the raw ``delivery`` rows for matches completed before the
provider's retention window — the licence-driven exception to the
cross-module-write rule (it deletes raw facts from their owning table; it never
writes derived rows).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.utils import timezone

from apps.core.errors import LicenseRequired

#: Licence feature names used by ``check_license``/``policy.allows``.
FEATURE_COMMENTARY = "commentary"
FEATURE_DERIVED_FEED = "derived_feed"
FEATURE_BALL_DATA = "ball_data"

#: Defaults applied to providers without an explicit policy entry: display is
#: allowed, but commentary and derived feeds are not licensed and no retention
#: limit applies.
_DEFAULT_POLICY: dict[str, Any] = {
    "display_rights": True,
    "redistribution_rights": False,
    "attribution_required": False,
    "attribution": None,
    "commentary_allowed": False,
    "derived_feeds_allowed": False,
    "ball_data_retention_days": None,
    "notes": None,
}


@dataclass(frozen=True)
class LicensedContentPolicy:
    """What the licence for one provider allows (SECURITY §11)."""

    provider_code: str
    display_rights: bool = True
    redistribution_rights: bool = False
    attribution_required: bool = False
    attribution: str | None = None
    commentary_allowed: bool = False
    derived_feeds_allowed: bool = False
    ball_data_retention_days: int | None = None
    notes: str | None = None

    def allows(self, feature: str) -> bool:
        """True when ``feature`` is covered by the licence."""
        if feature == FEATURE_COMMENTARY:
            return self.commentary_allowed
        if feature == FEATURE_DERIVED_FEED:
            return self.derived_feeds_allowed
        if feature == FEATURE_BALL_DATA:
            return self.display_rights
        return False


def _code(provider_or_code: Any) -> str | None:
    if isinstance(provider_or_code, str):
        return provider_or_code
    return getattr(provider_or_code, "code", None)


def _config() -> dict[str, dict[str, Any]]:
    return getattr(settings, "LICENSED_CONTENT_POLICIES", None) or {}


def policy_for(provider_or_code: Any) -> LicensedContentPolicy:
    """The policy for ``provider_or_code`` (defaults when not configured)."""
    code = _code(provider_or_code) or ""
    raw = _config().get(code)
    if raw is None:
        raw = {}
    merged: dict[str, Any] = {**_DEFAULT_POLICY, **raw, "provider_code": code}
    return LicensedContentPolicy(**merged)


def check_license(feature: str, provider_or_code: Any) -> None:
    """Raise ``403 license_required`` when ``feature`` is not licensed."""
    policy = policy_for(provider_or_code)
    if not policy.allows(feature):
        raise LicenseRequired(
            f"{feature} is not available under the current licence",
            details={"field": feature, "provider": policy.provider_code},
        )


def attribution_for(provider_or_code: Any) -> str | None:
    """The required attribution string, or ``None`` when not required."""
    policy = policy_for(provider_or_code)
    if policy.attribution_required:
        return policy.attribution
    return None


def license_meta(provider_or_code: Any) -> dict[str, bool]:
    """Capability flags for API ``meta`` (``ball_data``, ``commentary``)."""
    policy = policy_for(provider_or_code)
    return {
        "ball_data": policy.allows(FEATURE_BALL_DATA),
        "commentary": policy.allows(FEATURE_COMMENTARY),
    }


# --------------------------------------------------------------------------- #
# Retention
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RetentionReport:
    """What the retention job found/did (per provider, dry-run aware)."""

    dry_run: bool
    provider_code: str
    retention_days: int | None
    candidates: int = 0
    deleted: int = 0


def _retention_cutoff(as_of: Any, days: int) -> Any:
    return as_of - timedelta(days=days)


def enforce_retention(
    provider_or_code: Any = None,
    *,
    dry_run: bool = True,
    as_of: Any = None,
) -> list[RetentionReport]:
    """Enforce per-provider ball-data retention limits (dry-run by default).

    For every provider whose policy sets ``ball_data_retention_days``, find
    the current delivery rows of matches completed before the cutoff and
    (unless ``dry_run``) delete them. Derived summaries/cards are kept
    (RISKS RSK-04: "keep summaries"). ``as_of`` is injectable for tests.
    """
    from apps.deliveries.models import Delivery
    from apps.matches.models import Match
    from apps.providers.models import DataProvider

    as_of = as_of if as_of is not None else timezone.now()
    reports: list[RetentionReport] = []

    providers = DataProvider.objects.filter(is_active=True)
    if provider_or_code is not None:
        code = _code(provider_or_code)
        providers = providers.filter(code=code) if code else providers
    for provider in providers:
        policy = policy_for(provider)
        days = policy.ball_data_retention_days
        if days is None:
            reports.append(
                RetentionReport(dry_run=dry_run, provider_code=provider.code, retention_days=None)
            )
            continue
        cutoff = _retention_cutoff(as_of, days)
        match_ids = list(
            Match.objects.filter(
                source_provider_id=provider.id,
                status="COMPLETED",
                end_time_utc__lt=cutoff,
            ).values_list("id", flat=True)
        )
        candidates = Delivery.objects.filter(match_id__in=match_ids).count() if match_ids else 0
        deleted = 0
        if candidates and not dry_run:
            deleted, _ = Delivery.objects.filter(match_id__in=match_ids).delete()
        reports.append(
            RetentionReport(
                dry_run=dry_run,
                provider_code=provider.code,
                retention_days=days,
                candidates=candidates,
                deleted=deleted,
            )
        )
    return reports


__all__ = [
    "FEATURE_BALL_DATA",
    "FEATURE_COMMENTARY",
    "FEATURE_DERIVED_FEED",
    "LicensedContentPolicy",
    "RetentionReport",
    "attribution_for",
    "check_license",
    "enforce_retention",
    "license_meta",
    "policy_for",
]
