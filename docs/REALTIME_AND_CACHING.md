# Realtime & Caching (Phase E)

How Cricket OS serves fast reads and live updates, and how the two subsystems
fit together. This is the implementation companion to the wire contract in
[FRONTEND_CONTRACT.md](FRONTEND_CONTRACT.md) (what clients see) and the
load-test budgets in [CRICKET_PERFORMANCE_AND_SCALING.md §8](CRICKET_PERFORMANCE_AND_SCALING.md)
and `loadtests/` (how fast it must be).

```
                    ┌───────────────────────────┐
   providers ──────▶│  ingestion → scoring →    │
                    │  match_event_log (truth)  │
                    └───────────┬───────────────┘
                                │ append (commit-time)
              ┌─────────────────┼──────────────────┐
              ▼                 ▼                   ▼
     ┌───────────────┐  ┌───────────────┐   ┌──────────────────┐
     │ cache         │  │ Redis pub/sub │   │ invalidation     │
     │ (role DB 0)   │  │ (role DB 3)   │   │ registry + purge │
     └───────┬───────┘  └───────┬───────┘   └────────┬─────────┘
             │                  │                    │
             │            ┌─────▼─────┐              │
             │            │ broker    │  (degraded:  │
             │            │ → hub     │   poll log)  │
             │            └─────┬─────┘              │
             ▼                  ▼                    ▼
      ┌──────────────┐   ┌──────────────┐   ┌────────────────┐
      │ REST reads   │   │ SSE / WS     │   │ CDN (Surrogate-│
      │ (cached,     │   │ transports   │   │  Key PURGE)    │
      │ event_seq)   │   │ (same hub)   │   │                │
      └──────────────┘   └──────────────┘   └────────────────┘
```

The **event log is the source of truth** for both paths: the cache is a
rebuildable projection of it, and realtime delivery replays from it. Redis is
ephemeral — nothing is ever read back from Redis as truth.

---

## 1. Redis roles

One base URL (`REDIS_URL`); each logical role gets its own database so an
`allkeys-lru` cache eviction can never drop a broker task or a rate-limit
counter, and a memory-policy change on one role cannot hurt another.

| Role | DB | Setting | Eviction | Used by |
|---|---|---|---|---|
| cache | 0 | `CACHE_REDIS_URL` | `allkeys-lru` | read-through cache (`django_redis`, `caches["cache"]`) |
| Celery broker | 1 | `CELERY_BROKER_URL` | `noeviction` | task queue |
| Celery result | 2 | `CELERY_RESULT_BACKEND` | `noeviction` | task results |
| pubsub | 3 | `PUBSUB_REDIS_URL` | `noeviction` | realtime `match:{id}` channels |
| ratelimit | 4 | `RATELIMIT_REDIS_URL` | `noeviction` | token buckets |

All Redis clients are bounded: `REDIS_SOCKET_TIMEOUT_SECONDS` (0.25 s),
`REDIS_CONNECT_TIMEOUT_SECONDS` (0.5 s), health checks every 30 s, max 100
connections. **A slow Redis degrades a feature, never a request.**

---

## 2. The cache layer (`apps/core/cache`)

### 2.1 Keys and versions

Every cached resource lives under a **namespace** (`match:shell`, `matches:live`,
`players`, …) and a **version** derived from that namespace's event counter
(`cache:{ns}:{version}:{key}`). Invalidating a namespace = bumping its version:
old keys become unreachable immediately, no wildcard deletes, no races between a
reader and a purger. The version store lives in Redis (`cache:version:{ns}`),
falling back to a local counter when Redis is down.

### 2.2 Read-through

`CachedReadMixin.dispatch` (in `apps/core/cache_views.py`) wraps every cacheable
GET:

1. Throttle + auth checks run **first** (a cache hit is still rate-limited).
2. `store.get_or_build`: on a miss, **single-flight** — one builder per key
   while concurrent readers wait on the same future — builds the payload, and
   serves **stale-while-revalidate** during soft expiry.
3. A **local circuit breaker** (`LocalBreaker`): after 3 consecutive Redis
   errors it opens for 5 s and reads **fail open** (build directly, serve
   fresh) rather than failing the request.

### 2.3 State-dependent policy

One `CachePlan` drives the Redis TTL **and** the `Cache-Control` header so they
can never drift (`apps/core/cache/policy.py`):

| State | Redis TTL | `Cache-Control` |
|---|---|---|
| live | 5 s | `public, max-age=5, stale-while-revalidate=5` |
| completed (in window) | 900 s | `public, max-age=300, s-maxage=3600, stale-while-revalidate=600, stale-if-error=86400` |
| past reconciliation window (24 h) | 86400 s | `public, max-age=31536000, s-maxage=31536000, immutable` |
| reference data | 3600 s | `public, max-age=600` |

### 2.4 Invalidation

