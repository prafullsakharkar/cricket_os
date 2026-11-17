"""``manage.py shadow_report [--day YYYY-MM-DD]`` — run the shadow comparison
pass and write the dated report (Phase I Task 2).

Gated on ``PROVIDER_SHADOW_MODE`` (refuses otherwise).
"""

from __future__ import annotations

import datetime
from typing import Any

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Run the shadow comparison pass and write the dated report."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--day", default=None, help="Report day (YYYY-MM-DD); default today.")

    def handle(self, *args: Any, **options: Any) -> None:
        from django.conf import settings

        if not getattr(settings, "PROVIDER_SHADOW_MODE", False):
            raise CommandError(
                "shadow mode is off: set PROVIDER_SHADOW_MODE=true and retry (docs/SHADOW_MODE.md)"
            )
        from apps.shadow.services.driver import run_shadow_pass
        from apps.shadow.services.report import report_dir

        day = None
        if options.get("day"):
            day = datetime.date.fromisoformat(options["day"])
        compared = run_shadow_pass(day=day)
        self.stdout.write(
            self.style.SUCCESS(
                f"shadow pass complete: {compared} matches compared; report under {report_dir()}"
            )
        )
