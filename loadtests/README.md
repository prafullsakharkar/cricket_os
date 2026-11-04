# Load tests — Phase E (acceptance #1/#10)

Two layers:

1. **`smoke_gate.py` — the CI gate** (`make test-loadtest-smoke`). An
   in-process Python check of the two acceptance budgets, deterministic and
   hermetic. This is what CI enforces.
2. **`k6/` — manual capacity scripts.** Load generators to run against a
   deployed node for real concurrency numbers. k6 is **not** installed in CI;
   these are for manual capacity runs.

Both measure the same budgets (acceptance #10):

| Budget | Gate |
|---|---|
| Warm cached `GET /api/v1/matches/live` p95 | ≤ 200 ms |
| Realtime fan-out (publish → every subscriber) | ≤ 1 s |

## `make test-loadtest-smoke` (CI gate)

```bash
make test-loadtest-smoke
# or: uv run python loadtests/smoke_gate.py
```

What it does (all in one process, under `config.settings.test`):

- **Migrates** `DATABASE_URL` (idempotent; a no-op on an already-migrated
  database, the schema bootstrap on a fresh CI database).
- **Cached reads** — seeds 20 live matches (each with two teams, tagged
  `Smoke *`), warms the read-through cache with one request, then measures 100
  warm `GET /api/v1/matches/live` requests through `django.test.Client()`
  (full stack: middleware, token-bucket throttle, cache hit, DRF rendering).
  Reports p50/p95/p99/max; fails when p95 > 200 ms.
- **Fan-out** — a real `Hub` with the real coalesce ticker
  (`flush_interval` 0.2 s), 100 subscribers on one match, a 1000-frame burst
  (coalesced into `batch` frames) plus an immediate-correction flush (never
  waits for the tick). Fails when publish → last-subscriber delivery > 1 s.
- **Cleans up its own rows** (`Smoke *` teams + matches), so a crash can never
  wedge a re-run. Exits non-zero on any breach.

Local run needs the usual services (PostgreSQL for the ORM reads; the cache is
in-memory LocMem under the test settings, so no Redis is required):

```bash
make up        # PostgreSQL + Redis
make test-loadtest-smoke
```

## Manual k6 capacity scripts

Requires [k6](https://grafana.com/docs/k6/latest/get-started/installation/)
≥ 0.52 (the core `k6/websockets` module). Run against a deployed node that is
actively feeding a live match through the ingestion path.

### `k6/cache_reads.js` — cached-read capacity

```bash
K6_BASE_URL=http://localhost:8000 k6 run loadtests/k6/cache_reads.js
```

20 VUs for 30 s of warm reads; threshold `p(95) < 200 ms` on the measured
scenario. Env: `K6_BASE_URL`, `K6_VUS`, `K6_DURATION`.

### `k6/sse_fanout.js` — realtime fan-out capacity

```bash
K6_MATCH=<live-match-uuid> k6 run loadtests/k6/sse_fanout.js
```

Opens `STREAMS` (default 50) concurrent connections to one live match and
asserts every stream receives its first frame within 1 s (the server's ≤1 s
coalesce window) and that no connection fails. Env: `K6_BASE_URL`, `K6_MATCH`
(required), `STREAMS`, `K6_DURATION`.

> **Why WebSocket?** Vanilla k6 cannot stream SSE (the `xk6-sse` extension is
> archived). The WebSocket transport is the byte-identical twin of the SSE feed
> — same hub, same frames, same coalescing (parity contract,
> `docs/FRONTEND_CONTRACT.md` §1) — so fan-out measured over WS is the fan-out
> a browser gets over SSE.

The server is one-way; these scripts only consume. To produce load on the
write side (event publication), feed deliveries through the live ingestion
path while the scripts run.

## Interpreting the smoke gate numbers

A healthy run looks like (LocMem warm reads are sub-millisecond; the budget
guards against regressions like an N+1 sneaking into the render path or a
serialization blow-up):

```
cold build                       1 request      58.48ms
cached reads (warm)              n= 100  p50=    0.49ms  p95=    0.55ms  p99=    0.87ms  max=    0.88ms
  budget: p95 <= 200 ms -> PASS
fan-out burst (1000 frames)        208.65ms
fan-out correction (immediate)       0.38ms
  budget: <= 1000 ms -> PASS

load smoke gate: PASS
```

The burst number is dominated by the 0.2 s coalesce flush (by design — one
batch per client per tick); the correction number is the pure immediate-flush
path (ledger #6).
