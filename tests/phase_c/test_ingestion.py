"""Ingestion framework: redaction, ImportJob lifecycle, DLQ, pipeline, tasks.

Covers ``ingestion.services`` (redact/jobs/pipeline/register), the
``synchronization`` checkpoint service, the Celery tasks and the
``import_cricsheet`` / ``import_summary_cards`` commands (through registered
fake pipelines — the real adapters land in Groups 5–6):

* DLQ payloads are redacted recursively; normal fields pass through;
* ``ImportJob`` idempotency: created → resumed (crash) → skipped (terminal);
* the pipeline is atomic, publishes after commit, advances the checkpoint only
  after commit, writes a DLQ row + ``failed`` job on ``PipelineError``, marks
  ``partial`` when a stage reports errors, and lets transient exceptions roll
  back;
* ``players.import_register`` runs through the pipeline (ImportJob + DLQ);
* Celery tasks retry transient failures with bounded exponential backoff;
* the commands dispatch to registered pipelines and fail cleanly otherwise.
"""

from __future__ import annotations

from collections.abc import Callable
from io import StringIO
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core import events as core_events
from apps.ingestion.models import ImportJob, IngestionError
from apps.ingestion.services.jobs import (
    create_or_resume_job,
    mark_succeeded,
    open_errors,
    record_error,
)
from apps.ingestion.services.pipeline import (
    IngestContext,
    PipelineError,
    PipelineNotRegistered,
    PipelineReport,
    PipelineSpec,
    StageResult,
    get_pipeline,
    list_pipelines,
    register_pipeline,
    run_pipeline,
)
from apps.ingestion.services.redact import is_sensitive_key, redact
from apps.ingestion.services.register import run_register_import
from apps.ingestion.tasks import MAX_RETRIES, backoff_seconds, ingest_job
from apps.players.models import Player
from apps.providers.models import ProviderEntityMap
from apps.synchronization.models import SyncCheckpoint
from apps.synchronization.services.sync import advance_checkpoint, get_checkpoint, resume_cursor

from .factories import make_provider

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------- #
# Redaction
# --------------------------------------------------------------------------- #


class TestRedact:
    def test_sensitive_keys_are_redacted_recursively(self) -> None:
        payload = {
            "match_id": "m-1",
            "request": {"headers": {"Authorization": "Bearer xyz", "Accept": "json"}},
            "credentials": {"api_key": "k-123", "client_secret": "s-456"},
            "player_ids": ["a", "b"],
        }
        out = redact(payload)
        assert out["match_id"] == "m-1"
        # Sensitive keys are replaced wholesale (including their nested value).
        assert out["request"]["headers"]["Authorization"] == "[REDACTED]"
        assert out["request"]["headers"]["Accept"] == "json"
        assert out["credentials"] == "[REDACTED]"
        assert out["player_ids"] == ["a", "b"]

    def test_sensitive_keys_are_matched_case_and_separator_insensitively(self) -> None:
        assert is_sensitive_key("API_KEY") is True
        assert is_sensitive_key("client-secret") is True
        assert is_sensitive_key("access_token") is True
        assert is_sensitive_key("password") is True
        assert is_sensitive_key("full_name") is False
        assert is_sensitive_key("match_id") is False

    def test_scalars_and_none_pass_through(self) -> None:
        assert redact(None) is None
        assert redact("hello") == "hello"
        assert redact(42) == 42
        assert redact(["a", {"token": "t"}]) == ["a", {"token": "[REDACTED]"}]


# --------------------------------------------------------------------------- #
# ImportJob lifecycle + DLQ
# --------------------------------------------------------------------------- #


