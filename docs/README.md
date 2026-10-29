# Cricket OS — Backend & Database Implementation Plan

This directory is the planning package for **Cricket OS**, a cricket scores and
statistics platform inspired by the *observable functionality* of Cricbuzz,
ESPNcricinfo and Dream11. It contains research, domain modelling, database
design, scoring rules, architecture, API, ingestion, performance, security,
testing, a phased roadmap, and a risk register.

> **Status:** Research + design only. No application code has been written.
> Implementation begins with the non-engineering
> [Phase 0 (licensing gate)](CRICKET_IMPLEMENTATION_ROADMAP.md), then
> [Phase A (foundation)](CRICKET_IMPLEMENTATION_ROADMAP.md) with the
> [Phase A+ pure scoring engine](CRICKET_IMPLEMENTATION_ROADMAP.md) running as a
> parallel track immediately after it.

## Document map

| # | Document | Contents |
|---|----------|----------|
| 0 | [README.md](README.md) | This index |
| 1 | [CRICKET_PLATFORM_RESEARCH.md](CRICKET_PLATFORM_RESEARCH.md) | Phase 1 (REA tooling) + Phase 2 (website analysis) research log, method, evidence, limitations |
| 2 | [CRICKET_FEATURE_INVENTORY.md](CRICKET_FEATURE_INVENTORY.md) | Feature inventory table (feature → data → capability → freshness → evidence → confidence) |
| 3 | [CRICKET_DOMAIN_MODEL.md](CRICKET_DOMAIN_MODEL.md) | Normalised cricket domain model (all entities, fields, relationships, examples) |
| 4 | [CRICKET_DATABASE_SCHEMA.md](CRICKET_DATABASE_SCHEMA.md) | PostgreSQL design, Mermaid ER diagram, full DDL, indexing, partitioning, edge cases |
| 5 | [CRICKET_SCORING_RULES.md](CRICKET_SCORING_RULES.md) | Scoring domain service, algorithms, pseudocode, unit tests, rule edge cases |
| 6 | [CRICKET_BACKEND_ARCHITECTURE.md](CRICKET_BACKEND_ARCHITECTURE.md) | Modular monolith (Django/DRF), modules, dependency rules, directory structure |
| 7 | [CRICKET_API_SPECIFICATION.md](CRICKET_API_SPECIFICATION.md) | Versioned REST API `/api/v1/`, endpoint matrix, JSON payloads, cache policy |
| 8 | [CRICKET_DATA_INGESTION.md](CRICKET_DATA_INGESTION.md) | Provider evaluation matrix, adapter design, idempotency, reconciliation, sequence diagram |
| 9 | [CRICKET_PERFORMANCE_AND_SCALING.md](CRICKET_PERFORMANCE_AND_SCALING.md) | Capacity scenarios, caching, partitioning, real-time fan-out, load-testing |
| 10 | [CRICKET_TESTING_STRATEGY.md](CRICKET_TESTING_STRATEGY.md) | Test pyramid, scoring/ingestion/API tests, match-lifecycle fixtures |
| 11 | [CRICKET_IMPLEMENTATION_ROADMAP.md](CRICKET_IMPLEMENTATION_ROADMAP.md) | Phases 0–H (incl. Phase 0 licensing gate + Phase A+ scoring engine), tasks, dependencies, acceptance criteria, effort |
| 12 | [CRICKET_SECURITY_AND_RELIABILITY.md](CRICKET_SECURITY_AND_RELIABILITY.md) | Auth/RBAC, secrets, audit, backups/DR, data quality, licensing-vs-access (Phase 10) |
| 13 | [CRICKET_RISKS_AND_ASSUMPTIONS.md](CRICKET_RISKS_AND_ASSUMPTIONS.md) | Risks, licensing dependencies, open questions, task checklist |

## Ground rules used throughout

1. **No proprietary reuse.** Nothing here is copied from Cricbuzz source code,
   private APIs, internal schemas or protected assets. Cricbuzz is studied only
   through its *publicly served pages* (see the research log for the exact
   method and its limitations).
2. **Label everything.** Observations are marked *observed*; architecture
   decisions are marked *inference* or *recommendation*.
3. **Licensing is a hard dependency.** Live ball-by-ball feeds, commentary and
   official rankings must come from a **licensed** provider. Public pages may be
   used for *studying UX*, never for *redistribution of data*.
4. **PostgreSQL + Django modular monolith** unless evidence in this package
   justifies otherwise.

## Quick start for a developer

- Read [CRICKET_DOMAIN_MODEL.md](CRICKET_DOMAIN_MODEL.md) and
  [CRICKET_DATABASE_SCHEMA.md](CRICKET_DATABASE_SCHEMA.md) before writing models.
- Read [CRICKET_SCORING_RULES.md](CRICKET_SCORING_RULES.md) before writing any
  statistics code.
- Start with the **Phase 0 licensing gate** (non-engineering) in parallel with
  **Phase A**; build the pure **scoring engine** as the parallel **Phase A+**
  track, then take Phase B/C/D tasks. The smallest viable release is described in
  [CRICKET_IMPLEMENTATION_ROADMAP.md](CRICKET_IMPLEMENTATION_ROADMAP.md).

## Revision history

### 2026-10-29 (follow-up review)

