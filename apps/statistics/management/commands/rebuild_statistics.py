"""Backfill the Tier-2 aggregate tables from durable source rows.

Usage::

    python manage.py rebuild_statistics all [--since YYYY-MM-DD] [--dry-run]
    python manage.py rebuild_statistics player --ids <uuid>...
    python manage.py rebuild_statistics team    --ids <uuid>...
    python manage.py rebuild_statistics series  --ids <uuid>...

Before recomputing, any completed match in scope whose ``player_match_stat``
rows are missing is rebuilt through the Phase C per-match builder
(``stats.build_match_stats``), so a backfill after a fresh import produces
both tiers. ``--dry-run`` reports what would be recomputed without writing.
"""

from __future__ import annotations

import datetime
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.statistics.models import PlayerMatchStat
from apps.statistics.services import recompute, stats


class Command(BaseCommand):
    help = "Recompute the Phase F aggregate tables from source (Tier-2 backfill)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("scope", choices=["all", "player", "team", "series"])
        parser.add_argument("--ids", nargs="*", default=None, help="UUIDs to scope by")
        parser.add_argument("--since", default=None, help="Only matches on/after YYYY-MM-DD")
        parser.add_argument("--dry-run", action="store_true", help="Report, do not write")

    def handle(self, *args: Any, **options: Any) -> None:
        scope = options["scope"]
        ids = options["ids"]
        since = options["since"]
        dry_run = options["dry_run"]

        if scope in ("player", "team", "series") and not ids:
            raise CommandError(f"scope '{scope}' requires --ids")
        if scope == "all" and ids:
            raise CommandError("scope 'all' does not take --ids")
        if since:
            try:
                datetime.date.fromisoformat(since)
            except ValueError as exc:
                raise CommandError("--since must be YYYY-MM-DD") from exc

        matches = recompute._matches_in_scope(scope, ids, since)
        built = 0
        for match in matches:
            if (
                not dry_run
                and match.innings.exists()
                and not PlayerMatchStat.objects.filter(match_id=match.id).exists()
            ):
                try:
                    stats.build_match_stats(match, is_partial=stats._is_partial(match))
                    built += 1
                except Exception as exc:
                    self.stderr.write(f"warning: build_match_stats failed for {match.id}: {exc}")

        count = recompute.rebuild_statistics(scope, dry_run=dry_run, ids=ids, since=since)
        verb = "would recompute" if dry_run else "recomputed"
        self.stdout.write(f"({verb} {count} matches; built {built} missing stat rows)")