class TestJobs:
    def test_create_or_resume_creates_running_job(self) -> None:
        provider = make_provider()
        job, action = create_or_resume_job(
            provider, job_type="test", scope={"a": 1}, idempotency_key="key-1"
        )
        assert action == "created"
        assert job.status == "running"
        assert job.started_at is not None
        assert job.provider == provider

    def test_terminal_job_with_same_key_is_skipped(self) -> None:
        provider = make_provider()
        job, _ = create_or_resume_job(provider, job_type="test", scope={}, idempotency_key="key-2")
        mark_succeeded(job, read=1, written=2, skipped=0)
        job.refresh_from_db()

        again, action = create_or_resume_job(
            provider, job_type="test", scope={}, idempotency_key="key-2"
        )
        assert action == "skipped"
        assert again.id == job.id
        assert again.status == "succeeded", "skipped re-run must not clobber the terminal status"

    def test_crashed_running_job_is_resumed(self) -> None:
        provider = make_provider()
        job, _ = create_or_resume_job(provider, job_type="test", scope={}, idempotency_key="key-3")
        job.status = "running"
        job.started_at = timezone.now() - timezone.timedelta(hours=2)
        job.save(update_fields=["status", "started_at"])

        again, action = create_or_resume_job(
            provider, job_type="test", scope={}, idempotency_key="key-3"
        )
        assert action == "resumed"
        assert again.id == job.id
        assert again.status == "running"

    def test_duplicate_create_folds_onto_the_winner(self) -> None:
        provider = make_provider()
        first, _ = create_or_resume_job(
            provider, job_type="test", scope={}, idempotency_key="key-4"
        )
        # Simulate the losing side of a concurrent create race (savepoint so the
        # aborted INSERT does not poison the surrounding test transaction).
        with pytest.raises(IntegrityError), transaction.atomic():
            ImportJob.objects.create(
                provider=provider,
                job_type="test",
                scope={},
                idempotency_key="key-4",
                status="running",
            )
        again, action = create_or_resume_job(
            provider, job_type="test", scope={}, idempotency_key="key-4"
        )
        assert action == "resumed"
        assert again.id == first.id

    def test_mark_succeeded_records_counters(self) -> None:
        provider = make_provider()
        job, _ = create_or_resume_job(provider, job_type="test", scope={}, idempotency_key="key-5")
        mark_succeeded(job, read=10, written=7, skipped=3)
        job.refresh_from_db()
        assert (job.status, job.records_read, job.records_written, job.records_skipped) == (
            "succeeded",
            10,
            7,
            3,
        )
        assert job.finished_at is not None

    def test_record_error_redacts_payload_and_stamps_attempt(self) -> None:
        provider = make_provider()
        job, _ = create_or_resume_job(provider, job_type="test", scope={}, idempotency_key="key-6")
        row = record_error(
            job,
            stage="validate",
            payload={"row": 4, "auth": {"token": "secret"}},
            error_class="DeliveryValidationError",
            error_detail="runs out of range",
            attempt=2,
        )
        assert row.import_job == job
        assert row.provider_id == provider.id
        assert row.stage == "validate"
        assert row.payload == {"row": 4, "auth": "[REDACTED]"}
        assert row.error_class == "DeliveryValidationError"
        assert row.attempt == 2
        assert row.resolved is False

    def test_open_errors_returns_only_unresolved(self) -> None:
        job = None
        first = record_error(job, stage="a", error_class="E1")
        record_error(job, stage="b", error_class="E2")
        first.resolved = True
        first.save(update_fields=["resolved"])
        assert [e.error_class for e in open_errors()] == ["E2"]


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _clean_pipelines() -> object:
    from apps.ingestion.services import pipeline as pipeline_mod

    saved = dict(pipeline_mod._registry)
    pipeline_mod._registry.clear()
    # transaction=True tests flush the migration-seeded ``cricsheet`` provider
    # row, so ensure it exists for every pipeline/task/command test.
    make_provider()
    yield
    pipeline_mod._registry.clear()
    pipeline_mod._registry.update(saved)


@pytest.fixture(autouse=True)
def _clear_events() -> object:
    core_events.clear_subscriptions()
    yield
    core_events.clear_subscriptions()


def _fake_spec(
    factory_args: dict[str, Any], *, stage: Callable[[IngestContext], StageResult]
) -> PipelineSpec:
    path = factory_args["path"]
    return PipelineSpec(
        provider_code="cricsheet",
        job_type="fake",
        idempotency_key=f"fake:{path}",
        scope={"path": path},
        stages=[stage],
        checkpoint_scope="fake:cricsheet",
    )


def _ok_stage(ctx: IngestContext) -> StageResult:
    return StageResult(
        read=5, written=3, skipped=1, checkpoint_cursor="ball-8", last_delivery_seq=8
    )


