# Phase D — Live matches: implementation plan

**Branch:** `phase-d` off `phase-c`. **Phase 0 gate:** `docs/PROVIDER_DECISION.md`
is **ABSENT** → ReplayAdapter only, no vendor adapter; ship an adapter template
and a reusable contract kit.

Execute in reviewable commits. Each group: implement → test → `ruff format` →
commit. Final gate: `make check` + schema-ddl/migration-drift + acceptance run.

## Commit groups

1. **State machine** (`apps/matches/services/state.py`, `state_text.py`):
   transition table, typed `IllegalTransition`, idempotent same-state no-op,
   audited reopen, `state_title`/`status_detail`, `match.state_changed` events.
   Add `match.state_title`, `match.last_ingested_at` columns (migration + schema
   doc DDL).
2. **Durable event log + publication** (`apps/matches/models.MatchEventLog`,
   `services/event_log.py`, `services/publication.py`): per-match advisory-lock
   sequencing, `UNIQUE(match_id, seq)`, `events_since`/`resync_required`,
   Redis `match:{id}` publication after commit (failure non-fatal), retention.
   Add `match_event_log` + `reconciliation_job` to drift scope.
3. **Live write path** (`apps/live/`): `apply_batch` under the per-match lock —
   append/revise via Phase C path, Tier-1 rebuild per innings, state
   transitions, event-log rows, `last_ingested_at`, checkpoint intent; shadow
   verify every N deliveries + innings end; Tier-1 isolation test.
4. **Results + corrections + reconciliation**: `matches.services.result_service`
   (single completion, engine results, supplied DLS target only),
   `synchronization.services.corrections` extension, `reconciliation.py`
   (checkpoint/gap, state, fixture) + `ReconciliationJob` model/admin.
5. **Live ingestion framework** (`apps/live/services/*`, `tasks.py`,
   management commands): backoff+jitter/Retry-After, circuit breaker, polling
   dispatcher with TTL lock + adaptive cadence, feature-flagged HMAC push
   receiver, credential abstraction + redaction, capabilities degradation.
6. **ReplayAdapter + fault harness** (`apps/providers/adapters/replay.py`),
   deterministic virtual clock, fault injection, `ENVIRONMENT=prod` guard;
   `tests/contracts/provider_adapter_kit.py` reusable suite.
7. **Licensing** (`apps/providers/licensing.py`): per-provider policy from
   settings, attribution, field degradation (`403 license_required`), retention
   job (dry-run default).
8. **Public endpoints**: `/matches/live|upcoming|recent` selectors + views,
   state-dependent cache headers, cursor pagination, `meta.stale`, attribution,
   single indexed scan + no N+1.
9. **Observability + runbooks**: Prometheus facade (`apps/core/metrics.py`),
   internal `/metrics`, alert rules under `ops/alerts/`, `docs/RUNBOOKS.md`.
10. **Docs**: `docs/LIVE_INGESTION.md` (Mermaid + state machine + event
    schemas + flags), update schema/domain/API/ingestion/architecture/security/
    roadmap "Phase D decisions".

## Key decisions

- **No `apply_delivery`** (SCORING_ENGINE_API §7): full per-innings rebuild
  (~3 ms/300 balls); shadow verify recomputes and compares digests.
- **Advisory xact lock** keyed on match UUID: transaction-scoped, auto-released
  on rollback, no counter row, gapless by construction.
- `match.status` lifecycle only; outcome in `result_type`/`match_result`.
- `state_title` ("Day 4 - Tea") and `last_ingested_at` are new columns (schema
  doc updated; drift scope updated).
- Licensing policy is settings configuration (`LICENSED_CONTENT_POLICIES`), not
  a table, per SECURITY §11 "configuration, not code".
- `prometheus_client` is not installed in this offline environment; metrics use
  a drop-in facade that delegates to `prometheus_client` when present.

## Acceptance mapping

See the final report for commands and results; tests live under
`tests/phase_d/`, `tests/contracts/`, plus `ops/alerts/` and runbooks.
