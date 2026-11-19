"""Import a summary-only scorecard through the ingestion pipeline.

``manage.py import_summary_cards --file path/to/summary.json``

Runs the registered ``summary_cards`` pipeline for a match whose innings carry
**no ball-by-ball data** (provider summaries only): the adapter writes the
provider's final figures, marks every ``innings_summary`` row
``batch_complete=false``, and the engine rebuild is skipped for those innings
(they are summary-only). The job is keyed by ``summary:<match_id>:<content_sha256>``
so identical re-imports are no-ops.

The adapter pipeline is registered by the summary-only adapter module; until
then the command fails with a clear "no pipeline registered" message.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.ingestion.services.pipeline import PipelineNotRegistered, run_pipeline


class Command(BaseCommand):
    help = "Import one summary-only scorecard file through the ingestion pipeline."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--file", dest="path", metavar="PATH", required=True)
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="parse + validate only; write nothing (delegated to the adapter)",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        source = Path(options["path"])
        if not source.is_file():
            raise CommandError(f"summary scorecard file not found: {source}")
        try:
            report = run_pipeline(
                pipeline_name="summary_cards",
                factory_args={"path": str(source), "dry_run": bool(options["dry_run"])},
            )
        except PipelineNotRegistered as exc:
            raise CommandError(
                f"{exc.message} — the summary-only adapter pipeline is registered "
                "by its adapter module"
            ) from exc
        self.stdout.write(
            f"job {report.job_type} action={report.action} status={report.status} "
            f"read={report.read} written={report.written} skipped={report.skipped} "
            f"errors={report.errors}"
        )
        if report.error:
            self.stderr.write(f"job failed: {report.error}")