class TestPipeline:
    def test_registry_and_missing_pipeline(self) -> None:
        assert list_pipelines() == []
        with pytest.raises(PipelineNotRegistered):
            get_pipeline("nope")
        register_pipeline("fake", lambda args: _fake_spec(args, stage=_ok_stage))
        assert get_pipeline("fake") is not None
        assert list_pipelines() == ["fake"]

    @pytest.mark.django_db(transaction=True)
    def test_success_marks_succeeded_and_publishes_completed(self) -> None:
        completed: list[dict[str, Any]] = []
        core_events.subscribe(core_events.TOPIC_INGESTION_COMPLETED, lambda p: completed.append(p))
        register_pipeline("fake", lambda args: _fake_spec(args, stage=_ok_stage))

        report = run_pipeline(pipeline_name="fake", factory_args={"path": "/tmp/x.yaml"})

        assert report.action == "created"
        assert report.status == "succeeded"
        assert (report.read, report.written, report.skipped) == (5, 3, 1)
        job = ImportJob.objects.get(id=report.job_id)
        assert job.status == "succeeded"
        assert job.records_read == 5
        assert len(completed) == 1
        assert completed[0]["status"] == "succeeded"

    @pytest.mark.django_db(transaction=True)
    def test_idempotent_rerun_is_skipped_and_stages_not_rerun(self) -> None:
        calls: list[int] = []

        def counting_stage(ctx: IngestContext) -> StageResult:
            calls.append(1)
            return StageResult(read=1)

        register_pipeline("fake", lambda args: _fake_spec(args, stage=counting_stage))
        first = run_pipeline(pipeline_name="fake", factory_args={"path": "/tmp/x.yaml"})
        assert first.status == "succeeded"

        second = run_pipeline(pipeline_name="fake", factory_args={"path": "/tmp/x.yaml"})

        assert second.action == "skipped"
        assert second.status == "succeeded"
        assert len(calls) == 1, "a terminal job must not re-run its stages"

    @pytest.mark.django_db(transaction=True)
    def test_crash_resume_reruns_stages(self) -> None:
        calls: list[int] = []

        def counting_stage(ctx: IngestContext) -> StageResult:
            calls.append(1)
            return StageResult(read=1)

        # A previous run committed the job row then crashed before finishing.
        provider = make_provider()
        ImportJob.objects.create(
            provider=provider,
            job_type="fake",
            scope={"path": "/tmp/x.yaml"},
            idempotency_key="fake:/tmp/x.yaml",
            status="running",
            started_at=timezone.now() - timezone.timedelta(hours=1),
        )
        register_pipeline("fake", lambda args: _fake_spec(args, stage=counting_stage))
        report = run_pipeline(pipeline_name="fake", factory_args={"path": "/tmp/x.yaml"})

        assert report.action == "resumed"
        assert report.status == "succeeded"
        assert len(calls) == 1

    @pytest.mark.django_db(transaction=True)
    def test_permanent_failure_writes_dlq_and_marks_failed(self) -> None:
        failed: list[dict[str, Any]] = []
        core_events.subscribe(core_events.TOPIC_INGESTION_FAILED, lambda p: failed.append(p))

        def bad_stage(ctx: IngestContext) -> StageResult:
            raise PipelineError(
                "invalid enum value",
                stage="validate",
                payload={"row": 2, "token": "sensitive"},
                error_class="DeliveryValidationError",
            )

        register_pipeline("fake", lambda args: _fake_spec(args, stage=bad_stage))
        report = run_pipeline(pipeline_name="fake", factory_args={"path": "/tmp/x.yaml"})

        assert report.status == "failed"
        assert report.error_class == "DeliveryValidationError"
        job = ImportJob.objects.get(id=report.job_id)
        assert job.status == "failed"
        (dlq,) = IngestionError.objects.filter(import_job=job)
        assert dlq.stage == "validate"
        assert dlq.error_class == "DeliveryValidationError"
        assert dlq.payload == {"row": 2, "token": "[REDACTED]"}
        assert len(failed) == 1

    @pytest.mark.django_db(transaction=True)
    def test_transient_exception_rolls_back_everything(self) -> None:
        def flaky_stage(ctx: IngestContext) -> StageResult:
            raise ConnectionError("broker hiccup")

        register_pipeline("fake", lambda args: _fake_spec(args, stage=flaky_stage))
        with pytest.raises(ConnectionError):
            run_pipeline(pipeline_name="fake", factory_args={"path": "/tmp/x.yaml"})

        assert ImportJob.objects.count() == 0
        assert IngestionError.objects.count() == 0

    @pytest.mark.django_db(transaction=True)
    def test_partial_status_when_stage_reports_errors(self) -> None:
        def erroring_stage(ctx: IngestContext) -> StageResult:
            return StageResult(read=4, written=2, skipped=0, errors=2)

        register_pipeline("fake", lambda args: _fake_spec(args, stage=erroring_stage))
        report = run_pipeline(pipeline_name="fake", factory_args={"path": "/tmp/x.yaml"})

        assert report.status == "partial"
        assert report.errors == 2
        job = ImportJob.objects.get(id=report.job_id)
        assert job.status == "partial"

    @pytest.mark.django_db(transaction=True)
    def test_checkpoint_advanced_only_after_commit(self) -> None:
        provider = make_provider()
        register_pipeline("fake", lambda args: _fake_spec(args, stage=_ok_stage))
        run_pipeline(pipeline_name="fake", factory_args={"path": "/tmp/x.yaml"})

        checkpoint = get_checkpoint(provider, "fake:cricsheet")
        assert checkpoint is not None
        assert checkpoint.last_delivery_seq == 8
        assert checkpoint.cursor == "ball-8"

    @pytest.mark.django_db(transaction=True)
    def test_no_checkpoint_without_checkpoint_scope(self) -> None:
        provider = make_provider()
        register_pipeline(
            "fake",
            lambda args: PipelineSpec(
                provider_code="cricsheet",
                job_type="fake",
                idempotency_key=f"fake:{args['path']}",
                scope={},
                stages=[_ok_stage],
            ),
        )
        run_pipeline(pipeline_name="fake", factory_args={"path": "/tmp/x.yaml"})
        assert SyncCheckpoint.objects.filter(provider=provider).count() == 0


