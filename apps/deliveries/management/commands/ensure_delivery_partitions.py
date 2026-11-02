"""Create delivery partitions for a range of years on demand.

``manage.py ensure_delivery_partitions --from 2020 --to 2030`` creates the
yearly ``delivery_<year>`` partition for every year in ``[--from, --to]``
(inclusive), skipping any that already exist. The write path also creates
partitions lazily via ``deliveries.services.partitions.ensure_partition``, so
this command is only needed to warm a range ahead of a bulk historical import.

Pre-creating next year's partition on a schedule, and archival of old
partitions, are Phase G tickets (see CRICKET_IMPLEMENTATION_ROADMAP.md).
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from apps.deliveries.services.partitions import ensure_partition


class Command(BaseCommand):
    help = "Create yearly RANGE partitions of cricket.delivery for a year range."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--from", dest="from_year", type=int, required=True)
        parser.add_argument("--to", dest="to_year", type=int, required=True)

    def handle(self, *args: Any, **options: Any) -> None:
        from_year = int(options["from_year"])
        to_year = int(options["to_year"])
        if from_year > to_year:
            raise ValueError("--from must be <= --to")
        created = [y for y in range(from_year, to_year + 1) if ensure_partition(y)]
        self.stdout.write(
            f"delivery partitions ensured for {from_year}..{to_year} "
            f"({len(created)} created)"
        )
