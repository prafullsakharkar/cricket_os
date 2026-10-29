# CRICKET_RISKS_AND_ASSUMPTIONS.md

Risk register, licensing dependencies, explicit assumptions, and open questions
for Cricket OS. Items are traced from earlier documents where relevant.

---

## 1. Risk register

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| RSK-01 | **No licensed data provider agreed** for live ball-by-ball | Medium | High | **Phase 0 licensing gate** ([roadmap](CRICKET_IMPLEMENTATION_ROADMAP.md)); start with open historical data (Cricsheet) + a managed provider trial; build and load-test live with the internal **ReplayAdapter**; keep the adapter layer provider-swappable |
| RSK-02 | Provider coverage gaps (some domestic/associate matches missing) | High | Medium | Capability-driven UI (show what exists); reconcile from multiple sources; mark data unavailable rather than fake it |
| RSK-03 | **Redistribution rights** bar showing ball-by-ball/commentary to end users | Medium | High | Contract display/redistribution rights explicitly; `LicensedContentPolicy` per provider; endpoints degrade gracefully (`403 license_required`) |
| RSK-04 | Data-retention limits require deleting ball data | Medium | Medium | Policy-driven retention job; keep summaries; archive raw feeds if permitted |
| RSK-05 | Provider schema drift breaks ingestion | Medium | Medium | Strict validation + contract tests + quarantine to DLQ + alerting |
| RSK-06 | Provider outage during a marquee match | Medium | High | Circuit breakers, cache fallback, `stale` marking, multi-provider strategy later |
| RSK-07 | Incorrect/corrected scores damage trust | Medium | High | Derive scores from stored deliveries; reconciliation vs. provider final; audited corrections; replay-safe recompute |
| RSK-08 | Player identity duplication across providers | High | Medium | `provider_entity_map` + name/DoB scoring + steward review; merge with redirect map |
| RSK-09 | NRR / DLS / super-over rule misinterpretation | Medium | Medium | Encode rules per format with rule config; DLS only from licensed source; extensive edge-case tests |
| RSK-10 | Traffic spike on a popular match overwhelms origin | Medium | High | CDN absorption, Redis, replicas, coalesced realtime, rate limits, load tests |
| RSK-11 | Cost blow-up from over-provisioning (Kafka/K8s/multi-region) | Medium | High | Explicitly defer; adopt only on measured need (see performance doc) |
| RSK-12 | Licensing of ICC rankings / official stats | Medium | Medium | Treat rankings as provider-supplied; verify right to display |
| RSK-13 | Secrets leakage via provider payloads/logs | Low | High | Secret manager; redaction; never store secrets in DB/DLQ |
| RSK-14 | Migrations cause downtime on large tables | Medium | Medium | Expand/contract; `CONCURRENTLY`; migration tests at scale; backup gate |
| RSK-15 | Scope creep into out-of-scope features (news, betting, paid fantasy) | Medium | Medium | Explicit out-of-scope list enforced in backlog review |

---

## 2. Licensing dependencies (hard gates)

| Dependency | Needed for | Status |
|---|---|---|
| Managed provider contract (scores, ball-by-ball, stats) | Phases D, F | **Open — must verify** |
| Commentary rights (if in scope) | `/commentary` | **Open — likely separately licensed** |
| Official ICC rankings display rights | `/rankings` | **Open — verify** |
| DLS implementation licence or provider-supplied par scores | DLS results | **Open — do not self-implement** |
| Historical open data (Cricsheet ODC-By) | Phase C backfill | Available with attribution (commercial use permitted) |
| Attribution requirements rendering | Public UI | Configure per licence |

> **Operational access control ≠ content licence.** RBAC controls *who inside the
> platform* can do what; the licence controls *what the platform may display or
> redistribute at all*. Both must be enforced independently.

---

## 3. Assumptions (explicit)

1. **A licensed provider will be available** with adequate international +
   domestic + IPL coverage, live ball-by-ball, and corrections. *(Unverified.)*
2. Historical reconstruction is possible via **authorized open data**
   (Cricsheet) for a large corpus, with attribution. *(Observed.)*
