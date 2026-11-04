# Runbooks — live ingestion, realtime & caching

Every alert in `ops/alerts/*.yml` maps to a runbook here
(SECURITY_AND_RELIABILITY §10): `live_alerts.yml` covers ingestion (Phase D),
`realtime_alerts.yml` covers the realtime transports and the cache (Phase E).
**Every recovery path is safe to run twice** — all replay/backfill operations
are idempotent (per-match advisory lock + `delivery_seq` resume + `is_current`
revisioning; realtime replay via `events_since(seq)` against the durable log).
Read the alert's `runbook` label to jump to the right section.

| Alert | Runbook |
|---|---|
| `LiveMatchStale` | [Provider outage](#provider-outage) |
| `CircuitBreakerOpen` | [Provider outage](#provider-outage) |
| `ProviderLagHigh` | [Provider outage](#provider-outage) |
| `DLQDepthHigh` | [DLQ triage](#dlq-triage) |
| `ShadowVerifyMismatch` | [Wrong score complaint](#wrong-score-complaint) |
| `IngestLatencyHigh` | [Stuck checkpoint](#stuck-checkpoint) |
| `EventPublishFailures` | [Realtime gap](#realtime-gap) |
| `RealtimeDegraded` | [Realtime degraded](#realtime-degraded) |
| `RealtimeDroppedHigh` | [Realtime backpressure](#realtime-backpressure) |
| `RealtimeGapBackfills` | [Realtime gap](#realtime-gap) |
| `RealtimeResyncsHigh` | [Realtime resync](#realtime-resync) |
| `RealtimeCapDeniedHigh` | [Connection caps](#connection-caps) |
| `RealtimeConnectionsNearCap` | [Connection caps](#connection-caps) |
| `CacheBreakerOpen` | [Cache breaker](#cache-breaker) |
| `CacheDegraded` | [Cache breaker](#cache-breaker) |
| `CacheMissRatioHigh` | [Cache miss ratio](#cache-miss-ratio) |

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

**Symptoms.** `EventPublishFailures` (`event_publish_failures_total`
increasing) or `RealtimeGapBackfills` (`realtime_gap_backfills_total`).

**Detection.** `event_publish_failures_total` over 15 m; `event_log_lag` gauge
grows while `match_event_log` rows keep being written;
`realtime_gap_backfills_total` increasing means subscribers saw seq runs with
holes and the hub replayed the missing run from the log.

**Impact.** Redis `match:{id}` keys stop advancing (or drop messages between
publication and subscription); WS/SSE consumers (Phase E) would see stale data
or gap-backfill churn. The durable `match_event_log` is unaffected — the hub
replays from it, so no event is ever permanently lost.

**Recovery.**

1. Redis is best-effort: publication failures are logged, never fatal.
2. Restart/repair Redis; publication resumes on the next committed event.
3. Consumers recover via `events_since(seq)` (resync) — the log is the source
   of truth for realtime. An occasional `realtime_gap_backfills_total` bump is
   normal under Redis pressure; a sustained rate means messages are being lost
   (check the pubsub role's `maxmemory-policy` — it must be `noeviction`).
4. If Redis is permanently unavailable, disable publication
   (`LIVE_PUBLISH_ENABLED=false`) so the metric stops alerting; the log keeps
   accumulating and the broker serves from degraded polling.

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

---

# Realtime & caching runbooks (Phase E)

Alerts live in `ops/alerts/realtime_alerts.yml`. All realtime recovery is
driven by the durable `match_event_log` — the hub replays from it, so delivery
is eventually consistent even when every Redis transport is down.

## Realtime degraded

**Symptoms.** `RealtimeDegraded` — the broker's Redis pub/sub transport failed
`max_errors` times and the node switched to **degraded polling**: it polls the
event log for every match with subscribers at `poll_interval` cadence.

**Detection.** `realtime_degraded == 1` on the node; broker logs
"realtime broker degraded: polling the event log".

**Impact.** Delivery continues but at poll cadence (default 1 s), not
event-time — a degraded node is still correct, just less fresh. No data loss:
the log is the source of truth.

**Recovery.**

1. Check the pubsub Redis role (DB 3 by default) — connectivity,
   `maxmemory-policy` (`noeviction`), memory pressure.
2. Repair/restart Redis; the broker's periodic `ping()` probe detects recovery
   and clears `realtime_degraded` automatically.
3. If Redis is down for a long time, consider raising
   `REALTIME_POLL_INTERVAL` to shed load, or scale the node (each node polls
   independently).

**Verification.** `realtime_degraded` returns to 0; consumers see event-time
delivery again; `realtime_gap_backfills_total` stays flat.

## Realtime backpressure

**Symptoms.** `RealtimeDroppedHigh` — `realtime_dropped_frames_total`
increasing. A subscriber's bounded queue (64 frames, ≤1 Hz coalescing) is
full, so the hub dropped a coalesced batch and counted it.

**Detection.** `realtime_dropped_frames_total` over 5 m; correlate with the
node's `realtime_connections` and per-match subscriber counts
(`realtime_connections` is node-total; use connection caps per match to bound
hot matches).

**Impact.** The slow subscriber misses that batch; on reconnect it passes its
last `seq` and the hub replays the missing run from the log — nothing is
permanently lost. A sustained drop rate means a client cannot keep up.

**Recovery.**

1. Identify the slow clients (high per-IP connection counts, aggressive
   reconnect loops, very large fan-out matches).
2. Clients should coalesce rendering (batch frames already group ≤1 s of
   updates) and use `from_seq`/`Last-Event-ID` on reconnect.
3. If a hot match is the cause, the per-match connection cap
   (`REALTIME_CONNECTION_CAP_PER_MATCH`) bounds it; consider raising
   `DEFAULT_QUEUE_CAPACITY` only after confirming memory headroom.

**Verification.** `realtime_dropped_frames_total` flatlines; the affected
clients reconnect and observe contiguous `seq`s.

## Realtime resync

**Symptoms.** `RealtimeResyncsHigh` — `realtime_resyncs_total` increasing: the
hub told clients their requested position is outside the retained window.

**Detection.** `realtime_resyncs_total` over 5 m; look at client reconnect
patterns (a client reconnecting with a stale `last_seq` after a long pause).

**Impact.** Affected clients must refetch the snapshot over REST and
resubscribe from its `meta.event_seq` (the `resync_required` frame tells them
this; the connection stays live). A spike usually means a client bug (not
persisting `last_seq`) or a long pub/sub outage that pushed everyone past the
retention window (`LIVE_RECONCILIATION_WINDOW_HOURS`, default 24 h).

**Recovery.**

1. Confirm which clients are affected and whether they persisted their last
   seen `seq` (the contract requires it).
2. Fix the client to persist `seq` and resubscribe from `meta.event_seq` on
   `resync_required`.
3. If a long outage caused it, no server action is needed — clients refetch on
   their own once fixed.

**Verification.** `realtime_resyncs_total` flatlines; no client reports
stale scoreboards after recovery.

## Connection caps

**Symptoms.** `RealtimeCapDeniedHigh` (`realtime_cap_denied_total`) or
`RealtimeConnectionsNearCap` (`realtime_connections` near the 10000 node cap).

**Detection.** `realtime_cap_denied_total` over 5 m (SSE `429` /
`connection_limit`, WS close `4429`); `realtime_connections` per node.

**Impact.** Legitimate streams are rejected once a cap is reached: per-IP (8),
per-match (2000), per-node (10000). A single hot match or an IP behind a NAT
can legitimately trip its cap; a sustained high denied rate is overload.

**Recovery.**

1. Check whether one match or one IP dominates (`realtime_connections` +
   `realtime_cap_denied_total`; the caps are transport-namespaced `sse:*` /
   `ws:*`).
2. A broken auto-reconnect loop (reconnect without backoff) burns IP caps —
   fix the client; caps are released on disconnect.
3. Scale out horizontally (each node has its own in-process limiter) or raise
   `REALTIME_CONNECTION_CAP_PER_*` after capacity planning.

**Verification.** `realtime_cap_denied_total` returns to ~0;
`realtime_connections` stays comfortably below the node cap.

## Cache breaker

**Symptoms.** `CacheBreakerOpen` (`cache_breaker_open == 1`) or
`CacheDegraded` (`cache_degraded_total` increasing).

**Detection.** The local breaker opened after repeated Redis cache errors;
reads fall through to direct builds. `cache_degraded_total` counts the traffic
served without the cache; `cache_build_seconds` p95 rises because every read
rebuilds.

**Impact.** Correctness is unaffected (reads are always fresh); cost and
latency rise. The breaker auto-closes after the retry window; reads degrade
fail-**open** (availability over freshness).

**Recovery.**

1. Check the cache Redis role (DB 0) — connectivity, memory (`allkeys-lru`),
   `maxmemory`.
2. Repair/restart Redis; the breaker recovers on the next successful probe.
3. If memory churn is the cause, review key TTLs (see
   [Cache miss ratio](#cache-miss-ratio)).

**Verification.** `cache_breaker_open` returns to 0; `cache_degraded_total`
flatlines; `cache_hits_total` resumes rising.

## Cache miss ratio

**Symptoms.** `CacheMissRatioHigh` — miss ratio above 50% over 15 m.

**Detection.** `sum(rate(cache_misses_total[15m])) / (sum(rate(cache_hits_total[15m]))
+ sum(rate(cache_misses_total[15m]))) > 0.5`; the metric is namespaced, so
check which namespace (`match:m1`, `matches:live`, `players`, …) dominates.

**Impact.** Most reads rebuild entries instead of hitting Redis — elevated
latency and load. Common causes: TTLs too short, an invalidation storm
(`cache_invalidations_total` spiking — e.g. a series of corrections or a
reference-data merge), or a workload mix change.

**Recovery.**

1. Identify the hot namespace from the labels.
2. Check `cache_invalidations_total`: a storm means a noisy event topic — fix
   the publisher (corrections are expected to invalidate, but a loop is not).
3. Raise TTLs for stable entities (immutable/`s-maxage=31536000` entries
   after the reconciliation window already avoid revalidation).
4. Confirm single-flight is working (`cache_build_seconds` count vs misses).

**Verification.** Miss ratio returns under 50%; `cache_hits_total` dominates;
p95 cached-read latency returns to the ≤200 ms budget.
