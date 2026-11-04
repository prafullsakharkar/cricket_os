# Phase E — Frontend APIs & real-time updates (execution ledger)

Branch: `phase-e` from `develop` @ `8de11c3`. Inline execution with TDD.
Ledger updated per commit; each group is independently reviewable.

## Pre-flight: conflicts resolved (record in final report)

1. **`realtime` boundary vs event-log access.**
   - `ARCHITECTURE.md` §2/§3: `realtime` depends only on `core`.
   - `PERFORMANCE §8`: "the `realtime` module owns `match_event_log`";
     `DATABASE_SCHEMA.md`/Phase D place the table + service in `apps/matches`.
   - **Resolution:** `DATABASE_SCHEMA.md` wins on the table (stays in `matches`);
     `ARCHITECTURE.md` wins on boundaries. `apps/core/streaming.py` defines the
     narrow `EventLogReader`/`StreamInfo` protocols (core is a leaf), `apps/matches`
     implements them (`apps/matches/services/stream_reader.py`), and the
     composition root (`config/asgi.py` + `apps.realtime.wiring`) injects the
     implementation into `apps/realtime`. `realtime` imports only `core`.
2. **Cursor pagination everywhere (roadmap) vs API_SPEC §5.** Spec wins:
   cursor for time-ordered feeds (`/matches/live|upcoming|recent`,
   `/deliveries`, `/players` without `q`), page-number for bounded tables
   (reference lists, `/players?q=`). Existing Phase B/C/D behaviour is kept.
3. **Live ETag weak (API_SPEC §3.9) vs task's "strong ETag".** Task + Phase D
   precedent wins: strong `ETag` (content digest) for every cacheable GET.
   Strong validators are safe where weak ones are allowed; recorded here.
4. **Completed `Cache-Control` (API_SPEC: `max-age=300, s-maxage=3600`)
   vs Phase D (`max-age=300, swr=600`).** Merge both: completed =
   `public, max-age=300, s-maxage=3600, stale-while-revalidate=600,
   stale-if-error=86400`; after the reconciliation window add
   `s-maxage=31536000, immutable`.
5. **`event_seq` capture.** At-least-as-new (seq first) can replay an event the
   snapshot already contains; at-most-as-new (seq last) can skip one. Use a
   **REPEATABLE READ snapshot** for the data read + `MAX(seq)` when no outer
   transaction exists; inside an existing transaction (tests) the outer
   isolation is inherited (documented). Reads never take the write lock.
6. **Correction frames / coalescing.** Corrections (`correction.applied`) and
   terminal transitions (`match.completed`) flush immediately; ordinary
   delivery/scorecard deltas coalesce on the ≤1 Hz tick. Reason: a correction
   changes history and must not be delayed.
7. **`immutable` window.** `LIVE_RECONCILIATION_WINDOW_HOURS` (default 24 h)
   reused as the post-completion window; no new setting.
8. **Rate-limit failure mode.** Public reads fail **open** (availability) with a
   per-process fallback bucket + metric; connection-cap checks fail **open** too
   but are still bounded per node. Recorded in docs.
9. **Redis roles.** Separate logical DBs by default (`cache` 0, `pubsub` 3,
   `ratelimit` 4, Celery broker 1/result 2) with separate URLs; `noeviction`
   for broker/ratelimit, `allkeys-lru` for cache. Local/CI may share one server.

## Commit groups

1. **Redis roles + factory** (`core.redis`, settings, docs table, tests).
2. **Cache layer** (`core.cache`: keys/normalization, TTL policy, entry,
   version store, single-flight, local breaker, invalidation registry,
   CachePurger, metrics) + invalidation event publishers for reference data.
3. **Cache integration** (view mixin, all public GETs, snapshot `event_seq`).
4. **HTTP/CDN polish** (`s-maxage`, `immutable`, `stale-if-error`, `Vary`,
   surrogate keys, trusted-proxy IP, pagination audit).
5. **Throttling** (token bucket store + virtual clock tests, DRF throttle,
   scopes, API-key hook, fail-open, connection limiter primitives).
6. **Realtime core** (`core.streaming` protocols, hub, broker, log reader,
   frames, replay/gap/backfill, degraded polling, coalescing, backpressure,
   licensing, drain) — unit/integration with fakes.
7. **SSE transport** (view, headers, Last-Event-ID/from_seq, heartbeats,
   caps/Origin, ASGI tests).
8. **WebSocket transport** (Channels consumer, routing, ASGI wiring, Origin,
   message validation/limits, drain, parity tests).
9. **Reference client + dev demo** (`tools/reference_client`, `docs/FRONTEND_CONTRACT.md`).
10. **Observability/runbooks/alerts** (metrics, alert YAML, runbook sections).
11. **Load tests** (`loadtests/` k6 scripts + CI Python smoke + Makefile gate).
12. **Docs/roadmap** (API spec, performance, architecture, security, testing,
    ingestion, NEW `docs/REALTIME_AND_CACHING.md`, roadmap decisions).

## Acceptance command matrix

| # | Criterion | Command / evidence |
|---|---|---|
| 1 | `make check` + schema/migration/load smoke | `make check`, `make test-loadtest-smoke`; CI workflows |
| 2 | REST→subscribe exactly once | `tests/phase_e/test_snapshot_stream.py` |
| 3 | Reconnect/resync | `tests/phase_e/test_realtime_*.py` |
| 4 | Dropped pubsub / Redis outage healed | `test_realtime_hub.py::test_gap_backfill`, degraded/recovery |
| 5 | Coalescing + slow consumer | `test_realtime_hub.py::test_coalescing`, `test_backpressure` |
| 6 | N misses → 1 build; invalidation; Redis down | `test_cache_*.py` |
| 7 | Headers/ETag/surrogate/purge | `test_http_cache.py`, `test_surrogate_purge.py` |
| 8 | Limits/caps/Origin/spoofed XFF | `test_throttling.py`, `test_realtime_transport.py` |
| 9 | Graceful drain jitter | `test_realtime_hub.py::test_drain` |
| 10 | p95 cached live ≤ 200 ms; fan-out ≤ 1 s | `make test-loadtest-smoke` output |
| 11 | Docs/runbooks/alerts/contract/roadmap | file diff |

## Notes / risks

- `prometheus_client` absent: metrics facade fallback; alert YAML validated by
  tests, not a live Prometheus.
- No external network: all transports tested through ASGI/Channels test clients;
  real Redis/Postgres are local.
- k6 is not installed here; scripts are provided for manual runs, and a Python
  in-process smoke gate runs in CI.
- Coverage gates: add `apps/realtime` to the main coverage source and keep
  ≥90%; hub gap/backfill + coalescing + invalidation registry ≥95% branch.
