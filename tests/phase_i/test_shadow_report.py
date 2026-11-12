"""Phase I Task 2: dated shadow report writer + driver pass.

* a dated report is written to a tmp dir (``SHADOW_REPORT_DIR`` override) with
  the correct filename, the environment banner, and JSON that round-trips;
* the full driver pass (``run_shadow_pass``) works against a replay-ingested
  completed match: metrics recorded, findings queued via the registered
  reporter (DLQ row), report written.
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from django.test import override_settings

from apps.shadow.compare import ComparisonReport, compare_match, register_shadow_comparison_reporter
from apps.shadow.services.report import _ENV_BANNER, report_dir, write_report

pytestmark = pytest.mark.django_db


def _finished_t20() -> object:
    from tests.phase_d.test_live_apply import _finish_t20, make_context

    ctx = make_context()
    _finish_t20(ctx)
    return ctx["match"]


def _report(tmp_path, day: date = date(2026, 11, 13)) -> tuple[object, list[ComparisonReport]]:
    match = _finished_t20()
    report = compare_match(match)
    with override_settings(SHADOW_REPORT_DIR=str(tmp_path)):
        md_path = write_report(day, [report], latency={"p50": 0.001, "p90": 0.002, "p99": 0.003})
        return md_path, [report]


def test_report_dir_respects_override(tmp_path) -> None:
    with override_settings(SHADOW_REPORT_DIR=str(tmp_path)):
        assert report_dir() == tmp_path


def test_clean_corpus_report_has_banner_and_zero_findings(tmp_path) -> None:
    md_path, _reports = _report(tmp_path)
    text = md_path.read_text(encoding="utf-8")
    assert "2026-11-13" in md_path.name
    assert _ENV_BANNER in text
    assert "No mismatches (replay corpus is internally consistent)." in text
    assert "Matches compared: **1**" in text
    assert "UNVERIFIED" in text  # the environment gate is named, never hidden


def test_report_json_round_trips(tmp_path) -> None:
    _md_path, reports = _report(tmp_path)
    json_path = tmp_path / "2026-11-13.json"
    assert json_path.is_file()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["day"] == "2026-11-13"
    assert data["matches_compared"] == 1
    assert data["mismatch_rate_by_category"]["totals"] == 0.0
    assert data["findings"] == [f.as_dict() for report in reports for f in report.findings]


def test_report_writes_mismatch_findings(tmp_path) -> None:
    match = _finished_t20()
    summary = match.innings.order_by("innings_number").first().summary
    summary.runs += 1
    summary.save(update_fields=["runs"])
    report = compare_match(match)
    assert report.mismatch_count >= 1

    day = date(2026, 11, 13)
    with override_settings(SHADOW_REPORT_DIR=str(tmp_path)):
        md_path = write_report(day, [report])
    text = md_path.read_text(encoding="utf-8")
    assert "## Findings (1)" in text or "## Findings (2)" in text
    assert "critical" in text
    assert "inn=1" in text


# --------------------------------------------------------------------------- #
# Full driver pass
# --------------------------------------------------------------------------- #


def test_run_shadow_pass_records_metrics_and_queues_findings(tmp_path) -> None:
    """The driver end-to-end: compare, metrics, reporter (DLQ), report."""
    from apps.core import metrics

    # Register the DLQ reporter exactly as apps.ingestion.ready does.
    from apps.ingestion.services.reporters import _report_shadow_comparison
    from apps.shadow.services.driver import run_shadow_pass

    match = _finished_t20()
    from apps.matches.services import result_service

    result_service.finalise(match)
    assert str(match.status) == "COMPLETED"

    metrics.reset()
    register_shadow_comparison_reporter(_report_shadow_comparison)
    try:
        with override_settings(
            PROVIDER_SHADOW_MODE=True,
            SHADOW_REPORT_DIR=str(tmp_path),
        ):
            compared = run_shadow_pass(day=date.today())
    finally:
        register_shadow_comparison_reporter(None)

    assert compared == 1
    report_path = tmp_path / f"{date.today().isoformat()}.md"
    assert report_path.is_file()
    assert (tmp_path / f"{date.today().isoformat()}.json").is_file()
    text = report_path.read_text(encoding="utf-8")
    assert "Matches compared: **1**" in text
    assert "No mismatches (replay corpus is internally consistent)." in text

    # Metrics: the comparison counters were bumped and are rendered.
    rendered = metrics.render()
    assert "shadow_comparisons_total" in rendered
    assert "shadow_mismatches_total" in rendered
    assert "shadow_ingest_latency_seconds" in rendered

    # No mismatch -> no DLQ row for this clean corpus (flag-and-queue only).
    from apps.ingestion.models import IngestionError

    assert IngestionError.objects.filter(stage="shadow_compare").count() == 0


def test_run_shadow_pass_queues_dlq_row_when_corpus_has_mismatch(tmp_path) -> None:
    """An injected mismatch is queued to the DLQ, never auto-corrected."""
    from apps.core import metrics
    from apps.ingestion.services.reporters import _report_shadow_comparison
    from apps.shadow.services.driver import run_shadow_pass

    match = _finished_t20()
    from apps.matches.services import result_service

    result_service.finalise(match)
    # Inject AFTER finalise: finalise rebuilds the read models, which would
    # otherwise wipe the mismatch.
    summary = match.innings.order_by("innings_number").first().summary
    summary.runs += 1
    summary.save(update_fields=["runs"])

    metrics.reset()
    register_shadow_comparison_reporter(_report_shadow_comparison)
    try:
        with override_settings(
            PROVIDER_SHADOW_MODE=True,
            SHADOW_REPORT_DIR=str(tmp_path),
        ):
            compared = run_shadow_pass(day=date.today())
    finally:
        register_shadow_comparison_reporter(None)

    assert compared == 1
    from apps.ingestion.models import IngestionError

    rows = IngestionError.objects.filter(stage="shadow_compare")
    assert rows.count() >= 1
    payload = rows.first().payload
    assert payload["category"] == "totals"
    assert payload["field"] == "runs"

    # The match row is untouched: the injected wrong value is still there.
    summary.refresh_from_db()
    assert summary.runs == 61


def test_run_shadow_pass_covers_provider_scoped_paths(tmp_path) -> None:
    """Provider-scoped driver paths: filter by source, coverage gap, identity.

    Seeds a ``replay`` DataProvider row (as Task 3's migration will) and a
    completed match owned by it plus one owned by another provider, so the
    driver's ``provider is not None`` branches (match filter, coverage gap,
    unresolved-identity scope) are exercised. The provider-owned match has no
    event-log rows, exercising the latency `continue` branch too.
    """
    from datetime import UTC, datetime, timedelta

    from tests.phase_c.factories import make_match, make_match_teams, make_team

    from apps.core import metrics
    from apps.providers.models import DataProvider
    from apps.shadow.services.driver import run_shadow_pass

    replay, _ = DataProvider.objects.get_or_create(code="replay", defaults={"name": "Replay"})
    cricsheet, _ = DataProvider.objects.get_or_create(
        code="cricsheet", defaults={"name": "Cricsheet"}
    )

    now = datetime.now(UTC)
    owned = make_match(
        status="COMPLETED",
        source_provider_id=replay.id,
        start_time_utc=now - timedelta(days=1),
        end_time_utc=now,
    )
    make_match_teams(owned, make_team("India"), make_team("Australia"))
    other = make_match(
        status="COMPLETED",
        source_provider_id=cricsheet.id,
        start_time_utc=now - timedelta(days=1),
        end_time_utc=now,
    )
    make_match_teams(other, make_team("England"), make_team("NZ"))

    metrics.reset()
    with override_settings(
        PROVIDER_SHADOW_MODE=True,
        SHADOW_REPORT_DIR=str(tmp_path),
    ):
        compared = run_shadow_pass(day=date.today())

    assert compared == 1  # only the replay-owned match is in scope
    report_data = json.loads(
        (tmp_path / f"{date.today().isoformat()}.json").read_text(encoding="utf-8")
    )
    assert report_data["coverage_gaps"] == 1  # the cricsheet-owned match is a gap


def test_run_shadow_pass_refuses_when_mode_off(tmp_path) -> None:
    from apps.shadow.services.driver import run_shadow_pass

    with (
        override_settings(PROVIDER_SHADOW_MODE=False),
        pytest.raises(RuntimeError, match="shadow mode is off"),
    ):
        run_shadow_pass(day=date(2026, 11, 13))
