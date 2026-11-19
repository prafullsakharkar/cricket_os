# Phase I — Pre-launch validation: final report

Date: 2026-11-14 · Branch `phase-i` · Commits `68298ae`…`e18da66` (10 tasks) ·
Plan: `docs/superpowers/plans/2026-11-11-phase-i-pre-launch.md`

This is the brief's deliverable. It mirrors what was reported in the session
and records (a)–(g) below.

---

## (a) What was built and run

**Gates (decided first):** G1 provider gate and G2 environment gate both
**fail** (`docs/PROVIDER_DECISION.md` and `docs/STAGING_ACCESS.md` absent), so
Phase I ran **without a vendor adapter and without network access**, comparing
against the internal **ReplayAdapter** on the local single-node topology. G3
safety was built **first**: every load/chaos/DAST harness refuses any target
not on the explicit `TARGET_ALLOWLIST` (`backend/loadtests/safety.py`, empty default),
and every staged figure is tagged **MEASURED / ASSUMED / UNVERIFIED** with the
missing gate named.

| Task | Built | Evidence |
|---|---|---|
| 1 Provider readiness | Golden mapping, licensing policy config, redaction logging, degradation dry-runs | `reports/phase_i/task1_licensing_evidence.md` |
| 2 Shadow comparison | Compare/cards/report services, dated reports, metrics, alerts, launch-criteria hooks | `reports/shadow/*`; shadow tests |
| 3 Identity continuity | Linking batch (name/DoB scoring, cap), continuity test, idempotence + dry-run | `reports/phase_i/task3_identity_evidence.md`, `identity_coverage.md` |
| 4 Match de-duplication | Models + reference DDL; canonicalise + fixture hooks; reversible merge/split, selector exclusion, review admin | `tests/phase_i/test_match_*`; `reports/phase_i/` |
| 5 Load validation | G3 allow-list gate + harnesses + soak; capacity model | 7 reports `reports/phase_i/load/`; `docs/CAPACITY_MODEL.md` |
| 6 Game-day | 8 scenarios + 2 drills, alert sink, OPERATIONS guide, 2 new runbook sections | 10 reports `reports/game_day/`; `docs/OPERATIONS.md` |
| 7 Consumer integration | nginx proxy config, consumer-ladder script, Sunset/Link deprecation middleware + policy | `reports/phase_i/consumer_ladder.md`; `docs/API_DEPRECATION_POLICY.md` |
| 8 Security validation | Secret scan (CI), DAST-equivalent scan, SECURITY_REVIEW + PENTEST_SCOPE, pagination-schema fix | `reports/security/dast-20261113T201150Z.md`; `docs/SECURITY_REVIEW.md` |
| 9 Launch package | Launch plan, go/no-go, feature-flag inventory, risks update, roadmap block, this report | `docs/LAUNCH_PLAN.md`, `GO_NO_GO.md`, `FEATURE_FLAGS.md` |

**Run:** `make check` green end-to-end (lint, format, mypy 315 files,
import-linter, full suite, secret scan); `make test-phasei` **96.12% ≥ 95%**
(177 Phase I tests); `make test-security` **112 tests**. All 10 game-day
drills and the proxy ladder were run **for real** on the local stack.

## (b) Gates satisfied

| Gate | Outcome |
|---|---|
| G1 provider docs | **FAILS** — no licensed provider; all provider-facing numbers UNVERIFIED; shadow tooling ready to flip the moment a provider decision exists |
| G2 staging docs | **FAILS** — no staging environment; all performance/SLO numbers UNVERIFIED; drills re-runnable on staging |
| G3 harness safety | **SATISFIED** — every load/chaos/DAST harness refuses non-allow-listed targets (tests enforce this in CI) |

## (c) Measured vs targets (per §12 SLO)

