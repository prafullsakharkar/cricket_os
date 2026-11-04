"""On-demand fixture sync: ``python manage.py sync_fixtures``.

Folds the upcoming fixture window for active providers into ``matches``
(idempotent). Mirrors the beat task ``live.sync_fixtures``; a no-op when
``LIVE_INGESTION_ENABLED`` is off.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Sync the upcoming fixture window for active providers (idempotent)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--provider-id", default=None, help="Sync one provider by UUID.")
        parser.add_argument("--days", type=int, default=7, help="Window size ahead (days).")

    def handle(self, *args: Any, **options: Any) -> None:
        if not getattr(settings, "LIVE_INGESTION_ENABLED", False):
            self.stdout.write(
                self.style.WARNING("live ingestion is disabled (LIVE_INGESTION_ENABLED)")
            )
            return
        from apps.live.services.fixture_sync import run_fixture_sync
        from apps.providers.models import DataProvider

        providers = DataProvider.objects.filter(is_active=True)
        if options.get("provider_id"):
            providers = providers.filter(id=options["provider_id"])
        for provider in providers:
            report = run_fixture_sync(provider, days=options["days"])
            self.stdout.write(
                f"{provider.code}: created={report.created} updated={report.updated} "
                f"rescheduled={report.rescheduled} cancelled={report.cancelled} "
                f"unchanged={report.unchanged} skipped={report.skipped} errors={report.errors}"
            )
