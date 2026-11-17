# CAPACITY_MODEL.md

Capacity model for the Phase I pre-launch validation (plan Task 5 §9.3).
Every figure is tagged `MEASURED | ASSUMED | UNVERIFIED` with the environment,
the exact command and the date. Per-SLO rows map the §12 targets
(`CRICKET_PERFORMANCE_AND_SCALING.md` §12) to the local measurement and state
what a failure implies.

> **Gate G2 FAILS** — `docs/STAGING_ACCESS.md` is absent, so there is **no
> staging environment**. All numbers below come from the **local
> production-topology stack**, are tagged `UNVERIFIED` against production, and
> are **not** evidence of SLO attainment. No production-like target was ever
> hit (gate G3: every harness refuses non-allow-listed targets).

## 1. Measured topology (what these numbers mean)

| Component | Configuration |
|---|---|
| App | 1 ASGI process (`uvicorn config.asgi:application`, 127.0.0.1:8000) |
| Database | docker-compose PostgreSQL (single primary; no replica) |
| Cache / throttle / hub | 1 Redis (7.x) |
| Worker | 1 Celery worker (local) |
| Client | `urllib`-based harnesses in `backend/loadtests/` (single-process, paced) |

The local topology is **smaller than the planning stage** in every dimension:
1 app node vs 2–4 at Growth, no CDN, no read replica, no connection pool at
scale, and a synthetic ReplayAdapter-shaped corpus instead of a licensed
provider feed. **Extrapolation of these numbers to production is an ASSUMED
estimate, not a guarantee** (`CRICKET_PERFORMANCE_AND_SCALING.md` §1 figures are
themselves planning assumptions).

## 2. Per-SLO results

SLO targets are from `CRICKET_PERFORMANCE_AND_SCALING.md` §12. Status values:
`demonstrated` / `not-demonstrated` / `failed` (plan §9.3). All rows are
`UNVERIFIED` (G2). Evidence: `reports/phase_i/load/<name>-<timestamp>.json`.

| # | §12 SLO | Target | Local measurement (MEASURED, UNVERIFIED) | Status | What a failure implies / implication |
|---|---|---|---|---|---|
| 1 | Read soak | p95 < 200 ms, error < 0.1% at 1× Growth RPS | p50 13.2 ms, **p95 16.6 ms**, p99 70.3 ms, error 0.0%, at 15 RPS = **0.01× Growth** (≈1,500 RPS) | demonstrated (locally) | read p95 > 200 ms locally would mean the CDN/replica tiers in the capacity plan are not optional; re-run on staging at 1× Growth before launch |
| 2 | Marquee spike | ramp 0→50k RPS in 60 s on one match: no 5xx, p95 < 500 ms, cache hit > 90% | peak **200 RPS** (the 50k figure is **not** run locally — stated, not faked); p95 **46.4 ms**, p99 52.0 ms, **0 5xx**, **526 requests (40% of the spike) throttled 429** | demonstrated for no-5xx + p95; **cache hit > 90% not-demonstrated** (not client-observable) | 429s under the default throttle scope (10/s, 120 burst) prove the origin would shed marquee load — the cache/CDN tier must absorb the spike, not the app; re-verify at the real 50k figure on staging |
| 3 | Deliveries feed | cursor pagination under spike: p95 < 300 ms | page_size 50; p50 46.9 ms, **p95 60.5 ms**, p99 131.8 ms, error 0.0%, at 2 pages/s (the `deliveries` throttle scope caps origin reads at 2/s — API_SPEC §7) | demonstrated (locally) | p95 ≥ 300 ms would flag the cursor query plan / partition-key index; re-run on staging with a production-sized `delivery` table |
| 4 | Realtime | event latency < 1 s, reconnect < 5 s | connect→headers **p95 23.3 ms**, p99 23.3 ms, error 0.0%, 6 cycles @1/s; subscriber counts are low hundreds — the §12 **1M-connections figure is stated, not run locally** | demonstrated for reconnect (locally); 1M-connection event-latency not-demonstrated | reconnect p95 ≥ 5 s would flag the SSE hub/coalescer or the CDN edge; publish→delivery latency is covered by the in-process realtime suite |
| 5 | Ingestion throughput | 10× peak provider rate: no DLQ growth, lag < 30 s | **196.6 fixtures/s**, per-fixture p95 25.4 ms, **DLQ growth 0** (depth 0 → 0), lag estimate **0.025 s**, burst of 5 fixtures | demonstrated (locally) | throughput below 10× peak or DLQ growth would flag batching/backpressure; re-run with the real provider corpus on staging |
| 6 | Correction burst | simultaneous corrections: no lock-contention errors | 8 corrections across 4 threads; **0 lock errors**, p95 **745.5 ms**, Tier-1 rebuild OK (12 deliveries, revisions valid) | demonstrated (locally) | lock errors would flag the advisory-lock strategy (deadlock window); re-run on staging at real correction volume |
| 7 | Soak (read + realtime + ingest) | plan target ≥ 2 h locally incl. deploy + replica-failover drills | **25 s smoke run** (not the ≥ 2 h target): read p95 16.7 ms, error 0.0%, 3 ingest ticks; **deploy drill UNVERIFIED** and **failover drill UNVERIFIED** (see §3) | not-demonstrated (local smoke only) | a full multi-hour soak that holds the SLOs across a deploy + failover is the strongest local evidence; the staging soak is the launch gate |

