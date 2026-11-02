"""Import one Cricsheet match file through the ingestion pipeline.

``manage.py import_cricsheet --file path/to/123456.yaml``

Reads a **local** Cricsheet YAML file (never a URL) and runs the registered
``cricsheet`` pipeline: parse → validate → resolve → persist (deliveries via
``append_or_revise``) → Tier-1 rebuild, all under one ``ImportJob`` keyed by
``cricsheet:<match_id>:<content_sha256>`` (computed by the adapter, so a
re-import of identical content is a no-op). Permanent failures write a
redacted ``IngestionError`` DLQ row; the job's final status is printed.

The adapter pipeline is registered by the Cricsheet adapter module; until that
module exists the command fails with a clear "no pipeline registered" message.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.ingestion.services.pipeline import PipelineNotRegistered, run_pipeline


class Command(BaseCommand):
    help = "Import one Cricsheet match YAML file through the ingestion pipeline."

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
            raise CommandError(f"Cricsheet file not found: {source}")
        try:
            report = run_pipeline(
                pipeline_name="cricsheet",
                factory_args={"path": str(source), "dry_run": bool(options["dry_run"])},
            )
        except PipelineNotRegistered as exc:
            raise CommandError(
                f"{exc.message} — the Cricsheet adapter pipeline is registered by "
                "its adapter module"
            ) from exc
        self.stdout.write(
            f"job {report.job_type} action={report.action} status={report.status} "
            f"read={report.read} written={report.written} skipped={report.skipped} "
            f"errors={report.errors}"
        )
        if report.error:
            self.stderr.write(f"job failed: {report.error}")
