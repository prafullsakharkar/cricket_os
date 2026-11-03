# Runbooks — live ingestion & data integrity

Every alert in `ops/alerts/live_alerts.yml` maps to a runbook here
(SECURITY_AND_RELIABILITY §10). **Every recovery path is safe to run twice** —
all replay/backfill operations are idempotent (per-match advisory lock +
`delivery_seq` resume + `is_current` revisioning). Read the alert's
`runbook` label to jump to the right section.

| Alert | Runbook |
|---|---|
| `LiveMatchStale` | [Provider outage](#provider-outage) |
| `CircuitBreakerOpen` | [Provider outage](#provider-outage) |
| `ProviderLagHigh` | [Provider outage](#provider-outage) |
| `DLQDepthHigh` | [DLQ triage](#dlq-triage) |
| `ShadowVerifyMismatch` | [Wrong score complaint](#wrong-score-complaint) |
| `IngestLatencyHigh` | [Stuck checkpoint](#stuck-checkpoint) |
| `EventPublishFailures` | [Realtime gap](#realtime-gap) |

## Provider outage

**Symptoms.** `LiveMatchStale` (freshness > 90 s), `CircuitBreakerOpen`
(`circuit_breaker_state{provider,endpoint} == 2`), `ProviderLagHigh`
(`provider_lag_seconds > 60`). Public reads still return last-known-good with
`meta.stale=true` (SECURITY §8: a provider failure never blocks reads).

**Detection.**

- `GET /api/v1/matches/live` — items carry `stale: true` when their feeding
  provider's breaker is open.
- `dlq_depth` / `ingestion_errors_total{stage="..."}` for rejected records.
- Breaker state gauge per provider/endpoint (0=closed, 1=half-open, 2=open).

**Impact.** Live matches freeze until the provider recovers; the API degrades
gracefully (stale content, correct cache TTLs). No data is lost: the poll
resumes from `SyncCheckpoint.last_delivery_seq`.

**Recovery.**

1. Confirm the incident with the provider (status page / contact); note the
   ETA in the incident channel.
2. **Do nothing** while the breaker is `HALF_OPEN` — probes recover it
   automatically once `recovery_timeout` elapses.
3. If the provider recovered but the breaker is stuck (config misjudged),
   force-close it via the steward admin breaker action (`CircuitBreaker.reset`).
4. After recovery, the next poll fetches `since_seq = last committed seq` and
   backfills everything missed. Verify with the freshness monitor.

**Verification.** `match_freshness_seconds` back under 90 s for all live
matches; `meta.stale` is `false` on `/matches/live`.

## Schema drift

**Symptoms.** Ingestion errors with `error_class` matching a validation/quarantine
failure; contract-test failures for the provider adapter; `ingestion_errors_total`
climbing for one `stage`.

**Detection.** `dlq_depth` growing; `stage` labels on `ingestion_errors_total`;
the provider adapter's contract suite (`tests/contracts/provider_adapter_kit.py`)
fails against a captured payload.

**Impact.** New deliveries/fixtures are quarantined (never written half-formed);
matches go stale but remain readable.

**Recovery.**

1. Capture the rejected raw payload (redacted DLQ row) and diff against the
   adapter contract.
2. Fix the adapter mapping; add a regression to the contract suite.
3. Re-drive the quarantined rows with the same idempotency keys (safe to run
   twice — duplicates are no-ops).
4. Re-run the adapter contract suite; run `make check`.

**Verification.** `dlq_depth` returns to ~0; new deliveries flow; contract suite
green.

## Stuck checkpoint

**Symptoms.** `IngestLatencyHigh` (P95 > 5 s) and/or a match that never
advances despite the provider updating. The checkpoint for `match:{id}` has not
moved.

**Detection.**

- `SyncCheckpoint` rows: `last_delivery_seq`/`last_provider_updated_at` frozen
  while the provider shows newer data.
- `match_freshness_seconds{match_id}` flatlined for one match.
- `apply_batch` never stamps `last_ingested_at` (empty polls do not advance it).

**Impact.** One match (or the whole poll loop if a lock is stuck) falls behind.
Advisory locks are transaction-scoped, so a crashed worker releases them; the
Redis TTL poll lock expires on its own.

**Recovery.**

1. Confirm no worker holds the match's poll lock (`cache` TTL key) or an open
   transaction holding the advisory lock.
2. Manually re-drive: `python manage.py poll_live --match <id>` (idempotent;
   resumes from the checkpoint).
3. If the checkpoint itself is corrupt (seq > provider length), use
   `reconcile_checkpoint_gaps` to surface the gap, then backfill by
   re-fetching `since_seq = checkpoint - 1`.
4. Re-run the dispatcher.

**Verification.** `last_ingested_at` advances; `match_freshness_seconds` resets;
the event log's `seq` grows for that match.

## Wrong score complaint

**Symptoms.** `ShadowVerifyMismatch` fires, or a user reports a score that does
not match the provider's published card.

**Detection.** `shadow_verify_mismatches_total` increased; the match's
`match_event_log` shows a `delivery.corrected`/`correction.applied` sequence.

**Impact.** A displayed score is wrong until corrected. The engine derives
scores from stored deliveries; a correction revises the deliveries and rebuilds
Tier-1 rows in place (match stays `COMPLETED`, `match.completed` fires once).

**Recovery.**

1. Compare the stored current deliveries against the provider card
   (`reconcile_state` on the completed match).
2. If the provider revised data, apply the correction through the correction
   path (steward admin): it supersedes the wrong deliveries, bumps `revision`,
   appends `delivery.corrected` + `correction.applied`, and rebuilds the
   innings.
3. The strong ETag changes automatically, so caches and clients pick up the
   corrected payload.
4. If the engine itself disagrees with the deliveries, file a scoring-engine
   bug — never hand-edit derived rows.

**Verification.** `GET /matches/{id}/scorecard` matches the provider card;
`meta.revision` increased; no `match.completed` duplicate.

## DLQ triage

**Symptoms.** `DLQDepthHigh` (`dlq_depth > 100` for 10 m).

**Detection.** Open `IngestionError` rows (payloads redacted; `provider_id`
stamped). Group by `stage` + `error_class`.

**Impact.** Rows are quarantined, not lost: the pipeline continues; nothing
half-written. A growing DLQ means the failure is systematic, not transient.

**Recovery.**

1. Group by `stage`/`error_class` to find the common cause.
2. Fix the mapping/adapter/credential (see [Schema drift](#schema-drift)).
3. Re-drive rejected rows with their original idempotency keys (safe twice).
4. Confirm resolution; leave genuinely unmappable rows for steward review.

**Verification.** `dlq_depth` trends to 0; `ingestion_errors_total` stops
increasing; no data missing from the final scorecards.

## Realtime gap

**Symptoms.** `EventPublishFailures` (`event_publish_failures_total` increasing).

**Detection.** `event_publish_failures_total` over 15 m; `event_log_lag` gauge
grows while `match_event_log` rows keep being written.

**Impact.** Redis `match:{id}` keys stop advancing; WS/SSE consumers (Phase E)
would see stale data. The durable `match_event_log` is unaffected.

**Recovery.**

1. Redis is best-effort: publication failures are logged, never fatal.
2. Restart/repair Redis; publication resumes on the next committed event.
3. Consumers recover via `events_since(seq)` (resync) — the log is the source
   of truth for realtime.
4. If Redis is permanently unavailable, disable publication
   (`LIVE_PUBLISH_ENABLED=false`) so the metric stops alerting; the log keeps
   accumulating.

**Verification.** `event_publish_failures_total` flatlines; `event_log_lag`
returns to ~0; a test consumer sees contiguous `seq`s with no gap.

## Identity conflict

**Symptoms.** Ingestion errors around player/team resolution; DLQ rows with
`stage` = identity resolution; steward inbox has unresolved candidates.

**Detection.** `ingestion_errors_total{stage="identity"}`; open
`ProviderEntityMap` rows with `verified=False`; duplicate-player candidates.

**Impact.** New deliveries can be quarantined when identity cannot be resolved.
Never auto-merge: candidates below the link threshold are only flagged.

**Recovery.**

1. Resolve the candidates in the steward admin (merge or reject) — audit
   events `player.merged`/`duplicate.reviewed` are appended.
2. Re-drive the quarantined rows; resolution now hits the map.
3. Verify no cross-entity corruption: merged rows keep both maps pointing at
   one internal entity.

**Verification.** No new identity-stage DLQ rows; duplicate candidates cleared;
the affected player's career rows are consistent (Phase F aggregates).