Domain events publish to an **invalidation registry** (namespace → affected
matches/entities). On commit, the registry bumps the namespace versions and
emits **surrogate keys** for the CDN `PURGE` hook (`CACHE_PURGE_URL`, empty =
no CDN; `CACHE_SURROGATE_HEADER`, default `Surrogate-Key`). A correction
(`correction.applied`) invalidates and changes the `ETag` even after completion.

### 2.5 `meta.event_seq` — the REST→realtime handoff

Every match-scoped payload carries `meta.event_seq`: the highest committed
`match_event_log.seq` reflected by the snapshot, captured with a **REPEATABLE
READ** snapshot (at-least-as-new semantics; see the Phase E ledger decision 5).
A client that subscribes with `last_seq = meta.event_seq` receives **exactly**
the events after the snapshot — no gap, no duplicate (the exactly-once rule,
FRONTEND_CONTRACT §3.2).

---

## 3. Realtime pipeline (`apps/realtime`)

### 3.1 Boundaries

`apps/realtime` imports **only** `apps.core` (import-linter enforced). The
narrow protocols (`EventLogReader`, `StreamInfo`, `MatchPolicy`) live in
`apps/core/streaming.py`; the implementations live in `apps/matches`
(`PostgresEventLogReader`, licensing policy) and are injected by the
composition root (`config/asgi.py` + `apps/realtime/wiring.py`). No ORM call
happens inside `apps/realtime` without going through the injected reader/policy.

### 3.2 Broker — Redis pub/sub with degraded polling

Every web node runs one `Broker` that pattern-subscribes to `match:*` and feeds
the local hub. Delivery is **best effort**: after `max_errors` (3) consecutive
transport failures the broker enters **degraded mode** — it polls the durable
event log (`events_since`) for every match with subscribers at `poll_interval`
(default 1 s, constructor-injected), and periodically probes the transport for
recovery.
A Redis outage degrades realtime delivery; it never takes the node down, and the
log guarantees nothing is permanently lost. Invalid frames are counted and
dropped; a single corrupt message never kills the loop.

### 3.3 Hub — coalescing, backpressure, gap recovery

The hub is the in-process fan-out point; it routes broker frames to per-match
fanouts and then to subscribers. Behaviours:

* **Coalescing** — ordinary deltas (`delivery.appended`, `innings.updated`,
  `match.state_changed`) accumulate in `pending` and flush as **one `batch`
  frame** on the ≤1 s tick, so a burst of deliveries costs one update per
  client.
