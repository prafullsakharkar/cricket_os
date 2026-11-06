# CRICKET_PERFORMANCE_AND_SCALING.md

Performance and scaling strategy for the planning capacity scenarios, with
cache TTLs, partitioning, read replicas, real-time fan-out, spike behaviour,
and a load-testing plan. All figures are **planning assumptions**, not
guarantees or measured results.

---

## 1. Capacity scenarios (assumptions)

| Stage | DAU | Peak RPS (read) | Live matches | Deliveries/day | Notes |
|---|---|---|---|---|---|
| Initial | 10,000 | ~150 | a few | ~50k | Single app node + DB + Redis |
| Growth | 100,000 | ~1,500 | tens | ~500k | 2–4 app nodes, read replica, CDN |
| High | 1,000,000 | ~15,000 | hundreds | ~5M | Autoscaling, replica pool, partition pruning, aggressive CDN |
| Tournament peak | 1,000,000+ | **50k+** bursts | 1–2 hot matches | spikes | Thundering herd on a marquee match |

The dominant load is **read-heavy** (live scorecards polled every few seconds),
not write-heavy (a match produces ~300–600 deliveries/day/format).

---

## 2. Data volume & the case for partitioning

`delivery` is the only table that grows without bound (order of **10⁸–10⁹ rows**
at global scale over years). Everything else is comparatively small:

- RANGE-partition `delivery` by `match_date` (monthly or yearly). Benefits:
  partition pruning on time-bounded queries, cheap archival/detach of old
  seasons, smaller indexes, faster VACUUM.
- A maintenance job pre-creates upcoming partitions.
- **Do not** over-partition (thousands of tiny partitions hurt planning);
  yearly partitions with monthly splitting only for very hot years is a
  reasonable default.

`player_career_stat`, `leaderboard`, `tournament_standings`, `player_ranking`
are precomputed and small → no partitioning.

---

## 3. Indexing & query optimization

- B-tree on every filtered/joined FK (`match.series_id`, `delivery.innings_id`,
  `player_match_stat.player_id`, …).
- **Composite/covering** indexes for hot paths:
  - `match (status, start_time_utc DESC)` for live lists.
  - `delivery (match_date, innings_id, delivery_seq)` unique for the current
    revision of the feed (`ux_delivery_current ... WHERE is_current`), plus
    `ux_delivery_revision` for the full history.
  - `player_ranking (gender, format_type, category, rank, as_of_date DESC)`.
- **Partial indexes**: `WHERE NOT resolved` (ingestion errors),
  `WHERE provider_*_id IS NOT NULL`.
- **Trigram GIN** for name search (`player.full_name`, `team.name`).
- Avoid `SELECT *`; selectors project only needed columns.
- Use `EXPLAIN (ANALYZE, BUFFERS)` in CI for a fixed set of hot queries
  (regression gate: no plan may introduce a seq scan on `delivery`).

---

## 4. Connection pooling

- **PgBouncer** in transaction mode between app/Celery and PostgreSQL.
- Django `CONN_MAX_AGE=0` with PgBouncer (or persistent connections with
  `CONN_MAX_AGE` when pooling server-side); separate pools for web and workers.
- Celery workers use a bounded pool; ingestion bursts must not exhaust the web
  pool.

---

## 5. Caching strategy (Redis + CDN)

### 5.1 Recommended TTLs (mirroring observed freshness tiers)

| Data | Redis TTL | HTTP `Cache-Control` | Rationale |
|---|---|---|---|
| Live match list | 3–5 s | `max-age=5, swr=5` | Observed Cricbuzz `max-age=5` |
| Live scorecard | 3–5 s | `max-age=5, swr=5` | Ball-level freshness |
| Live commentary feed | 5 s | `max-age=5` | Observed `max-age=5` |
| Over-by-over | 5 s | `max-age=5` | Same tier |
| Completed match scorecard | 5–15 min | `max-age=300, s-maxage=3600`, strong `ETag`; `immutable` only after the post-completion reconciliation window (~24 h) | Frozen after completion, but corrections can still land |
| Fixtures / schedule | 30–60 s | `max-age=30, swr=60` | Observed `max-age=26` |
| Points table | 20–30 s | `max-age=20, swr=20` | Observed `max-age=22` |
| Series statistics | 15–20 s | `max-age=20, swr=20` | Observed `max-age=1` (recompute-on-read) |
| Rankings | 60 s | `max-age=60` | Provider-supplied, infrequent change |
| Player profile | 5 min | `max-age=300, swr=600` | Observed `max-age=5` (chose longer for stable fields) |
| Historical statistics / records | 10–60 min | `max-age=600, s-maxage=3600` | Immutable-ish |
| Reference data (teams/venues) | 1 h | `max-age=3600` | Rarely changes |

