"""``manage.py link_provider_identities --provider replay [--entity-type ...] [--dry-run]``.

Batch-links a recorded live-provider entity corpus onto the platform's
canonical entities (Phase I Task 3), using the generic identity resolver
(``apps.providers.services.resolve``). Writes a dated coverage report to
``reports/phase_i/identity_coverage.md``: auto-linked vs candidate-pending vs
stub-created counts, the steward workload estimate (candidates × minutes-per-
review, ASSUMED), and residual duplicates in the corpus.

The corpus is the recorded sample at ``tests/fixtures/provider_live/players.yaml``
(a stand-in — UNVERIFIED against a real provider's view). ``--dry-run`` reports
what would happen without writing anything.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

import yaml
from django.core.management.base import BaseCommand, CommandError

from apps.providers.linking import (
    LINKABLE_ENTITY_TYPES,
    OUTCOME_ALREADY_LINKED,
    OUTCOME_AUTO_LINKED,
    OUTCOME_CANDIDATE_PENDING,
    OUTCOME_STUB_CREATED,
    LinkSummary,
    link_all,
)

#: Assumed minutes per steward review (ASSUMED, not measured).
MINUTES_PER_REVIEW = 2

DEFAULT_CORPUS = (
    Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "provider_live" / "players.yaml"
)
REPORT_PATH = Path("reports/phase_i/identity_coverage.md")

_OUTCOME_NAMES = (
    OUTCOME_ALREADY_LINKED,
    OUTCOME_AUTO_LINKED,
    OUTCOME_CANDIDATE_PENDING,
    OUTCOME_STUB_CREATED,
)


class Command(BaseCommand):
    help = "Batch-link a recorded live-provider corpus onto canonical entities."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--provider", default="replay", help="Provider code (default replay).")
        parser.add_argument(
            "--entity-type",
            default=None,
            choices=list(LINKABLE_ENTITY_TYPES),
            help="Restrict to one entity type.",
        )
        parser.add_argument("--corpus", default=str(DEFAULT_CORPUS), help="Corpus YAML path.")
        parser.add_argument("--dry-run", action="store_true", help="Report without writing.")

    def handle(self, *args: Any, **options: Any) -> None:
        from apps.providers.models import DataProvider

        provider = DataProvider.objects.filter(code=options["provider"]).first()
        if provider is None:
            raise CommandError(
                f"no DataProvider with code {options['provider']!r}; run migrations first"
            )

        corpus_path = Path(options["corpus"])
        if not corpus_path.is_file():
            raise CommandError(f"corpus file not found: {corpus_path}")
        with corpus_path.open(encoding="utf-8") as handle:
            document = yaml.safe_load(handle)

        dry_run = options["dry_run"]
        entity_type_filter = options["entity_type"]
        entries: list[tuple[str, str, dict[str, Any]]] = []
        for entry in document.get("corpus", []):
            entity_type = entry["entity_type"]
            if entity_type_filter and entity_type != entity_type_filter:
                continue
            attributes = _coerce_dates(_resolve_teams(dict(entry["attributes"])))
            entries.append((entity_type, entry["provider_entity_id"], attributes))

        summary = link_all(provider, entries, dry_run=dry_run)
        summary.entity_types = LINKABLE_ENTITY_TYPES
        summary.residual_duplicates = _residual_duplicates()

        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(_render_report(summary), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(_render_summary(summary)))


def _coerce_dates(attributes: dict[str, Any]) -> dict[str, Any]:
    """Parse ISO date strings from the recorded corpus into ``datetime.date``.

    A provider payload carries dates as ISO strings (as the fixture does); the
    identity scorer needs ``date`` objects.
    """
    for key in ("date_of_birth", "date_of_death"):
        value = attributes.get(key)
        if isinstance(value, str):
            attributes[key] = datetime.date.fromisoformat(value)
    return attributes


def _resolve_teams(attributes: dict[str, Any]) -> dict[str, Any]:
    """Translate ``team_names`` (provider view) onto platform team ids.

    A real provider sends its own team ids; our recorded stand-in sends team
    names, which we resolve onto platform ids by normalised name so the player
    scoring sees shared-team evidence. Unknown names contribute no evidence.
    """
    team_names = attributes.pop("team_names", None)
    if not team_names:
        return attributes
    from apps.teams.models import Team

    team_ids = []
    for name in team_names:
        match = Team.objects.filter(
            is_active=True, deleted_at__isnull=True, name__iexact=name
        ).first()
        if match is not None:
            team_ids.append(str(match.id))
    if team_ids:
        attributes["team_ids"] = team_ids
    return attributes


def _residual_duplicates() -> int:
    """Players in the corpus whose platform entity has a same-name duplicate.

    The continuity suite asserts zero duplicates explicitly; the batch reports
    the residual count (expected 0 in the fixture corpus). This reads the
    post-link state: a duplicate would be a second active Player row for the
    same normalised name + DoB.
    """
    from apps.players.models import Player

    rows = list(
        Player.objects.filter(is_active=True, deleted_at__isnull=True)
        .values("full_name", "date_of_birth")
        .order_by("full_name", "date_of_birth")
    )
    seen: dict[tuple[str, object], int] = {}
    for row in rows:
        key = (row["full_name"], row["date_of_birth"])
        seen[key] = seen.get(key, 0) + 1
    return sum(count - 1 for count in seen.values() if count > 1)


def _render_summary(summary: LinkSummary) -> str:
    mode = "DRY-RUN (nothing written)" if summary.dry_run else "linked"
    return (
        f"identity linking ({mode}): auto={summary.totals.get(OUTCOME_AUTO_LINKED, 0)} "
        f"already={summary.totals.get(OUTCOME_ALREADY_LINKED, 0)} "
        f"pending={summary.totals.get(OUTCOME_CANDIDATE_PENDING, 0)} "
        f"stub={summary.totals.get(OUTCOME_STUB_CREATED, 0)} "
        f"residual-duplicates={summary.residual_duplicates} "
        f"report: {REPORT_PATH}"
    )


def _render_report(summary: LinkSummary) -> str:
    mode = "DRY-RUN (no writes)" if summary.dry_run else "batch link"
    lines = [
        f"# Identity linking coverage — {mode}",
        "",
        f"Date: {datetime.date.today().isoformat()}",
        f"Provider: `{summary.provider_code}`",
        f"Dry run: {summary.dry_run}",
        "",
        "> UNVERIFIED: the corpus is the recorded stand-in at "
        "`tests/fixtures/provider_live/players.yaml`, not a real provider's "
        "view (provider gate `docs/PROVIDER_DECISION.md` not met).",
        "",
        "## Outcomes",
        "",
        "| Outcome | Count |",
        "|---|---|",
        f"| auto_linked | {summary.totals.get(OUTCOME_AUTO_LINKED, 0)} |",
        f"| already_linked | {summary.totals.get(OUTCOME_ALREADY_LINKED, 0)} |",
        f"| candidate_pending | {summary.totals.get(OUTCOME_CANDIDATE_PENDING, 0)} |",
        f"| stub_created | {summary.totals.get(OUTCOME_STUB_CREATED, 0)} |",
        "",
        "## By entity type",
        "",
        "| Type | auto | already | pending | stub |",
        "|---|---|---|---|---|",
    ]
    for entity_type in LINKABLE_ENTITY_TYPES:
        counts = summary.by_type.get(entity_type, {})
        lines.append(
            f"| {entity_type} | {counts.get(OUTCOME_AUTO_LINKED, 0)} | "
            f"{counts.get(OUTCOME_ALREADY_LINKED, 0)} | "
            f"{counts.get(OUTCOME_CANDIDATE_PENDING, 0)} | "
            f"{counts.get(OUTCOME_STUB_CREATED, 0)} |"
        )
    lines += [
        "",
        "## Steward workload estimate",
        "",
        f"- Candidates pending: **{len(summary.candidate_ids)}**",
        f"- Estimated review time: **{summary.steward_workload_minutes(MINUTES_PER_REVIEW)} min** "
        f"({MINUTES_PER_REVIEW} min/review — ASSUMED)",
        "",
        f"- Residual duplicates in corpus: **{summary.residual_duplicates}**",
        "",
    ]
    return "\n".join(lines) + "\n"
