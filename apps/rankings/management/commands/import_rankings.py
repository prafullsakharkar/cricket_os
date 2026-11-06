"""Import provider rankings for one gender (idempotent).

Usage::

    python manage.py import_rankings --provider fixture [--gender M] [--as-of YYYY-MM-DD]

The pipeline resolves provider player ids through ``provider_entity_map``,
upserts by the schema's ``ux_ranking`` key (re-runs update in place), derives
trend from the previous snapshot and best-rank/best-rating from history, and
reports unresolved provider players to the DLQ (when the ``ingestion`` app is
installed and its default reporters are registered). ``--as-of`` replays the
corpus only up to a date (backfill); without it every recorded row is imported.
"""

from __future__ import annotations

import datetime
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.providers.models import DataProvider
from apps.rankings.services.import_rankings import import_rankings


class Command(BaseCommand):
    help = "Import one provider's recorded rankings for a gender (idempotent)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--provider", default="fixture", help="Provider code")
        parser.add_argument("--gender", default="M", help="Gender code (M/F)")
        parser.add_argument("--as-of", default=None, help="Only rows on/before YYYY-MM-DD")

    def handle(self, *args: Any, **options: Any) -> None:
        code = options["provider"]
        gender = options["gender"].upper()
        if gender not in ("M", "F"):
            raise CommandError("--gender must be M or F")
        as_of = None
        if options["as_of"]:
            try:
                as_of = datetime.date.fromisoformat(options["as_of"])
            except ValueError as exc:
                raise CommandError("--as-of must be YYYY-MM-DD") from exc

        provider = DataProvider.objects.filter(code=code).first()
        if provider is None:
            raise CommandError(f"no provider with code {code!r}")

        summary = import_rankings(provider, gender=gender, as_of=as_of)
        self.stdout.write(
            "rankings: total={total} imported={imported} updated={updated} "
            "skipped_unresolved={skipped_unresolved}".format(**summary)
        )