- **CRICKET_DATABASE_SCHEMA.md** — added the `innings_event_type` enum and the
  `innings_event` table (partial/revision/provider unique indexes) so a
  retirement with no ball (rain/break, before the first ball, innings never
  resumed) is captured without overloading `delivery`; ER entity + edges; §4.8
  now defines the single merged accessor `innings_retirements(innings)` and the
  exactly-one-source rule.
- **CRICKET_DOMAIN_MODEL.md** — new `InningsEvent` entity (§3.13), retirement
  wording updated to the single-source rule, cardinality line added.
- **CRICKET_SCORING_RULES.md** — `innings_retirements()`/`merged_timeline()`
  accessor and off-ball handling in `partnerships()`; new §2.14 non-6-ball
  formats (The Hundred: 5-ball sets, 100 balls, presentation-only end change);
  E25; Hundred + off-ball-retirement unit tests.
- **CRICKET_DATA_INGESTION.md** — normalizer routes off-ball retirements to
  `innings_event`; data-quality rule that a retirement is recorded in exactly one
  source (+ `innings_event` idempotency key).
- **CRICKET_API_SPECIFICATION.md** — scorecard exposes a single merged
  `retirements[]` (`§3.2`/`§4.2`) built from `delivery` + `innings_event`.
- **CRICKET_BACKEND_ARCHITECTURE.md** — `innings` module owns `InningsEvent`.
- **CRICKET_TESTING_STRATEGY.md** — `innings_event` constraint tests; §13.1
  reference-DDL ↔ migration drift check; §14.9 off-ball retirement and §14.10 The
  Hundred E2E scenarios; new §16 `postgres:16` CI database jobs (`schema-ddl`,
  `migration-drift`).
- **CRICKET_IMPLEMENTATION_ROADMAP.md** — Phase A CI db jobs (`schema-ddl` on
  full `postgres:16`, `migration-drift`); Phase C adds `InningsEvent` and the
  hand-written `RunSQL` `delivery` partitioning migration; drift gate added to
  cross-phase discipline.
- **README.md** — this revision entry.

### 2026-10-29

- **CRICKET_DATABASE_SCHEMA.md** — `delivery` gained `is_current`/`superseded_at`,
  retirement columns + `ck_delivery_retirement`, and new indexes
  (`ux_delivery_current`, `ux_delivery_revision`, `ux_delivery_provider`); every
  unique index now includes the partition key `match_date`. Added tables `ground`,
  `format_catalogue` (+ seed), `squad`/`squad_member`, `match_result`,
  `match_award`, `player_merge`, `match_event_log`, `audit_log` and
  `innings_summary.batch_complete`. `match_status` lost `TIED`/`NO_RESULT` (now
  result-only); added `retirement_type`. Converted `ux_form_trend`/`ux_fpr` to
  `CREATE UNIQUE INDEX`; fixed a non-immutable `player.name_key` generation
  expression.
- **CRICKET_DOMAIN_MODEL.md** — Delivery entry (addressing, `is_current`,
  retirements), status-vs-result (`§2.3`), MatchEventLog (`§3.12`), PlayerMerge
  (`§1.15`), AuditLog (`§6.9`), `SUPER_OVER_TIE_BREAK` result, `batch_complete`,
  cardinality summary.
- **CRICKET_SCORING_RULES.md** — wicket/retirement rules in the totals and
  partnership/FOW pseudocode, E10/E11, status-vs-result note, retirement unit
  tests, **two-tier** recalculation (`§6`), DLS provider-only stance (`§2.13`).
- **CRICKET_DATA_INGESTION.md** — tiered ingestion pipeline, `is_current`/
  `match_event_log` sequence flow, idempotency by revision, corrections wording,
  **ReplayAdapter** (`§2.1`) + provider-matrix row, retirement data-quality check.
- **CRICKET_BACKEND_ARCHITECTURE.md** — module ownership (PlayerMerge,
  FormatCatalogue, MatchEventLog), removed a self-loop edge, tiered subscriber
  contract, `adapters/replay.py`; quoted a node label so the diagram parses.
- **CRICKET_API_SPECIFICATION.md** — status-vs-result convention,
  state-dependent caching (`§3.9`) and per-resource cache lines, realtime
  `match_event_log`/`resync_required` (`§3.8`).
- **CRICKET_PERFORMANCE_AND_SCALING.md** — `delivery` index/partition note,
  state-dependent TTLs (`§5.1`/`§5.2`), tiered recalculation, event-log retention
  and reconnection (`§8`).
- **CRICKET_TESTING_STRATEGY.md** — constraint/revision/tier tests, state-cache
  test, correction flow by `is_current`/`revision`, ReplayAdapter + realtime/
  `resync_required` tests, DLS and retirement E2E cases.
- **CRICKET_IMPLEMENTATION_ROADMAP.md** — non-engineering **Phase 0** licensing
  gate, parallel **Phase A+** scoring engine, event publication moved into Phase
  D; effort re-sequenced (C 35→25, D 45→50, E 30→25) with the total unchanged at
  240.
- **CRICKET_RISKS_AND_ASSUMPTIONS.md** — RSK-01 mitigation (Phase 0 + Replay),
  Appendix A phase checklist (0/A+/D) and Appendix B build order.
- **CRICKET_FEATURE_INVENTORY.md** — schedule feature aligned to
  `GET /matches/upcoming?from=&to=&series_type=`.
- **README.md** — status/quick-start updated for Phase 0 and Phase A+; this
  revision history added.
