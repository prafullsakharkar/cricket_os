"""Backfill fantasy points for completed matches (Tier-2).

Usage::

    python manage.py fantasy_backfill all [--since YYYY-MM-DD] [--dry-run]
    python manage.py fantasy_backfill match  --ids <uuid>...
    python manage.py fantasy_backfill player --ids <uuid>...
    python manage.py fantasy_backfill series --ids <uuid>...
    python manage.py fantasy_backfill all --ruleset-version <ruleset-version-uuid>

Resumable: recomputing a match whose latest calc run is already current is a
no-op, so the command is safe to run repeatedly and heals matches that were
skipped or crashed mid-run. ``--dry-run`` reports what would be recomputed
without writing.
"""

from __future__ import annotations

import datetime
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.fantasy.services.recompute import backfill_matches


class Command(BaseCommand):
    help = "Backfill fantasy points from canonical match rows (Tier-2 recompute)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("scope", choices=["all", "match", "player", "series"])
        parser.add_argument("--ids", nargs="*", default=None, help="UUIDs to scope by")
        parser.add_argument("--since", default=None, help="Only matches on/after YYYY-MM-DD")
        parser.add_argument(
            "--ruleset-version", default=None, help="Only recompute one ruleset version"
        )
        parser.add_argument("--dry-run", action="store_true", help="Report, do not write")

    def handle(self, *args: Any, **options: Any) -> None:
        scope = options["scope"]
        ids = options["ids"]
        since = options["since"]
        version = options["ruleset_version"]
        dry_run = options["dry_run"]

        if scope in ("match", "player", "series") and not ids:
            raise CommandError(f"scope '{scope}' requires --ids")
        if scope == "all" and ids:
            raise CommandError("scope 'all' does not take --ids")
        if since:
            try:
                datetime.date.fromisoformat(since)
            except ValueError as exc:
                raise CommandError("--since must be YYYY-MM-DD") from exc

        count = backfill_matches(scope, dry_run=dry_run, ids=ids, since=since, version_id=version)
        verb = "would recompute" if dry_run else "recomputed"
        self.stdout.write(f"({verb} {count} matches)")