# --------------------------------------------------------------------------- #
# Register importer wrap
# --------------------------------------------------------------------------- #


class TestRegisterImport:
    def test_people_import_round_trip(self) -> None:
        provider = make_provider()
        report = run_register_import(
            [
                {"identifier": "p-0001", "name": "Virat Kohli", "key_opta": "opt-1"},
                {"identifier": "p-0002", "name": "Rohit Sharma"},
            ],
            kind="people",
            idempotency_key="register:people:abc",
        )

        assert report.status == "succeeded"
        assert report.written == 2
        assert report.read == 2
        assert Player.objects.count() == 2
        # One primary map row per player + the allowlisted key_opta row.
        assert (
            ProviderEntityMap.objects.filter(provider=provider, entity_type="player").count() == 3
        )
        job = ImportJob.objects.get(id=report.job_id)
        assert job.status == "succeeded"
        assert job.job_type == "register:people"

    def test_bad_rows_go_to_dlq_and_job_is_partial(self) -> None:
        report = run_register_import(
            [
                {"identifier": "ok", "name": "Good"},
                {"identifier": "", "name": "Missing id"},
                {"name": "Missing identifier"},
            ],
            kind="people",
            idempotency_key="register:people:bad",
        )

        assert report.status == "partial"
        assert report.errors == 2
        dlq_rows = IngestionError.objects.filter(import_job_id=report.job_id)
        assert dlq_rows.count() == 2
        assert {r.error_class for r in dlq_rows} == {"RegisterRowError"}
        assert all(r.stage == "validate" for r in dlq_rows)

    def test_idempotent_rerun_is_skipped(self) -> None:
        run_register_import(
            [{"identifier": "p-0001", "name": "Virat Kohli"}],
            kind="people",
            idempotency_key="register:people:same",
        )
        report = run_register_import(
            [{"identifier": "p-0001", "name": "Virat Kohli"}],
            kind="people",
            idempotency_key="register:people:same",
        )
        assert report.action == "skipped"
        assert Player.objects.count() == 1


# --------------------------------------------------------------------------- #
# Checkpoint service (synchronization)
# --------------------------------------------------------------------------- #


class TestCheckpoints:
    def test_advance_get_resume_cursor(self) -> None:
        provider = make_provider()
        assert get_checkpoint(provider, "deliveries") is None
        assert resume_cursor(provider, "deliveries") is None

        advance_checkpoint(provider, "deliveries", last_delivery_seq=120, cursor="c-120")
        checkpoint = get_checkpoint(provider, "deliveries")
        assert checkpoint is not None
        assert checkpoint.last_delivery_seq == 120
        assert checkpoint.cursor == "c-120"
        assert resume_cursor(provider, "deliveries") == "c-120"

    def test_advance_updates_in_place(self) -> None:
        provider = make_provider()
        advance_checkpoint(provider, "deliveries", last_delivery_seq=10)
        advance_checkpoint(provider, "deliveries", last_delivery_seq=25, cursor="c-25")
        assert SyncCheckpoint.objects.filter(provider=provider, scope_key="deliveries").count() == 1
        assert get_checkpoint(provider, "deliveries").last_delivery_seq == 25  # type: ignore[union-attr]