3. Freshness tiers observed on Cricbuzz (`max-age` 1–26 s) are a reasonable
   proxy for user expectations. *(Observed, used as a design hint.)*
4. A **modular Django monolith** is sufficient through the 1M-DAU stage. *(Inference.)*
5. PostgreSQL + Redis + Celery + Channels cover the real-time and batch needs
   without Kafka/K8s. *(Inference.)*
6. Statistics conventions (batting average excludes not-outs; bowled-out NRR
   uses full quota) follow standard practice and the provider's definitions will
   align or be documented where they differ. *(Partly observed via Wikipedia /
   Laws.)*
7. The domain model covers all formats in scope (Test, ODI, T20I, domestic
   T20/first-class, women's) via `format_catalogue`. *(Design claim.)*

---

## 4. Open questions (carried forward)

1. Which provider(s) will be contracted, with what latency/SLA/pricing?
2. Does the provider supply provider-stable entity IDs for idempotency?
3. Is commentary in scope, and is it licensed?
4. Is a licensed DLS engine available, or must we rely on provider par scores?
5. What are the exact data-retention and redistribution terms?
6. What is the ICC ranking update cadence and rating-algorithm version?
7. Are there regional data-residency requirements?
8. What is the required real-time latency SLO for live scores (product decision)?
9. Which leagues/competitions are must-have at launch (defines ingest scope)?
10. Do we need multi-language content beyond the structured data?

---

## 5. Appendix A — implementation task checklist (condensed)

**Phase 0** ☐ provider matrix ☐ licensing checklist (written answers for ≥1
provider) ☐ decision memo (live provider + fallback)
**Phase A** ☐ scaffold ☐ core app ☐ error envelope ☐ CI ☐ test infra
**Phase A+** ☐ pure scoring engine ☐ rule/property tests
**Phase B** ☐ reference models ☐ selectors/services ☐ provider map ☐ identity
resolution ☐ steward admin ☐ register importer
**Phase C** ☐ engine↔persistence integration ☐ adapter (Cricsheet) ☐ import
pipeline ☐ match models ☐ rebuild pipeline ☐ summary-only path
**Phase D** ☐ fixture sync ☐ state machine ☐ live adapter ☐ **ReplayAdapter** ☐
delivery append ☐ event publication (`match_event_log`/`seq`) ☐ result service ☐
reconciliation ☐ corrections ☐ freshness monitor
**Phase E** ☐ Redis cache ☐ invalidation ☐ ETag/CDN ☐ WS ☐ SSE ☐ replay ☐
throttling
**Phase F** ☐ career/team aggregates ☐ standings+NRR ☐ rankings import ☐
leaderboards ☐ compare ☐ search ☐ records
**Phase G** ☐ replicas ☐ pooling ☐ partition maintenance ☐ load tests ☐
observability ☐ backups/DR ☐ deploy/rollback ☐ security review
**Phase H** ☐ fantasy models ☐ compute ☐ adjustments ☐ ruleset versioning ☐ audit

## 6. Appendix B — prioritized next 10 implementation tasks

1. **Phase 0 licensing gate** ([roadmap](CRICKET_IMPLEMENTATION_ROADMAP.md)):
   written confirmation from provider(s) of coverage, redistribution, retention,
   DLS/rankings/commentary rights.
2. **Scaffold + CI** (Phase A): project, `core`, config, tests, import-linter.
3. **Reference models** (Phase B): country/venue/team/player/competition.
4. **Provider identity map + identity resolution** service.
5. **Scoring engine (pure)** — Phase A+ (parallel track after A) — with the
   Phase-5 unit/property tests.
6. **Cricsheet adapter + historical import pipeline** (idempotent, DLQ).
7. **Match/innings/delivery schema + rebuild pipeline** (scorecards).
8. **Read APIs** for live list, match, scorecard, deliveries (cached).
9. **Licensed live adapter + ReplayAdapter + reconciliation/corrections** (Phase D).
10. **Realtime (WS+SSE) with reconnect/replay** (Phase E).

*(Then Phase F statistics, Phase G production, Phase H fantasy.)*