**State-dependent match caching (decision).** Match sub-resources take their TTL
from **match state**: live → `max-age=5, swr=5`; completed → `max-age=300,
s-maxage=3600` + strong `ETag`; `immutable` only after a configurable
**post-completion reconciliation window** (~24 h). This resolves the apparent
conflict between the API spec's flat `max-age=5` (which is the *live* value) and
the long TTL needed for completed content. A correction purges by entity tag and
changes the `ETag`.

**Invalidation:** key by entity (`match:{id}:scorecard`, `series:{id}:points`,
`player:{id}:stats`) and invalidate a **targeted key set** on the relevant event
(`delivery.appended` → match keys; `match.completed` → series/leaderboard keys).
Use Redis `SCAN`-free invalidation via key-versioning (bump a `match:{id}:ver`
counter that is part of the cache key) to avoid wildcard deletes.

### 5.2 CDN

- Public GETs are CDN-cached with `s-maxage` + `stale-while-revalidate`, which
  absorbs the marquee-match spike far better than any app tier.
- Serve `304` via a **strong** `ETag` on completed/near-static resources; live
  payloads use a weak `ETag` so they revalidate frequently.
- Purge by tag (entity id) on completion/correction; mark completed resources
  `immutable` only after the post-completion reconciliation window (~24 h).

---

## 6. Materialized views & precomputed aggregates

- Use **precomputed tables** (`player_career_stat`, `team_stat`,
  `tournament_standings`, `leaderboard`, `head_to_head`, `form_trend`) rather
  than live aggregation over millions of deliveries.
- A small number of **materialized views** can back read-only leaderboards where
  point-in-time snapshots suffice; refresh `CONCURRENTLY` on a schedule.
- **Tiered incremental updates.** Appending a delivery runs **Tier 1** only
  (innings summary, two batting/bowling rows, FOW/partnership/over, then cache
  invalidation and the realtime event) — it does **not** touch career/team tables.
  **Tier 2** (career/team/standings/leaderboard/H2H/form/fantasy) runs on
  `match.completed`, `correction.applied` and scheduled jobs, never per delivery.

---

## 7. Read replicas

- Route **read-only selectors** to a replica pool; keep writes and
  correctness-critical reads (e.g. right after a write, or admin corrections) on
  the primary.
- Replication lag tolerance: live pages tolerate ~1 s lag; after a correction,
  the affected match is pinned to the primary until the replica catches up.
- At Growth, 1 replica; at High, 2–3 replicas behind a proxy
  (pgpool/HAProxy) with lag-aware routing.

---

## 8. Real-time fan-out

- **Topology:** `scoring engine → Redis pub/sub channel match:{id} (role DB 3) →`
  each web node's broker → the shared local hub → the connected WS/SSE clients
  (implemented in Phase E; see [REALTIME_AND_CACHING.md](REALTIME_AND_CACHING.md)).
- **Hot-match problem:** one match may have millions of subscribers. Mitigations:
  1. Prefer **SSE** for one-way scoreboards (cheaper than WS).
  2. **Delta events** (a single delivery JSON, not the full scorecard).
  3. **Coalescing**: ordinary deltas batch into one ≤1 Hz `batch` frame per
     client; corrections/terminal transitions flush immediately.
  4. **Backpressure**: bounded per-subscriber queues (64 frames); a slow
     consumer gets drops, not memory — recovery is the seq-based replay on
     reconnect.
  5. **CDN + short-poll fallback**: clients without WS/SSE poll the cached
     endpoint (already served by CDN) — the system degrades gracefully.
  6. Horizontal scale of the pub/sub tier; sticky-free because all nodes
     subscribe to the same Redis channels.
- **Degradation:** Redis pub/sub is best-effort. On transport failure a node's
  broker switches to **degraded polling** of the durable `match_event_log`
  (`realtime_degraded`), and recovers automatically when Redis returns. A Redis
  outage degrades freshness, never availability, and the log guarantees no event
  is permanently lost (gap/backfill replays missing runs via `events_since`).
