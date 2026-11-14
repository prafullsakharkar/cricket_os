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

---

## Phase I Task 5 — staged performance-validation harnesses (`loadtests/harness_*.py`, `soak.py`)

These are the §12 load scenarios (`docs/CRICKET_PERFORMANCE_AND_SCALING.md`
§12) run against the **local production-topology stack** (gate G2 FAILS → no
staging; every number is `UNVERIFIED`). CI runs **only** the safety tests
(`tests/phase_i/test_harness_safety.py`); the harnesses are manual/long.

### Gate G3 — the target allow-list (built first)

Every harness refuses to run unless its target is on `TARGET_ALLOWLIST`
(comma-separated `host:port`), which is **empty by default**:

```bash
# refuse (default, empty allow-list) — exits 1
uv run python loadtests/harness_read_soak.py --target localhost:8000

# allow a target for this run
TARGET_ALLOWLIST=localhost:8000 uv run python loadtests/harness_read_soak.py \
    --target localhost:8000 --duration 60 --rate 20
```

`--dry-run` validates inputs and prints the plan **without any request and
without needing an allow-list entry**:

```bash
uv run python loadtests/harness_read_soak.py --target localhost:8000 --dry-run
```

Production-looking hosts are refused unless explicitly listed, and
`docs/STAGING_ACCESS.md` is the only document allowed to name a real
environment's target.

### Harnesses

Start the stack first (PostgreSQL + Redis + the ASGI server):

```bash
make up
uv run uvicorn config.asgi:application --host 127.0.0.1 --port 8000   # terminal 1
```

All HTTP harnesses share `--target host:port`, `--duration`, `--rate`,
`--dry-run`; reports land in `reports/phase_i/load/<name>-<timestamp>.json`
tagged with env / instances / command / date and the §12 SLO target.

| Harness | Scenario (§12) | Run (allow-listed) | Dry-run |
|---|---|---|---|
| `harness_read_soak.py` | read soak @ 1× Growth (local: scaled down) | `uv run python loadtests/harness_read_soak.py --target localhost:8000 --duration 60 --rate 20` | `--target localhost:8000 --dry-run` |
| `harness_marquee_spike.py` | spike on one match (local: hundreds, not 50k RPS) | `uv run python loadtests/harness_marquee_spike.py --target localhost:8000 --match-id <uuid> --rate 200 --ramp 30 --hold 30` | `--target localhost:8000 --dry-run` |
| `harness_deliveries_feed.py` | cursor pagination under load | `uv run python loadtests/harness_deliveries_feed.py --target localhost:8000 --match-id <uuid> --innings 1 --rate 2` | `--target localhost:8000 --dry-run` |
| `harness_realtime_reconnect.py` | SSE connect/reconnect churn | `uv run python loadtests/harness_realtime_reconnect.py --target localhost:8000 --match-id <uuid> --rate 1` | `--target localhost:8000 --dry-run` |
| `harness_ingest_throughput.py` | replay ingest throughput (in-process, rolled back; target = Postgres) | `TARGET_ALLOWLIST=localhost:55435 uv run python loadtests/harness_ingest_throughput.py --target localhost:55435 --fixtures 100` | `--target localhost:55435 --dry-run` |
| `harness_correction_burst.py` | simultaneous corrections (in-process, rolled back) | `TARGET_ALLOWLIST=localhost:55435 uv run python loadtests/harness_correction_burst.py --target localhost:55435 --workers 8 --burst 40` | `--target localhost:55435 --dry-run` |
| `soak.py` | read soak + realtime + ingest ticks; deploy & failover drills | `uv run python loadtests/soak.py --target localhost:8000 --match-id <uuid> --duration 7200 --rate 20` | `--target localhost:8000 --dry-run` |

Notes:

- **`--match-id` is optional for `--dry-run`** (required for real runs). The
  seeded load-fixture match used for local evidence is
  `01a1d48f-b1a9-7000-b091-b296048c7f52`.
- The **deliveries endpoint is throttled at 2/s** (API_SPEC §7), so
  `harness_deliveries_feed.py` defaults to 2 pages/s; a higher `--rate` trips
  the throttle itself, which is recorded honestly (429s are not hidden).
- `soak.py` attempts a **mid-soak deploy restart** and a **replica failover**;
  in this environment both record `UNVERIFIED` with the exact missing piece
  (no running compose `app` service; no `db-replica` service / no streaming
  replication config) — never faked. The plan's local soak target is ≥ 2 h.
- The two in-process harnesses wrap their work in rolled-back transactions and
  clean up their seed rows; the dev database is left untouched.
- Results and the per-SLO model: `docs/CAPACITY_MODEL.md` (all numbers
  `MEASURED` on the local topology, `UNVERIFIED` against production).
