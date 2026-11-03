"""Live fixture sync (DATA_INGESTION §2, DOMAIN_MODEL §6.6).

``sync_fixtures`` folds a provider's fixture list for a window into the
``matches`` table, idempotently keyed by ``(source_provider_id,
provider_match_id)`` (the ``ux_match_provider`` unique constraint):

* **created** — a new ``Match`` (plus ``MatchTeam`` rows via the shared
  team upsert, keyed by registry id or normalised name);
* **rescheduled** — ``start_time_utc`` changed → ``match.updated`` event-log
  row with the changed fields;
* **updated** — any other content change (venue/format/overs…) → the same
  ``match.updated`` event;
* **cancelled** — a fixture marked ``CANCELLED`` moves the stored match
  through the lifecycle transition table (``match.state_changed``); a fixture
  list may never *regress* an in-progress match back to SCHEDULED, so
  impossible transitions are skipped, never raised;
* **never deleted** — a fixture absent from a later window is a
  reconciliation concern (``reconcile_fixtures``), not a deletion.

Ownership: this module lives in ``synchronization`` because it shares the
checkpoint/correction/reconciliation audit discipline with the rest of the
module; the *caller* (the live beat task / management command) supplies an
error reporter so DLQ rows are written by ``apps.ingestion`` (dependency
inversion, same as reconciliation).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.core.enums import MatchStatus, TeamType
from apps.matches import event_schemas
from apps.matches.models import Match, MatchTeam
from apps.matches.services import event_log
from apps.matches.services import state as lifecycle
from apps.players.identity_scoring import normalize_name

#: Statuses a fixture list is allowed to impose on a stored match.
FIXTURE_IMPOSABLE_STATUSES = frozenset({MatchStatus.SCHEDULED, MatchStatus.CANCELLED})

#: International formats (mirrors ``ingestion.adapters.identity``; kept local
#: because ``synchronization`` must not import ``ingestion``).
_INTERNATIONAL_FORMATS = ("TEST", "ODI", "T20I")

#: Content fields (excluding status) that a fixture refresh may update.
_CONTENT_FIELDS = (
    "format_type",
    "gender",
    "start_time_utc",
    "source_local_date",
    "source_timezone",
    "overs_limit",
    "balls_per_over",
    "match_number",
    "match_desc",
    "state_title",
    "status_detail",
)


def _team_type_for(match_type: str, gender: str) -> str:
    if match_type in _INTERNATIONAL_FORMATS:
        return TeamType.WOMEN_INTERNATIONAL if gender == "W" else TeamType.INTERNATIONAL
    return TeamType.WOMEN_FRANCHISE if gender == "W" else TeamType.DOMESTIC


def _registry_identifier(registry: dict[str, dict[str, str]], name: str) -> str | None:
    """First non-empty registry id for ``name`` (``key_pulse``/``key_opta``/…)."""
    for value in (registry.get(name) or {}).values():
        if value:
            return value
    return None


@dataclass(frozen=True)
class FixtureRecord:
    """One normalized fixture from an adapter's ``list_fixtures`` window."""

    provider_match_id: str
    format_type: str
    start_time_utc: datetime
    gender: str = "M"
    source_local_date: Any = None  # date or None → derived from start_time_utc
    source_timezone: str = "UTC"
    overs_limit: int | None = None
    balls_per_over: int = 6
    status: str = MatchStatus.SCHEDULED
    state_title: str | None = None
    status_detail: str | None = None
    teams: tuple[str, ...] = ()
    team_registry: dict[str, dict[str, str]] = field(default_factory=dict)
    match_number: int | None = None
    match_desc: str | None = None

    def __post_init__(self) -> None:
        if self.source_local_date is None:
            object.__setattr__(self, "source_local_date", self.start_time_utc.date())


@dataclass(frozen=True)
class FixtureSyncReport:
    """What one window sync did (per provider)."""

    provider_code: str
    created: int = 0
    updated: int = 0
    rescheduled: int = 0
    cancelled: int = 0
    unchanged: int = 0
    skipped: int = 0
    errors: int = 0

    def __bool__(self) -> bool:
        return any(
            (
                self.created,
                self.updated,
                self.rescheduled,
                self.cancelled,
                self.errors,
            )
        )


def _content_updates(match: Match, record: FixtureRecord) -> dict[str, Any]:
    """Fields whose stored value differs from the fixture record."""
    updates: dict[str, Any] = {}
    for name in _CONTENT_FIELDS:
        current = getattr(match, name)
        incoming = getattr(record, name)
        if current != incoming:
            updates[name] = incoming
    return updates


def _event_fields(updates: dict[str, Any]) -> dict[str, Any]:
    """Event-payload-safe rendering of changed fields (datetimes → ISO)."""
    serialized: dict[str, Any] = {}
    for key, value in updates.items():
        if isinstance(value, datetime):
            serialized[key] = value.isoformat()
        else:
            serialized[key] = value
    return serialized


