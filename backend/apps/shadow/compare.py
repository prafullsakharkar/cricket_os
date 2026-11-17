"""Shadow comparison: diff the platform's card against the provider and a
second source (Phase I Task 2).

The comparison is **read-only and never auto-corrects**: a mismatch is a
:class:`ComparisonFinding` that the driver records (metrics + DLQ queue row +
dated report). No match row is ever written by this module.

Categories (per the brief): derived totals, wickets, extras, result/status,
per-batter lines, per-bowler lines. Severities: ``critical`` for totals/wickets/
extras/result (a displayed score would be wrong), ``diff`` for batting/bowling
lines and overs_display.

``compare_match`` is side-effect free so the unit suite can inject mismatches
and assert deterministic findings. The reporter hook is registered by
``apps.ingestion`` (dependency inversion: this app never imports ingestion).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from apps.shadow.cards import Card, card_from_deliveries, card_from_provider, card_from_read_models

#: Categories the comparison can flag (brief Task 2).
CATEGORIES = ("totals", "wickets", "extras", "result", "batting", "bowling")

SEVERITY_CRITICAL = "critical"
SEVERITY_DIFF = "diff"


@dataclass(frozen=True)
class ComparisonFinding:
    """One value-level mismatch between the three card views."""

    category: str
    match_id: str
    innings_number: int | None = None
    entity_key: str | None = None
    field: str = ""
    ours: Any = None
    provider: Any = None
    second: Any = None
    severity: str = SEVERITY_DIFF

    def as_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "match_id": self.match_id,
            "innings_number": self.innings_number,
            "entity_key": self.entity_key,
            "field": self.field,
            "ours": _serialize(self.ours),
            "provider": _serialize(self.provider),
            "second": _serialize(self.second),
            "severity": self.severity,
        }


@dataclass
class ComparisonReport:
    """The outcome of one match comparison."""

    match_id: str
    compared_at: datetime
    findings: list[ComparisonFinding] = field(default_factory=list)

    @property
    def mismatch_count(self) -> int:
        return len(self.findings)

    @property
    def by_category(self) -> dict[str, int]:
        counts = dict.fromkeys(CATEGORIES, 0)
        for finding in self.findings:
            counts[finding.category] += 1
        return counts

    @property
    def by_severity(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for finding in self.findings:
            counts[finding.severity] = counts.get(finding.severity, 0) + 1
        return counts


#: Reporter hook (registered by apps.ingestion): receives each finding so the
#: DLQ row is written by the DLQ owner, never by this app.
_shadow_comparison_reporter: Callable[[ComparisonFinding], Any] | None = None


def register_shadow_comparison_reporter(
    reporter: Callable[[ComparisonFinding], Any] | None,
) -> None:
    """Set the callback that surfaces a shadow-comparison finding to ops."""
    global _shadow_comparison_reporter
    _shadow_comparison_reporter = reporter


def report_finding(finding: ComparisonFinding) -> None:
    """Queue a finding via the registered reporter (no-op when unregistered)."""
    if _shadow_comparison_reporter is not None:
        _shadow_comparison_reporter(finding)


def compare_match(
    match: Any,
    *,
    adapter: Any | None = None,
    ours: Card | None = None,
    provider: Card | None = None,
    second: Card | None = None,
) -> ComparisonReport:
    """Diff the three card views for one match; never writes to the match.

    ``adapter`` defaults to a ``ReplayAdapter`` instance (provider gate fails).
    ``ours``/``provider``/``second`` may be injected for unit tests.
    """
    from apps.providers.adapters.replay import ReplayAdapter

    if adapter is None:
        adapter = ReplayAdapter()
    ours_card = ours if ours is not None else card_from_read_models(match)
    provider_card = provider if provider is not None else card_from_provider(match, adapter)
    second_card = second if second is not None else card_from_deliveries(match)

    findings: list[ComparisonFinding] = []
    match_id = str(match.id)
    for innings in ours_card.innings:
        _compare_innings(
            match_id,
            findings,
            innings,
            _innings_by_number(provider_card, innings.innings_number),
            _innings_by_number(second_card, innings.innings_number),
        )
    _compare_status(match_id, findings, ours_card, provider_card, second_card)

    return ComparisonReport(
        match_id=match_id,
        compared_at=datetime.now(UTC),
        findings=findings,
    )


# --------------------------------------------------------------------------- #
# Per-innings comparison
# --------------------------------------------------------------------------- #


def _compare_innings(
    match_id: str,
    findings: list[ComparisonFinding],
    ours: Any,
    provider: Any,
    second: Any,
) -> None:
    inn = ours.innings_number

    # Totals (critical): runs + overs_display.
    for other in (provider, second):
        if other is None:
            continue
        if ours.runs != other.runs:
            findings.append(
                ComparisonFinding(
                    category="totals",
                    match_id=match_id,
                    innings_number=inn,
                    field="runs",
                    ours=ours.runs,
                    provider=provider.runs if provider else None,
                    second=second.runs if second else None,
                    severity=SEVERITY_CRITICAL,
                )
            )
        if ours.overs_display != other.overs_display:
            findings.append(
                ComparisonFinding(
                    category="totals",
                    match_id=match_id,
                    innings_number=inn,
                    field="overs_display",
                    ours=ours.overs_display,
                    provider=provider.overs_display if provider else None,
                    second=second.overs_display if second else None,
                )
            )

    # Wickets (critical).
    for other in (provider, second):
        if other is None:
            continue
        if ours.wickets != other.wickets:
            findings.append(
                ComparisonFinding(
                    category="wickets",
                    match_id=match_id,
                    innings_number=inn,
                    field="wickets",
                    ours=ours.wickets,
                    provider=provider.wickets if provider else None,
                    second=second.wickets if second else None,
                    severity=SEVERITY_CRITICAL,
                )
            )

    # Extras (critical): compare the extras dict field by field.
    for key in ("wides", "noballs", "byes", "leg_byes", "penalty", "total"):
        ours_value = ours.extras.get(key, 0)
        provider_value = (provider.extras or {}).get(key, 0) if provider else None
        second_value = (second.extras or {}).get(key, 0) if second else None
        provider_mismatch = provider is not None and ours_value != provider_value
        second_mismatch = second is not None and ours_value != second_value
        if provider_mismatch or second_mismatch:
            findings.append(
                ComparisonFinding(
                    category="extras",
                    match_id=match_id,
                    innings_number=inn,
                    field=key,
                    ours=ours_value,
                    provider=provider_value,
                    second=second_value,
                    severity=SEVERITY_CRITICAL,
                )
            )

    # Per-batter lines (diff severity): ours vs second derivation.
    if second is not None:
        second_batting = {entry.player_key: entry for entry in second.batting}
        for entry in ours.batting:
            other = second_batting.get(entry.player_key)
            if other is None:
                continue  # a batter absent from the raw derivation is not a value diff
            for attr in ("runs", "balls", "fours", "sixes", "dismissal"):
                if getattr(entry, attr) != getattr(other, attr):
                    findings.append(
                        ComparisonFinding(
                            category="batting",
                            match_id=match_id,
                            innings_number=inn,
                            entity_key=entry.player_key,
                            field=attr,
                            ours=getattr(entry, attr),
                            provider=None,
                            second=getattr(other, attr),
                        )
                    )

    # Per-bowler lines (diff severity): ours vs second derivation.
    if second is not None:
        second_bowling = {entry.player_key: entry for entry in second.bowling}
        for entry in ours.bowling:
            other = second_bowling.get(entry.player_key)
            if other is None:
                continue
            for attr in ("legal_balls", "runs", "wickets"):
                if getattr(entry, attr) != getattr(other, attr):
                    findings.append(
                        ComparisonFinding(
                            category="bowling",
                            match_id=match_id,
                            innings_number=inn,
                            entity_key=entry.player_key,
                            field=attr,
                            ours=getattr(entry, attr),
                            provider=None,
                            second=getattr(other, attr),
                        )
                    )


def _compare_status(
    match_id: str,
    findings: list[ComparisonFinding],
    ours: Card,
    provider: Card,
    second: Card,
) -> None:
    # Result/status (critical): the lifecycle state must agree across sources.
    for other in (provider, second):
        if other is None or not other.status:
            continue
        if ours.status != other.status:
            findings.append(
                ComparisonFinding(
                    category="result",
                    match_id=match_id,
                    field="status",
                    ours=ours.status,
                    provider=provider.status if provider else None,
                    second=second.status if second else None,
                    severity=SEVERITY_CRITICAL,
                )
            )


def _innings_by_number(card: Card, number: int) -> Any:
    for innings in card.innings:
        if innings.innings_number == number:
            return innings
    return None


def _serialize(value: Any) -> Any:
    if value is None:
        return None
    return str(value)


__all__ = [
    "CATEGORIES",
    "SEVERITY_CRITICAL",
    "SEVERITY_DIFF",
    "ComparisonFinding",
    "ComparisonReport",
    "compare_match",
    "register_shadow_comparison_reporter",
    "report_finding",
]