| SLO | Target | Local measurement (MEASURED, UNVERIFIED) | Status |
|---|---|---|---|
| Read soak | p95 < 200 ms, err < 0.1% at 1× Growth | p95 **16.6 ms**, p99 70.3 ms, err 0.0% at **0.01× Growth** (≈1,500 RPS) | demonstrated locally; **unverified at scale** |
| Marquee spike | no 5xx, p95 < 500 ms, cache hit > 90% at 50k RPS | peak **200 RPS** (50k stated, never run); p95 **46.4 ms**, 0 5xx, 40% of the spike shed as 429s; cache-hit not client-observable → not-demonstrated | demonstrated for shedding; **50k figure not run** |
| Deliveries | p95 < 300 ms | p95 **60.5 ms** at 2 pages/s (the endpoint's throttle scope) | demonstrated locally |
| Realtime | event < 1 s, reconnect < 5 s | connect→headers p95 **23.3 ms**; 1M-connection figure **stated, not run** | demonstrated for reconnect; **1M not run** |
| Soak | ≥ 2 h incl. deploy + failover | **25 s smoke** only; deploy/failover drills UNVERIFIED | not-demonstrated |
| Restore (drill) | RTO ≤ 4 h, RPO ≤ 15 min | RTO **0.5 s**, RPO ~dump time | demonstrated locally (small topology) |

## (d) Shadow findings + identity/dedup outcomes

- **Shadow comparison** (against ReplayAdapter): deterministic, no
  auto-correction, mismatch categories (totals/wickets/extras/result/batting/
  bowling) with severity + alert contract. The comparison service is the
  launch gate's measurement machinery.
- **Identity continuity:** linking a corpus through the provider map leaves
  career stats identical (no double count), respects the DoB-mismatch cap,
  and is idempotent; the threshold is exercised on real local data
  (`reports/phase_i/task3_identity_evidence.md`).
- **Match de-dup:** order-independent, idempotent, no double-count (property
  tests); timezone/date-line, rescheduled, double-header, super-over and
  abandoned cases covered; merges are **reversible** with selector exclusion
  and a review admin; merged players answer **301**.
- **Game-day outcomes (9/10 sufficient, 1 honest UNVERIFIED):** provider
  outage, schema drift, wrong score, redis failover, failed migration,
  reconnect storm, expired credential, restore, derived rebuild — all pass
  locally; **replica loss is UNVERIFIED** (no db-replica service in the local
  topology — the missing pieces are named, not faked).
- **Consumer ladder:** 0 proxy-induced failures through the nginx config; the
  ladder exposed and the config fixed a real proxy bug (`Host` must pass
  through as `$http_host` or the app's same-origin WS check rejects).
- **Security scan:** secret scan clean; `check --deploy` on prod settings:
  **zero security.\* issues**; schemathesis found **contract drift only** (no
  exploitable vulnerability) — the one client-visible bug (list-envelope
  schema drift) was **fixed** and locked by a test.

## (e) Decisions where docs were silent or conflicting

| Conflict | Decision |
|---|---|
| Plan §8.6 vs `MATCH_DEDUPLICATION.md` §2 (missing match_number) | Resolved in favour of committed 4b: missing `match_number` = no evidence (full score); differing = 0.85 review-band, never silent merge |
| Load-test scale factors | Growth read RPS ≈ 1,500 (not 150) → soak scale factor 0.01× at 15 RPS |
| Deliveries throttling | Endpoint is deliberately 2/s (API_SPEC §7); harness defaults to 2 pages/s and records 429s separately (defence-in-depth) |
| Cache-hit > 90% | Not client-observable → recorded not-demonstrated, never faked |
| Outage fetcher exceptions | Must raise a **generic** exception (never `CircuitOpenError`) or the breaker never counts it |
| Breaker recovery | `success_threshold=1`; closes only from HALF_OPEN (driven past `recovery_timeout`) |
| Proxy `Host` header | `$http_host`, not `$host` (port-stripping breaks same-origin WS/SSE) |
| SSE "timeout after frames" | App idle (heartbeat > window), not proxy truncation |
| `pg_dump`/`pg_restore` | Run inside the postgres container (host 13.x refuses 17.x server) |
| Pagination schema | drf-spectacular declared the wrong envelope; `get_paginated_response_schema` fixes it (F-01) |
| `Sunset`/`Link` | Ships with an **empty registry**; additive-only; middleware never touches the body |

Full list in the SDD ledger `.superpowers/sdd/2026-11-11-phase-i-pre-launch/`.

## (f) Go / no-go summary

**NO-GO for production launch** — stated bluntly, every unproven item named:

1. Real provider behaviour (latency, coverage, schema stability, corrections,
   ID stability) — UNVERIFIED, replay-only.
2. Staging/production numbers — local 0.01× Growth only; 50k RPS and 1M
   connections stated, never run.
3. Production-scale SLO attainment (read p95, 99.9% availability, ingest lag).
4. Real on-call routing (local file sink only).
5. WAF / admin-edge controls — F-06 admin IP restriction, F-07 WAF open.
6. Read replicas / deploy + failover drills — UNVERIFIED.
7. The ≥ 2 h soak — not run (25 s smoke).
8. External pentest — planned, not run.
9. Deprecation-policy window and hypercare staffing — PROPOSED, not ratified.

Every LC-1…LC-8 criterion is **unproven** (replay/local only). Open licensing
items RSK-01/03/12 and the licensing checklist rows are **all open** (G1).
`docs/GO_NO_GO.md` holds the full per-criterion table with owners and residual
risk.

## (g) What could not be done, and what it needs

| Could not do | What it needs |
|---|---|
| Validate against a licensed provider | G1: `docs/PROVIDER_DECISION.md` (contract, coverage, rights) + a provider trial feed; the adapter contract kit + shadow tooling are ready |
| Validate on staging/production | G2: `docs/STAGING_ACCESS.md` naming the target; re-run the load/game-day/security evidence there |
| Run the 50k-RPS marquee and 1M-connection realtime figures | A staging/production topology + the k6/WS benches; locally they are stated, not faked |
| Demonstrate read replicas + deploy/failover drills | A replica topology (the scenario records exactly what is missing) |
| Run an external pentest | A deployment to test + a vendor; `docs/PENTEST_SCOPE.md` is the agreed scope (planned, not run) |
| Close the admin-IP-restriction and WAF gaps | The edge/reverse-proxy platform (allow-list at the edge); UNVERIFIED until then |
| Ratify PROPOSED thresholds (LC caps, deprecation window, error budget, hypercare) | Product-owner sign-off |

**The launch package is ready the day both gates are met:** `LAUNCH_PLAN.md`
(dark → canary → staged → full, with abort/rollback), `FEATURE_FLAGS.md` (the
flip sequence), `GO_NO_GO.md` (the re-run), and the drill evidence that
converts each UNVERIFIED row into a staging measurement.