- **Event log:** the durable per-match `match_event_log` (in `matches`) carries a
  monotonic **per-match** `seq` (distinct from the per-innings `delivery_seq`)
  and is the **source of truth** for both caching and realtime. Events are
  retained for a configurable window after completion (default 24 h), then
  purged.
- **Reconnection:** on reconnect the client sends its last seen `seq`
  (`Last-Event-ID`/`from_seq` for SSE, `from_seq` for WS); the server replays
  from `match_event_log`. If the position predates the retained range it delivers
  `resync_required` → REST refetch. No gaps (the REST snapshot's `meta.event_seq`
  handoff makes it exactly-once).
- **Connection caps:** per-IP 8, per-match 2000, per-node 10000 (fail-open,
  in-process); Origin allowlist rejects cross-site streams. Both are checked
  before a subscription is created.
- **Budgets:** `make test-loadtest-smoke` enforces p95 cached read ≤ 200 ms and
  fan-out ≤ 1 s in CI (`loadtests/`); k6 scripts in `loadtests/k6/` cover
  manual capacity runs.

---

## 9. Behaviour during a sudden traffic spike

1. CDN serves cached responses for the polled endpoints (absorbs the bulk).
2. Redis serves hot entity reads for cache misses.
3. Read replicas scale horizontally; connection pool caps protect the primary.
4. Rate limiting protects the app tier; `429`s return `Retry-After`.
5. Realtime fan-out coalesces to ≤1 update/s/client.
6. If the provider is slow, the platform serves last-known-good (`stale=true`)
   rather than blocking — a **breadth-first freshness** policy.
7. Circuit breakers prevent provider slowness from cascading.

---

## 10. API rate limiting & request deduplication

- Token-bucket per IP and per API key; separate, stricter buckets for
  `deliveries`/`commentary`.
- `ETag`/`If-None-Match` for dedup; CDN collapses identical concurrent requests
  (request collapsing).

---

## 11. Database migration strategy

- Django migrations, **expand/contract** pattern for zero-downtime:
  1. Expand: add nullable column / new table; backfill asynchronously.
  2. Dual-write/backfill; verify.
  3. Contract: drop old column/table in a later release.
- Never combine a large backfill and a schema change in one blocking migration;
  use `CONCURRENTLY` for index creation (`AddIndexConcurrently`).
- Migrations are tested against a production-sized fixture in CI.

---

## 12. Load-testing plan

| Test | Tool | Scenario | Success criteria |
|---|---|---|---|
| Read soak | k6/Locust | 1× Growth RPS for 1 h | p95 < 200 ms, error < 0.1% |
| Marquee spike | k6 | ramp 0→50k RPS in 60 s on one match | no 5xx, p95 < 500 ms, cache hit > 90% |
| Deliveries feed | k6 | cursor pagination under spike | p95 < 300 ms |
| Realtime | WebSocket bench | 1M connections across N nodes | < 1 s event latency, reconnect < 5 s |
| Ingestion throughput | pytest-benchmark | 10× peak provider rate | no DLQ growth, lag < 30 s |
| Write burst | Locust | simultaneous corrections | no lock contention errors |

**Latency & availability targets (SLOs):**

| SLO | Target |
|---|---|
| Live scorecard read latency | p95 ≤ 200 ms (cached), ≤ 500 ms (uncached) |
| Availability (public reads) | 99.9% monthly |
| Ingestion lag (live match) | ≤ 10 s median, ≤ 60 s p99 |
| Realtime event latency | ≤ 1 s p95 |
| Error rate | < 0.1% of requests |
| Data freshness alerting | alert if lag > 60 s |

---

## 13. What we deliberately do *not* add yet

- **Kafka** — Redis pub/sub + Celery suffice at these volumes; Kafka's
  operational cost is unjustified without a measured need for durable,
  replayable, multi-consumer streams.
- **Kubernetes** — a small team on managed containers/VM autoscaling is simpler;
  revisit only when multi-region or heavy autoscaling is required.
- **Dedicated search cluster** — PostgreSQL trigram first.
- **Microservices** — see [CRICKET_BACKEND_ARCHITECTURE.md](CRICKET_BACKEND_ARCHITECTURE.md).
