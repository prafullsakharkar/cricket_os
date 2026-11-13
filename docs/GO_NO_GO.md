# Go / no-go

The go/no-go package for production launch. Consumes `LAUNCH_CRITERIA.md`
(thresholds + owners) and `LAUNCH_PLAN.md` (rollout). **Verdict: NO-GO for
production launch** — the two gates fail, so every criterion is `unproven` and
launching would be dishonest. This is the correct outcome of Phase I: the
phase validated the *tooling and the local behaviour*; it did not and cannot
validate a licensed provider or a staging environment.

Gate context: `docs/PROVIDER_DECISION.md` (G1) and `docs/STAGING_ACCESS.md`
(G2) are both **absent**. No vendor adapter, no network, no staging — every
measured value below is from the local single-node topology against the
**ReplayAdapter** and is UNVERIFIED for production.

## 1. Launch criteria status

| # | Criterion | Status | Evidence | Owner | Residual risk |
|---|---|---|---|---|---|
| LC-1 | Zero unreconciled result mismatches (N=50) | **unproven** (replay corpus; N=50 not reached) | `reports/shadow/*.md`; `shadow_mismatches_total{category="result"}` | Data steward | Real provider result semantics may differ; N=50 on staging is the gate |
| LC-2 | Ingestion lag p99 ≤ 60 s | **unproven** (replay latency ≈ 0) | `shadow_ingest_latency_seconds` | Platform eng | Real provider network/SLA latency unknown |
| LC-3 | Shadow DLQ flat over 7 days | **unproven** | `IngestionError` count, `stage="shadow_compare"` | Ops | Real payload volume/quality unknown |
| LC-4 | Mismatch rate by category below caps | **unproven** | `shadow_mismatches_total{category}` / `shadow_comparisons_total` | Data steward | Caps are PROPOSED (not ratified) + replay-only |
| LC-5 | Coverage gaps ≤ 1% | **unproven** | `shadow_coverage_gaps_total` | Ops | Real coverage depends on the provider contract |
| LC-6 | Unresolved identity ≤ 5% | **unproven** | `link_provider_identities --dry-run`; `shadow_unresolved_identity_total` | Data steward | Real provider ID stability unknown |
| LC-7 | Corrections p99 ≤ 5 min | **unproven** (no provider revision timestamp in replay) | `shadow_correction_lag_seconds` | Platform eng | Real correction cadence unknown |
| LC-8 | Schema drift zero over N passes | **unproven** | `shadow_schema_drift_total`; adapter contract kit | Platform eng | Real provider schema changes unknown |

Every criterion is `unproven`, not `met`: per `LAUNCH_CRITERIA.md` §3, a
criterion may only be `met` with **ratified** thresholds AND **staging/real
provider** evidence. Thresholds marked PROPOSED in `LAUNCH_CRITERIA.md` have
not been ratified by their owners.

## 2. Open licensing items (gate G1 — all open)

From `docs/CRICKET_RISKS_AND_ASSUMPTIONS.md` §2 (licensing dependencies):

| Item | State | Blocking |
|---|---|---|
| Managed provider contract (scores, ball-by-ball, stats) | **Open — must verify** (RSK-01) | Live launch |
| Commentary rights | **Open — likely separately licensed** (RSK-03) | `/commentary` |
| Official ICC rankings display rights | **Open — verify** (RSK-12) | `/rankings` |
| DLS implementation licence / provider par scores | **Open — do not self-implement** | DLS results |

## 3. Known limitations (state plainly)

- **Coverage gaps** — some domestic/associate matches missing (RSK-02);
  capability-driven UI marks data unavailable rather than faking it.
- **Computed-vs-official statistics** — aggregates are computed from stored
  deliveries (derived-from-source); they may differ from provider-official
  figures and are labelled `derived` where the API exposes them.
- **Rankings** — provider-gated (RSK-12); the `/rankings` surface requires
  the licensing check.
- **Commentary** — deferred; not in scope until licensed.

## 4. Every unproven item (be blunt — do not round up)

1. **Real provider behaviour** — latency, coverage, schema stability,
   corrections, ID stability: UNVERIFIED (replay only).
2. **Staging/production numbers** — every SLO figure in
   `docs/CAPACITY_MODEL.md` is MEASURED on a single local node at 0.01× Growth
   and UNVERIFIED; the marquee 50k RPS and realtime 1M-connection figures are
   **stated, never run**.
3. **Production-scale SLOs** — read p95, error rate, availability 99.9%,
   ingestion lag: not demonstrated at production scale (G2).
4. **Real on-call routing** — the alert sink is a local file
   (`reports/game_day/alerts.log`); the real channel is blocked until
   STAGING_ACCESS.
5. **WAF / admin-edge controls** — F-06 admin IP restriction and F-07 WAF in
   `docs/SECURITY_REVIEW.md` are open design gaps, UNVERIFIED.
6. **Read replicas / failover** — the replica-loss game-day scenario is
   recorded **UNVERIFIED** (no db-replica service in the local topology);
   deploy + failover drills UNVERIFIED.
7. **Soak** — the ≥ 2 h soak (incl. deploy + failover) was not run; only a
   25 s local smoke.
8. **External pentest** — planned, not run (`docs/PENTEST_SCOPE.md`).
9. **Deprecation policy ratification** — the 90-day/2-release window in
   `docs/API_DEPRECATION_POLICY.md` is PROPOSED for the product owner.
10. **Hypercare/incident-role staffing** — PROPOSED (2 operators × first
    marquee weekend), not staffed.

## 5. Overall verdict

> **NO-GO for production launch** until G1 (licensed provider decision) and
> G2 (staging access + staging evidence) are met and LC-1…LC-8 are re-run on
> staging evidence. The Phase I package is the **pre-launch validation
> harness**: the shadow tooling, the drills, the gates and the honest
> measurement discipline are ready; the launch itself is deliberately not
> authorized by this phase.

## 6. Post-launch (error budget, SLO cadence, deferred backlog)

These policies take effect **at Stage D of `LAUNCH_PLAN.md`**, not before.

- **Error budget** — PROPOSED: 99.9% monthly availability ⇒ 0.9% monthly
  error budget (≈ 6.5 h/month); **burn-rate alerts** at 2× (page) and 10×
  (critical) consumption; budget exhausted ⇒ freeze feature deploys for the
  remainder of the month.
- **SLO review cadence** — monthly: re-run the §12 SLOs on the live topology,
  reconcile with `docs/CAPACITY_MODEL.md`, re-rate the LC criteria, publish to
  `GO_NO_GO.md`.
- **Deferred backlog (with triggers):**
  | Item | Trigger |
  |---|---|
  | Commentary | Commentary licence signed (RSK-03) |
  | Multi-region | Measured need (sustained p95 breach or regional latency data) |
  | Dedicated realtime gateway | Fan-out threshold (the 1M-connection figure approached on the live topology) |
  | Fantasy exposure | `FANTASY_PRODUCT_DECISION.md` present + product sign-off |
  | Kafka/K8s | Measured need (see `CRICKET_PERFORMANCE_AND_SCALING.md` §13) |