def _upsert_teams(match: Match, record: FixtureRecord, provider: Any) -> None:
    """MatchTeam rows via the shared team upsert (registry id or name)."""
    from apps.teams.services.teams import upsert_team

    for number, name in enumerate(record.teams, start=1):
        identifier = _registry_identifier(record.team_registry, name) or normalize_name(name)
        team = upsert_team(
            provider,
            identifier,
            name=name,
            team_type=_team_type_for(record.format_type, record.gender),
            gender=record.gender,
            is_international=record.format_type in _INTERNATIONAL_FORMATS,
        )
        MatchTeam.objects.get_or_create(match=match, team=team, defaults={"team_number": number})


def _sync_one(
    provider: Any,
    record: FixtureRecord,
    *,
    source: Any = None,
) -> tuple[str, FixtureSyncReport]:
    """Upsert one fixture; returns ``(match_id, delta report)``."""
    match, created = Match.objects.get_or_create(
        source_provider_id=provider.pk,
        provider_match_id=record.provider_match_id,
        defaults={
            "format_type": record.format_type,
            "gender": record.gender,
            "start_time_utc": record.start_time_utc,
            "source_local_date": record.source_local_date,
            "source_timezone": record.source_timezone,
            "overs_limit": record.overs_limit,
            "balls_per_over": record.balls_per_over,
            "status": record.status
            if record.status in FIXTURE_IMPOSABLE_STATUSES
            else MatchStatus.SCHEDULED,
            "state_title": record.state_title,
            "status_detail": record.status_detail,
            "match_number": record.match_number,
            "match_desc": record.match_desc,
            "provider_updated_at": timezone.now(),
            "ingested_at": timezone.now(),
            "revision": 1,
        },
    )
    if created:
        _upsert_teams(match, record, provider)
        return str(match.id), FixtureSyncReport(provider_code=provider.code, created=1)

    event_log.lock_match(match.id)
    match.refresh_from_db()
    updates = _content_updates(match, record)
    rescheduled = updates.get("start_time_utc") is not None
    updated = bool(updates)
    if updated:
        updates["provider_updated_at"] = timezone.now()
        updates["revision"] = (match.revision or 0) + 1
        Match.objects.filter(id=match.id).update(**updates)
        match.refresh_from_db()
        event_log.append_event(
            match.id,
            event_schemas.MATCH_UPDATED,
            {
                "fields": _event_fields({k: v for k, v in updates.items() if k != "revision"}),
                "reason": "fixture_sync",
                "source": getattr(source, "code", getattr(provider, "code", None)),
            },
        )

    cancelled = False
    skipped = False
    if record.status in FIXTURE_IMPOSABLE_STATUSES and record.status != match.status:
        if lifecycle.can_transition(match.status, record.status):
            lifecycle.transition(
                match,
                record.status,
                state_title=record.state_title,
                status_detail=record.status_detail,
                source=source,
            )
            cancelled = record.status == MatchStatus.CANCELLED
        else:
            skipped = True

    unchanged = not (updated or cancelled or skipped)
    return str(match.id), FixtureSyncReport(
        provider_code=provider.code,
        updated=1 if updated else 0,
        rescheduled=1 if rescheduled else 0,
        cancelled=1 if cancelled else 0,
        skipped=1 if skipped else 0,
        unchanged=1 if unchanged else 0,
    )


def sync_fixtures(
    provider: Any,
    records: Iterable[FixtureRecord],
    *,
    source: Any = None,
    on_error: Callable[[FixtureRecord, Exception], None] | None = None,
) -> FixtureSyncReport:
    """Upsert every fixture in ``records`` for ``provider`` (never deletes).

    Each record commits atomically (match, teams, events, state transition);
    a malformed record is counted as an error and reported via ``on_error``
    (the live pipeline wires this to the redacted ``IngestionError`` DLQ) so
    one bad fixture cannot abort the window.
    """
    total = FixtureSyncReport(provider_code=provider.code)
    for record in records:
        try:
            with transaction.atomic():
                _, delta = _sync_one(provider, record, source=source)
        except Exception as exc:
            if on_error is not None:
                on_error(record, exc)
            total = replace(total, errors=total.errors + 1)
            continue
        total = replace(
            total,
            created=total.created + delta.created,
            updated=total.updated + delta.updated,
            rescheduled=total.rescheduled + delta.rescheduled,
            cancelled=total.cancelled + delta.cancelled,
            unchanged=total.unchanged + delta.unchanged,
            skipped=total.skipped + delta.skipped,
        )
    return total


__all__ = ["FixtureRecord", "FixtureSyncReport", "sync_fixtures"]