* **Immediate flush** — corrections (`correction.applied`) and terminal
  transitions (`match.completed`) **never wait for the tick**: they change
  history, so they deliver instantly (ledger #6).
* **Backpressure** — every subscriber has a bounded queue (default 64 frames);
  a slow consumer whose queue is full has the batch dropped and counted
  (`realtime_dropped_frames_total`). Nothing is permanently lost: recovery is
  the seq-based replay on reconnect.
* **Gap/backfill** — a live frame whose `seq` skips the expected next `seq`
  means pub/sub dropped messages; the hub flushes pending, replays the missing
  run from the log reader, then continues. An unreplayable position yields
  `resync_required` (client refetches the REST snapshot).
* **Licensing degradation** — a per-match policy filters unlicensed event types
  (sequence is still tracked, delivery is not).
* **Drain** — on graceful shutdown the hub flushes pending, delivers
  `server.draining`, then closes connections with a small jittered stagger so
  reconnects do not thundering-herd.

### 3.4 Transports — SSE and WebSocket, one contract

| | SSE (`apps/realtime/sse.py`) | WS (`apps/realtime/consumers.py`) |
|---|---|---|
| Endpoint | `GET /api/v1/sse/matches/{id}/` | `WS /api/v1/ws/matches/{id}/` |
| Frames | SSE `id:`/`event:`/`data:` blocks | JSON text messages — **byte-identical** to the SSE `data:` line (parity contract) |
| Resume | `Last-Event-ID` header or `from_seq` query | `from_seq` query |
| Heartbeat | `:` comment every `REALTIME_SSE_HEARTBEAT_SECONDS` | client `{"type":"ping"}` → `{"type":"pong"}` |
| Client messages | — | JSON objects only; `ping` answered, others ignored, > 4096 B → close `4409`, malformed → `4400` |
| Close codes | 403 `origin_not_allowed` / 429 `connection_limit` | 4403 / 4429 / 4400 / 4409 / 1001 (drain) / 1011 (error) |

Both transports draw from the **same hub** and enforce **byte-identical
Origin + connection-cap policy** (`apps/realtime/security.py`):

* **Origin** — no header (non-browser) allowed; same-origin allowed
  (`ws`/`wss` map to `http`/`https` for the comparison); else must be on the
  comma-separated `REALTIME_ALLOWED_ORIGINS` allowlist.
* **Connection caps** — per-node (10000), per-match (2000), per-IP (8) drawn
  from a process-wide `ConnectionLimiter` (fail-open). SSE and WS count
  independently (`sse:*` / `ws:*` keys) but share the node budget. Caps are
  checked **before** a subscription is created; a denied client consumes no hub
  state. The client IP honours `TRUSTED_PROXY_HOPS` (0 = ignore
  `X-Forwarded-For`), so spoofed headers cannot switch buckets.

### 3.5 Composition root

`config/asgi.py` registers a `_RealtimeLifespan` handler: on
`lifespan.startup` it builds the hub + broker (injecting the matches
implementations into `apps/realtime.wiring`) and starts `broker.run()`; on
`lifespan.shutdown` it stops the broker and awaits the run task. Without this,
every SSE/WS connect would fail with "realtime hub is not configured".

---

## 4. Throttling (`apps/core/throttling.py`)

* **Token buckets** per IP (and optional per API key via
  `RATELIMIT_KEY_HEADER`, disabled by default — no key issuance in scope).
  Scope rates: `default` (10 tokens/s, burst 120) and a stricter `deliveries`
  (2/s, burst 20). Backend: the `ratelimit` Redis role, or `in_process`
  (tests + the runtime degraded fallback).
* **Fail open** — if the bucket store is unavailable the request proceeds
  (availability over enforcement), with a per-process fallback bucket and a
  metric. `429` responses carry `Retry-After`.
* **Connection limiter** — a per-process, per-key counter with limits, used by
  the realtime transports' connection caps (§3.4).

---

## 5. Observability

Metrics (rendered by the in-process facade at `GET /metrics`) — the realtime and
cache families:

* **Cache:** `cache_hits_total` / `cache_misses_total` (namespaced),
  `cache_stale_served_total`, `cache_degraded_total`, `cache_invalidations_total`,
  `cache_purges_total`, `cache_build_seconds`, `cache_breaker_open`.
* **Realtime:** `realtime_connections`, `realtime_degraded`,
  `realtime_dropped_frames_total`, `realtime_gap_backfills_total`,
  `realtime_resyncs_total`, `realtime_cap_denied_total`, `realtime_coalesced_total`,
  `realtime_drains_total`, `realtime_licence_filtered_total`.

Alerts live in `ops/alerts/realtime_alerts.yml` (9 rules: `RealtimeDegraded`,
`RealtimeDroppedHigh`, `RealtimeGapBackfills`, `RealtimeResyncsHigh`,
`RealtimeCapDeniedHigh`, `RealtimeConnectionsNearCap`, `CacheBreakerOpen`,
`CacheDegraded`, `CacheMissRatioHigh`), each mapping to a runbook section in
`docs/RUNBOOKS.md`. The alert → metric → runbook contract is enforced by
`tests/phase_d/test_observability.py`.

---

## 6. Settings reference (Phase E)

| Setting | Default | Purpose |
|---|---|---|
| `REDIS_URL` / `CACHE_REDIS_URL` / `PUBSUB_REDIS_URL` / `RATELIMIT_REDIS_URL` | base / db 0 / db 3 / db 4 | Redis roles (§1) |
| `REDIS_SOCKET_TIMEOUT_SECONDS` | 0.25 | bounded Redis calls |
| `CACHE_SURROGATE_HEADER` | `Surrogate-Key` | CDN purge tags |
| `CACHE_PURGE_URL` | empty | CDN `PURGE` hook (empty = no CDN) |
| `TRUSTED_PROXY_HOPS` | 0 | client-IP trust model |
| `RATELIMIT_RATES` | default / deliveries | token-bucket scopes |
| `RATELIMIT_BACKEND` | redis | `in_process` for tests |
| `REALTIME_SSE_HEARTBEAT_SECONDS` | 15 | SSE keepalive |
| `REALTIME_ALLOWED_ORIGINS` | empty | cross-origin allowlist |
| `REALTIME_CONNECTION_CAP_PER_IP` / `_PER_MATCH` / `_PER_NODE` | 8 / 2000 / 10000 | connection caps |
| broker `poll_interval` | 1.0 s | degraded-mode poll cadence (constructor-injected) |
| `LIVE_RECONCILIATION_WINDOW_HOURS` | 24 | post-completion `immutable` + event-retention window |

---

## 7. Load budgets

`make test-loadtest-smoke` (CI) enforces acceptance #10 in-process
(`loadtests/smoke_gate.py`); `loadtests/k6/` has manual capacity scripts for a
deployed node:

| Budget | Gate | Typical measured |
|---|---|---|
| Warm cached `GET /api/v1/matches/live` p95 | ≤ 200 ms | ~0.6 ms (LocMem) |
| Realtime fan-out (publish → all subscribers) | ≤ 1 s | burst ~0.21 s, immediate ~0.4 ms |