# --------------------------------------------------------------------------- #
# Celery tasks
# --------------------------------------------------------------------------- #


class TestTasks:
    def test_task_metadata(self) -> None:
        assert ingest_job.name == "ingestion.ingest_job"
        assert ingest_job.max_retries == MAX_RETRIES == 8

    def test_backoff_grows_exponentially_with_jitter(self) -> None:
        assert backoff_seconds(0) >= 5 and backoff_seconds(0) < 10
        assert backoff_seconds(1) >= 10 and backoff_seconds(1) < 15
        assert backoff_seconds(3) >= 40 and backoff_seconds(3) < 45

    def test_eager_success_returns_report(self) -> None:
        register_pipeline("fake", lambda args: _fake_spec(args, stage=_ok_stage))
        result = ingest_job.apply(
            kwargs={"pipeline_name": "fake", "factory_args": {"path": "/tmp/x.yaml"}}
        )
        report = result.get()
        assert isinstance(report, PipelineReport)
        assert report.status == "succeeded"

    def test_transient_failure_retries_with_backoff(self, monkeypatch: Any) -> None:
        retried: dict[str, Any] = {}

        # An instance-attribute replacement is not bound, so no ``self``.
        def fake_retry(*, exc: Any, countdown: int) -> Any:
            retried["exc"] = exc
            retried["countdown"] = countdown
            raise RuntimeError("retry raised")

        monkeypatch.setattr(ingest_job, "retry", fake_retry)

        def flaky_stage(ctx: IngestContext) -> StageResult:
            raise ConnectionError("broker hiccup")

        register_pipeline("fake", lambda args: _fake_spec(args, stage=flaky_stage))
        with pytest.raises(RuntimeError):
            ingest_job.apply(
                kwargs={"pipeline_name": "fake", "factory_args": {"path": "/tmp/x.yaml"}}
            )
        assert isinstance(retried["exc"], ConnectionError)
        # First attempt uses retries=0: base * 2**0 + jitter in [0, base).
        assert 5 <= retried["countdown"] < 10


# --------------------------------------------------------------------------- #
# Management commands
# --------------------------------------------------------------------------- #


class TestCommands:
    def _write(self, tmp_path: Any, content: str = "fake") -> str:
        path = tmp_path / "input.yaml"
        path.write_text(content, encoding="utf-8")
        return str(path)

    @pytest.mark.django_db(transaction=True)
    def test_import_cricsheet_missing_file(self, tmp_path: Any) -> None:
        with pytest.raises(CommandError):
            call_command("import_cricsheet", path=str(tmp_path / "nope.yaml"))

    def test_import_cricsheet_unregistered_pipeline(self, tmp_path: Any) -> None:
        with pytest.raises(CommandError, match="No pipeline registered"):
            call_command("import_cricsheet", path=self._write(tmp_path))

    @pytest.mark.django_db(transaction=True)
    def test_import_cricsheet_runs_registered_pipeline(self, tmp_path: Any) -> None:
        register_pipeline("cricsheet", lambda args: _fake_spec(args, stage=_ok_stage))
        path = self._write(tmp_path)
        out = StringIO()
        call_command("import_cricsheet", path=path, stdout=out)
        assert "status=succeeded" in out.getvalue()
        assert ImportJob.objects.filter(idempotency_key=f"fake:{path}").exists()

    @pytest.mark.django_db(transaction=True)
    def test_import_summary_cards_runs_registered_pipeline(self, tmp_path: Any) -> None:
        register_pipeline("summary_cards", lambda args: _fake_spec(args, stage=_ok_stage))
        out = StringIO()
        call_command("import_summary_cards", path=self._write(tmp_path), stdout=out)
        assert "status=succeeded" in out.getvalue()

    def test_import_summary_cards_missing_file(self, tmp_path: Any) -> None:
        with pytest.raises(CommandError):
            call_command("import_summary_cards", path=str(tmp_path / "nope.json"))
