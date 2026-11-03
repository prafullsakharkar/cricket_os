"""Celery tasks for the live ingestion framework (DATA_INGESTION §4.3).

* :func:`poll_live_match_task` — one poll cycle for one match (breaker-aware);
* :func:`dispatch_polling_task` — the beat task: poll every live match once,
  then re-schedule with the adaptive per-match cadence;
* :func:`receive_push` — processes an authenticated webhook payload through the
  same validated pipeline (enqueued by the push receiver).

Transient provider failures are handled by the breaker + backoff in the caller;
the tasks themselves are best-effort and never retry a whole match blindly (a
poll is idempotent, so a retry is safe but usually unnecessary — the next poll
picks up the same data).
"""

from __future__ import annotations

import json
from typing import Any

from config.celery import app


@app.task(name="live.poll_live_match", autoretry_for=())  # type: ignore[untyped-decorator]
def poll_live_match_task(match_id: str, provider_id: str | None = None) -> dict[str, Any]:
    """Poll one match; the report is returned for the dispatcher/observability."""
    from apps.live.services.polling import poll_live_match
    from apps.matches.models import Match

    match = Match.objects.filter(id=match_id).first()
    if match is None:
        return {"match_id": match_id, "polled": False, "skipped_reason": "no_match"}
    report = poll_live_match(
        match,
        fetcher=_noop_fetcher,
        source=provider_id,
        provider=provider_id,
    )
    return {
        "match_id": report.match_id,
        "polled": report.polled,
        "skipped_reason": report.skipped_reason,
        "applied": report.applied,
        "corrected": report.corrected,
        "noop": report.noop,
        "changed": report.changed,
    }


@app.task(name="live.dispatch_polls", autoretry_for=())  # type: ignore[untyped-decorator]
def dispatch_polling_task() -> list[dict[str, Any]]:
    """Poll every live match once (beat schedule entry)."""
    from apps.live.services.polling import dispatch_polls

    reports = dispatch_polls()
    return [
        {
            "match_id": r.match_id,
            "polled": r.polled,
            "skipped_reason": r.skipped_reason,
            "applied": r.applied,
            "corrected": r.corrected,
            "noop": r.noop,
            "changed": r.changed,
        }
        for r in reports
    ]


@app.task(name="live.receive_push", autoretry_for=())  # type: ignore[untyped-decorator]
def receive_push(payload: bytes | str) -> dict[str, Any]:
    """Process an authenticated webhook payload (enqueued by the receiver).

    The signed body is the **normalized batch envelope** produced by an
    adapter: ``{"match_id", "state": {...}?, "batches": [{"innings_number",
    "drafts": [delivery-dicts]}]}``. Draft dicts reconstruct
    ``DeliveryDraft`` records; ``apply_batch`` validates and writes them. A
    real adapter translates provider events into this same envelope (see
    LIVE_INGESTION.md), so the webhook path exercises the identical pipeline.
    """
    from apps.live.services.apply import LiveInningsBatch, apply_batch

    body = payload if isinstance(payload, bytes) else payload.encode()
    data = json.loads(body.decode())
    match_id = data.get("match_id")
    if not match_id:
        return {"accepted": False, "error": "missing_match_id"}

    from apps.matches.models import Match

    match = Match.objects.filter(id=match_id).first()
    if match is None:
        return {"accepted": False, "error": "no_match"}

    batches = [
        LiveInningsBatch(
            innings_number=int(entry["innings_number"]),
            drafts=tuple(_draft_from_dict(d) for d in entry.get("drafts", ())),
        )
        for entry in data.get("batches", [])
    ]
    state = _state_from_dict(data.get("state")) if data.get("state") else None
    report = apply_batch(match, batches, state=state, checkpoint_scope=f"match:{match_id}")
    return {
        "accepted": True,
        "match_id": match_id,
        "applied": report.appended,
        "corrected": report.corrected,
        "noop": report.noop,
        "changed": report.changed,
    }


def _draft_from_dict(raw: dict[str, Any]) -> Any:
    from apps.deliveries.services.validation import DeliveryDraft

    return DeliveryDraft(**raw)


def _state_from_dict(raw: dict[str, Any]) -> Any:
    from apps.live.services.apply import LiveState

    return LiveState(
        status=raw["status"],
        state_title=raw.get("state_title"),
        status_detail=raw.get("status_detail"),
        reopen=raw.get("reopen", False),
    )


def _noop_fetcher(match: Any, since_seq: int | None) -> list[Any]:  # pragma: no cover - glue
    return []


__all__ = ["dispatch_polling_task", "poll_live_match_task", "receive_push"]
