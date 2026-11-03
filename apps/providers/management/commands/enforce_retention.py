"""Retention: ``python manage.py enforce_retention [--apply] [--provider CODE]``.

Dry-run by default: prints the candidates each provider's retention window
would delete. Pass ``--apply`` to actually delete the ball data (a licence
requirement, RSK-04). Summaries/cards are always kept.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Enforce per-provider ball-data retention limits (dry-run default)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--provider", default=None, help="Restrict to one provider code.")
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually delete ball data (default: dry-run only).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from apps.providers.licensing import enforce_retention

        reports = enforce_retention(options.get("provider"), dry_run=not options.get("apply"))
        for report in reports:
            action = (
                "delete" if report.deleted else ("would delete" if report.dry_run else "deleted")
            )
            self.stdout.write(
                f"{report.provider_code}: retention_days={report.retention_days} "
                f"candidates={report.candidates} {action}={report.deleted}"
            )
