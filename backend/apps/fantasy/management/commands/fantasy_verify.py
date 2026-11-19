"""Verify the fantasy calc trail (append-only audit + ledger).

For every completed match in scope, each calc-audit row must reproduce its own
output digest from its stored snapshot, its stored snapshot digest must match,
and (for non-provisional runs) the current ledger rows must sum to the run's
per-player totals. A version whose content hash changed since a run is also
reported (impossible for frozen versions). Exits nonzero (``CommandError``)
when any finding is recorded.

Usage::

    python manage.py fantasy_verify all
    python manage.py fantasy_verify match  --ids <uuid>...
    python manage.py fantasy_verify series --ids <uuid>...
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.fantasy.services.recompute import verify_fantasy


class Command(BaseCommand):
    help = "Verify the fantasy calc audit trail reproduces its stored results."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("scope", choices=["all", "match", "player", "series"])
        parser.add_argument("--ids", nargs="*", default=None, help="UUIDs to scope by")
        parser.add_argument("--since", default=None, help="Only matches on/after YYYY-MM-DD")

    def handle(self, *args: Any, **options: Any) -> None:
        findings = verify_fantasy(options["scope"], ids=options["ids"])
        if findings:
            raise CommandError(
                f"fantasy verification FAILED ({len(findings)} finding(s)):\n" + "\n".join(findings)
            )
        self.stdout.write("fantasy verification OK")
