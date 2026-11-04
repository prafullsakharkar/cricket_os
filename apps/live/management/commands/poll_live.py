"""On-demand live polling: ``python manage.py poll_live``.

Polls every live match once (or a single match with ``--match-id``), exactly
like the Celery beat task ``live.dispatch_polls``. A no-op when
``LIVE_INGESTION_ENABLED`` is off.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Poll every live match once (on-demand; beat runs live.dispatch_polls)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--match-id", default=None, help="Poll a single match by UUID.")

    def handle(self, *args: Any, **options: Any) -> None:
        if not getattr(settings, "LIVE_INGESTION_ENABLED", False):
            self.stdout.write(
                self.style.WARNING("live ingestion is disabled (LIVE_INGESTION_ENABLED)")
            )
            return
        from apps.live.services.polling import dispatch_polls, poll_live_match

        if options.get("match_id"):
            from apps.matches.models import Match

            match = Match.objects.filter(id=options["match_id"]).first()
            if match is None:
                self.stderr.write(f"no match {options['match_id']}")
                return
            reports = [poll_live_match(match, fetcher=lambda m, s: [])]
        else:
            reports = dispatch_polls()

        for report in reports:
            self.stdout.write(
                f"{report.match_id}: polled={report.polled} "
                f"skipped={report.skipped_reason or '-'} "
                f"applied={report.applied} corrected={report.corrected} noop={report.noop}"
            )
