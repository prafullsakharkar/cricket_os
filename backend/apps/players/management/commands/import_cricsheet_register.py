"""Import Cricsheet reference-data registers from local CSV files.

``manage.py import_cricsheet_register --people people.csv [--names names.csv]
[--venues venues.csv] [--dry-run]``

- Local files only (no URLs); headers are read from the file itself and any
  header order is accepted. ``identifier`` + ``name`` are required; unknown
  columns are ignored.
- Each people/venue row imports the Cricsheet ``identifier`` as the primary
  provider-map key plus the ``key_*`` columns listed in
  ``settings.CRICSHEET_KEY_ALLOWLIST`` (default ``["key_opta", "key_pulse"]``)
  as extra map rows. ``key_cricbuzz`` / ``key_cricinfo`` are never imported
  unless the allowlist enables them (pending the Phase 0 licensing review).
- Venues come from ``--venues`` when given, else the curated seed CSV
  (``apps/venues/seed/curated_venues.csv``), which also seeds a Ground per
  venue.
- ``--dry-run`` reads and validates only; nothing is written (no errors file).
- A run never crashes on a bad row: bad rows are counted, reported and written
  to ``<input>.errors.csv``. Re-running is idempotent (map rows are unique by
  (provider, entity_type, identifier)).

Real ``ImportJob`` / DLQ tables belong to Phase C ``ingestion``, which will
wrap the pure :func:`apps.players.import_register.import_register` core this
command drives.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.players.import_register import ImportReport, Kind, RegisterSink, import_register
from apps.players.sinks import DjangoRegisterSink

#: Curated venue seed used when no --venues file is given (module docstring).
CURATED_VENUES_CSV = Path(__file__).resolve().parents[3] / "venues" / "seed" / "curated_venues.csv"

#: Column names the curated venue CSV carries (name,city,country_iso,timezone).
CURATED_HEADERS = ("name", "city", "country_iso", "timezone")


def _read_rows(path: Path) -> list[dict[str, str]]:
    """Read a register CSV, skipping blank lines and ``#`` comments.

    Comments are stripped BEFORE ``csv.DictReader`` sees the file so a leading
    comment block cannot become the header row.
    """
    with path.open(newline="", encoding="utf-8") as handle:
        lines = [line for line in handle if line.strip() and not line.lstrip().startswith("#")]
        reader = csv.DictReader(lines)
        return [
            {k: (v or "").strip() for k, v in raw.items()}
            for raw in reader
            if any((v or "").strip() for v in raw.values())
        ]


class Command(BaseCommand):
    help = "Import Cricsheet people/names/venues registers from local CSV files."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--people", dest="people", metavar="PATH")
        parser.add_argument("--names", dest="names", metavar="PATH")
        parser.add_argument("--venues", dest="venues", metavar="PATH")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="read + validate only; write nothing",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        people = options.get("people")
        names = options.get("names")
        venues = options.get("venues")
        dry_run: bool = options["dry_run"]
        if not (people or names or venues):
            raise CommandError("provide at least one of --people/--names/--venues")

        sink = None if dry_run else DjangoRegisterSink()
        # Venues fall back to the curated seed CSV when --venues is omitted.
        sources: list[tuple[Kind, Path]] = []
        if people:
            sources.append(("people", Path(people)))
        if names:
            sources.append(("names", Path(names)))
        sources.append(("venues", Path(venues) if venues else CURATED_VENUES_CSV))

        for kind, source in sources:
            if not source.is_file():
                raise CommandError(f"{kind} file not found: {source}")
            report = self._import_kind(kind, source, sink)
            self._summary(kind, source, report)
            if not dry_run and report.errors:
                self._write_errors(source, report)

    def _summary(self, kind: str, path: Path, report: ImportReport) -> None:
        self.stdout.write(
            f"{kind:<8} {path.name}: read={report.read} created={report.created} "
            f"updated={report.updated} skipped={report.skipped} "
            f"errors={report.failed}"
        )

    # -- per-kind import ------------------------------------------------------
    def _import_kind(
        self,
        kind: Kind,
        source: Path,
        sink: DjangoRegisterSink | None,
    ) -> ImportReport:
        if kind == "venues" and source == CURATED_VENUES_CSV:
            return self._import_curated_venues(source, sink)
        rows = _read_rows(source)
        if sink is None:
            # dry-run: validate rows without touching the DB.
            return import_register(rows, _DryRunSink(), kind=kind)
        with transaction.atomic():
            return import_register(rows, sink, kind=kind)

    def _import_curated_venues(
        self,
        source: Path,
        sink: DjangoRegisterSink | None,
    ) -> ImportReport:
        """The curated seed CSV: no identifiers, so handle rows directly."""
        rows = _read_rows(source)
        report = ImportReport()
        for number, row in enumerate(rows, start=1):
            report.read += 1
            missing = [c for c in CURATED_HEADERS if not row.get(c)]
            if missing:
                report.errors.append({"row": number, "reason": f"missing {', '.join(missing)}"})
                continue
            try:
                if sink is None:
                    status = _validate_curated_row(row)
                else:
                    status = sink.curated_venue(
                        row["name"],
                        row["city"],
                        row["country_iso"],
                        row["timezone"],
                    )
            except ValueError as exc:
                report.errors.append({"row": number, "reason": str(exc)})
                continue
            if status == "created":
                report.created += 1
            elif status == "updated":
                report.updated += 1
            else:
                report.skipped += 1
        return report

    def _write_errors(self, source: Path, report: ImportReport) -> None:
        errors_path = Path(str(source) + ".errors.csv")
        with errors_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["row", "reason"])
            writer.writeheader()
            writer.writerows(report.errors)
        self.stderr.write(f"wrote {len(report.errors)} bad-row entries to {errors_path}")


class _DryRunSink(RegisterSink):
    """Validates rows without touching the DB (dry-run mode).

    ``player``/``venue`` accept anything the pure core already validated
    (identifier + name present). ``player_name_variant`` cannot check whether
    the identifier exists without the DB, so dry-run counts it as skipped; the
    real run surfaces unknown identifiers as errors.
    """

    def player(
        self,
        identifier: str,
        name: str,
        unique_name: str | None,
        keys: Any,
    ) -> str:
        return "created"

    def player_name_variant(self, identifier: str, name: str) -> str:
        return "skipped"

    def venue(
        self,
        identifier: str,
        name: str,
        unique_name: str | None,
        keys: Any,
    ) -> str:
        return "created"


def _validate_curated_row(row: dict[str, str]) -> str:
    """Dry-run validation for a curated venue row (no writes)."""
    from apps.venues.models import Country, validate_iana_timezone

    if not Country.objects.filter(iso_code=row["country_iso"]).exists():
        raise ValueError(f"unknown country iso: {row['country_iso']}")
    validate_iana_timezone(row["timezone"])
    return "created"