## 3. Soak drills — recorded honestly (plan §9.4)

| Drill | Attempted? | Result | Missing piece |
|---|---|---|---|
| Mid-soak deploy (restart ASGI app) | Yes (t=12 s in smoke) | `UNVERIFIED` | no running `docker-compose` `app` service in this environment — the local stack is a directly-run ASGI process; a real deploy restart must be measured on staging |
| Replica failover (promote/demote `db-replica`) | Yes (t=18 s in smoke) | `UNVERIFIED` | `infrastructure/compose/compose.yml` has **no `db-replica` service** (pg_basebackup bootstrap + `primary_conninfo` absent) and the local Postgres is not configured for streaming replication (`wal_level`/`max_wal_senders`). The attempt cannot run here and is **not faked**. |

## 4. Tagged figures

All numbers in §2 are `MEASURED` (on the §1 topology), `UNVERIFIED` (G2: no
staging environment, `docs/STAGING_ACCESS.md` absent). Extrapolation to
production is `ASSUMED`. Commands and dates:

| Figure | Command (env) | Date (UTC) | Report |
|---|---|---|---|
| Read soak p95 16.6 ms | `TARGET_ALLOWLIST=localhost:8000 cd backend && uv run python loadtests/harness_read_soak.py --target localhost:8000 --duration 10 --rate 15` | 2026-11-13 12:41 | `reports/phase_i/load/harness_read_soak-20261113T124108Z.json` |
| Marquee p95 46.4 ms / 0 5xx / 526×429 | `cd backend && uv run python loadtests/harness_marquee_spike.py --target localhost:8000 --match-id 01a1d48f-b1a9-7000-b091-b296048c7f52 --rate 200 --ramp 5 --hold 5` | 2026-11-13 12:35 | `harness_marquee_spike-20261113T123535Z.json` |
| Deliveries p95 60.5 ms | `cd backend && uv run python loadtests/harness_deliveries_feed.py --target localhost:8000 --match-id 01a1d48f-b1a9-7000-b091-b296048c7f52 --innings 1 --duration 8 --rate 2` | 2026-11-13 11:18 | `harness_deliveries_feed-20261113T111854Z.json` |
| Realtime reconnect p95 23.3 ms | `cd backend && uv run python loadtests/harness_realtime_reconnect.py --target localhost:8000 --match-id 01a1d48f-b1a9-7000-b091-b296048c7f52 --duration 6 --rate 1` | 2026-11-13 11:34 | `harness_realtime_reconnect-20261113T113404Z.json` |
| Ingest 196.6 fixtures/s, DLQ 0 | `TARGET_ALLOWLIST=localhost:55435 cd backend && uv run python loadtests/harness_ingest_throughput.py --target localhost:55435 --fixtures 5` | 2026-11-13 10:33 | `harness_ingest_throughput-20261113T103358Z.json` |
| Correction burst 0 lock errors, p95 745.5 ms | `TARGET_ALLOWLIST=localhost:55435 cd backend && uv run python loadtests/harness_correction_burst.py --target localhost:55435 --workers 4 --burst 8` | 2026-11-13 10:56 | `harness_correction_burst-20261113T105647Z.json` |
| Soak smoke 25 s + drills | `cd backend && uv run python loadtests/soak.py --target localhost:8000 --match-id 01a1d48f-b1a9-7000-b091-b296048c7f52 --duration 25 --rate 10 --rt-rate 1 --ingest-every 8 --deploy-after 12 --failover-after 18` | 2026-11-13 11:35 | `soak-20261113T113507Z.json` |

## 5. Findings that feed launch planning

1. **The default throttle scope (10/s, 120 burst per IP) caps a marquee spike at
   the origin**: 40% of the 200-RPS spike returned 429. That is correct
   defence-in-depth behaviour (API_SPEC §7), not a defect — but the capacity
   plan must assume the CDN/cache tier absorbs marquee fan-out, because the app
   will not. Re-verify the real 50k figure behind a CDN on staging.
2. **Cache hit ratio is not client-observable**: the > 90% cache-hit criterion
   (§12) is `not-demonstrated` from a black-box client. Surrogate-key purge
   correctness is covered by the cache suite; hit-ratio accounting needs the
   CDN/origin metrics endpoint on staging.
3. **Replica failover is infrastructure debt, not a code gap**: no `db-replica`
   compose service and no WAL-level replication config exist in this repo's
   local stack. The soak records this honestly instead of faking a number.
   Standing this up is a pre-launch infra task (roadmap).

## 6. Extrapolation caveat (ASSUMED)

Local single-node measurements at 0.01×–0.13× of Growth scale do **not**
guarantee production behaviour at 1×–50k RPS. The capacity plan's node counts,
CDN tier and replica pool are planning assumptions; the numbers above bound the
**per-request cost of a single node** (read p95 ~17 ms, deliveries p95 ~61 ms,
reconnect ~23 ms, correction ~745 ms at small concurrency) and nothing more.
The staging soak (Task 6/launch gate) is where SLO attainment is actually
measured.
