# CRICKET_IMPLEMENTATION_ROADMAP.md

Incremental implementation plan, phases **0–H** (Phase 0 licensing gate, A+
scoring-engine track). Effort is in **developer-days**
(for a team of ~2–4 backend engineers, including tests and review). Estimates are
planning figures, not commitments.

---

## 0. Effort summary & smallest viable release

| Phase | Theme | Est. dev-days | Depends on |
|---|---|---|---|
| 0 | Licensing gate (non-engineering) | — (commercial/legal) | — |
| A | Foundation | 15 | — |
| A+ | Pure scoring engine (parallel track, starts after A) | 10 | A |
| B | Reference data | 25 | A |
| C | Historical data | 25 | A, B |
| D | Live matches | 50 | A, B, C |
| E | Frontend APIs & real-time | 25 | D |
| F | Statistics & analytics | 35 | C, D |
| G | Performance & production | 30 | E, F |
| H | Optional fantasy | 25 | F |
| **Total** | | **≈ 240** | |

**Effort shifts (total unchanged at 240):** the pure `scoring` engine was moved
out of Phase C into a **parallel track A+** (C 35→25, A+ +10), because it depends
only on `core` and can start immediately after Phase A scaffolding. Realtime
**event publication, `match_event_log` and per-match `seq` numbering** were moved
from Phase E into Phase D (D 45→50, E 30→25), because the live append path must
emit events before any WS/SSE consumer exists. Both moves are re-sequencing only —
no net change to the 240 dev-day total.

### Smallest viable release (SVR)

**Phases A + A+ + B + C + D (partial) + E (read-only live scores &
scorecards)**: reference data, the pure scoring engine (A+), historical import,
one live provider feed, and cached REST endpoints for **live matches** and
**scorecards** (with WebSocket updates). This delivers genuinely useful live
scores and scorecards without statistics, rankings or fantasy. Target:
**~120 dev-days** (the A+ engine was already counted inside C). Phase 0 gates the
live provider.

---

## Phase 0 — Licensing gate (non-engineering)

**Goal:** select at least one licensed provider and confirm, **in writing**, the
rights that gate Phases D/E, the commentary endpoints and `/rankings`.

**Owner type:** commercial/legal + product (**not** engineering). It gates
Phase D (live scores), the commentary endpoints and `/rankings`.

- **Tasks**
  1. Run the provider evaluation matrix and the **licensing verification
     checklist** in
     [CRICKET_DATA_INGESTION.md §1.1](CRICKET_DATA_INGESTION.md): coverage
     (international, domestic, IPL/T20 leagues), live score frequency and
     latency SLA, ball-by-ball, **corrections/cancellations semantics**, display
     and **redistribution** rights, **retention**, **DLS/target** and
     **ICC-ranking** supply, **commentary rights**, and attribution.
  2. Obtain **written answers** for at least one provider (plus a fallback) and
     pricing/SLA terms.
  3. Decide whether commentary is in scope and confirm DLS is
     provider-supplied (`source=provider`).
- **Deliverables:** a signed answer set (one row per checklist item) for ≥1
  provider; a decision memo naming the live provider and the fallback.
- **Acceptance criteria:** every checklist item has a written answer for at least
  one provider; rights gaps are explicit; Phase D is unblocked.
- **Risks:** no provider agrees — mitigated by the **ReplayAdapter** (Phase D/E
  can still be built and load-tested against recorded data). **Effort:**
  non-engineering (commercial/legal).
- **DoD:** licensing decision recorded in
  [CRICKET_RISKS_AND_ASSUMPTIONS.md](CRICKET_RISKS_AND_ASSUMPTIONS.md).

---

## Phase A — Foundation

**Goal:** a runnable, conventional Django project with CI, config, logging,
error handling, UUIDs, and testing infrastructure.

- **Tasks**
  1. Project scaffolding (`config/settings/{base,dev,prod,test}`), ASGI/Celery.
  2. `core` app: `TimeStampedModel`, UUIDv7 default, enums, error envelope,
     pagination, structured logging, request-id middleware.
  3. DB conventions: schema `cricket`, naming rules, migration workflow doc.
  4. `.env`/secret loading; Docker Compose for local Postgres/Redis.
  5. CI: lint (ruff), type-check (mypy), tests (pytest), import-linter contracts.
  6. `pytest` + `factory_boy` + disposable Postgres; coverage gate on `core`.
  7. **CI db jobs** (Testing Strategy §16): a `schema-ddl` job on the full
     **`postgres:16`** image that applies the reference DDL from
     `CRICKET_DATABASE_SCHEMA.md` with `ON_ERROR_STOP=1` and asserts the contrib
     extensions and trigram indexes exist; a `migration-drift` job that diffs a
     reference-DDL database against a migrations-only database (§13.1).
- **Tables/modules:** `core`.
- **Endpoints:** `/api/v1/health`.
- **Tests:** unit (uuid7 monotonicity, pagination, error envelope), CI smoke,
  the `schema-ddl` and `migration-drift` gates.
- **Acceptance:** `make up && make test` green; `health` returns OK; CI green.
- **Risks:** none material. **DoD:** scaffold, CI, and conventions documented.

---

## Phase A+ — Pure scoring engine (parallel track)

**Goal:** build the pure `scoring` engine (no DB, no ORM) as soon as Phase A
scaffolding exists, so Phase C *integrates* it with persistence rather than
building it.

- **Tasks**
  1. `scoring.laws/` (pure functions), `engine.py`, `aggregates.py`,
     `standings.py`, `results.py`, `dls.py`, `config.py` (`RuleConfig`).
  2. Unit + property tests for the critical algorithms (Phase 5 §5), including
     the `is_wicket`/retirement rules.
- **Tables/modules:** `scoring` (pure; depends only on `core` types).
- **Endpoints:** none.
- **Tests:** scoring unit + hypothesis property tests; a check that `scoring`
  imports nothing but `core`.
- **Acceptance:** the engine reproduces expected innings totals/cards for
  recorded deliveries.
- **Risks:** rule-convention ambiguity (mitigated by a versioned `RuleConfig`).
  **Effort:** 10 (moved out of Phase C).
- **DoD:** pure engine + rule tests green; no database dependency.

---

## Phase B — Reference data

**Goal:** countries, venues, teams, players, competitions, seasons — with the
provider-identity mapping infrastructure.

- **Tasks**
  1. Models: `Country, GoverningBody, Venue, Ground, Team, Player,
     TeamMembership, Squad, SquadMember, Competition, Season, Series,
     MatchStage`, plus `DataProvider, ProviderEntityMap`.
  2. Selectors + services per module (CRUD via ingestion, not public writes).
  3. `identity_resolution` service (map provider id → internal uuid; create-if-missing).
  4. Admin UI for stewards; duplicate-detection scoring.
  5. Seed importer for the Cricsheet **people/venues register** (authorized open data).
- **Tables/modules:** `venues, teams, players, competitions, providers`.
- **Endpoints:** `GET /teams`, `/teams/{id}`, `/players`, `/players/{id}`,
  `/competitions`, `/series`.
- **Tests:** model constraints; membership temporality; provider-map uniqueness;
  name-search; identity-resolution create/merge.
- **Acceptance:** reference data importable and browsable; player de-dup review flow works.
- **Risks:** identity conflicts (mitigated by steward review). **Effort:** 25.
- **DoD:** imports idempotent; endpoints documented and contract-tested.

---

## Phase C — Historical data

**Goal:** import historical matches, scorecards and stats from a
licensed/authorized source; **integrate** the pure scoring engine (built in
Phase A+) with persistence; backfill aggregates.

- **Tasks**
  1. Integrate the `scoring` engine (built in Phase A+) with persistence:
     deliveries → innings totals, cards, partnerships, FOW.
  2. Provider adapter (start with **Cricsheet** for historical; add managed
     provider later) + normalizer + validation.
  3. Import pipeline with idempotency, DLQ, checkpointing.
  4. Models: `Match, MatchTeam, Toss, MatchPlayer, MatchOfficial, Innings,
     Delivery, InningsEvent, Over, BattingEntry, BowlingEntry, FallOfWicket,
     Partnership, InningsSummary, PlayerMatchStat`, plus `FormatCatalogue`
     (seeded, DB doc §3.2) and `Squad`/`SquadMember`.
  5. Rebuild pipeline (deliveries → cards → per-match stats).
  6. Historical summaries (summary-only) import path.
  7. Hand-written migrations the ORM cannot emit: `delivery` is
     `Meta.managed=False`, and its `PARTITION BY RANGE (match_date)` definition,
     partitions and partial/unique indexes are created by a **`RunSQL`**
     migration; enum types and partial/expression indexes are likewise
     hand-written. The `migration-drift` gate (Testing Strategy §13.1) must stay
     green.
- **Tables/modules:** `matches, innings, deliveries, scorecards, statistics,
  ingestion` (`scoring` is built in Phase A+ and integrated here).
- **Endpoints:** `GET /matches/{id}`, `/matches/{id}/scorecard`,
  `/matches/{id}/innings`, `/matches/{id}/innings/{n}/deliveries`.
- **Tests:** engine integration + property tests; import counts; rebuild
  determinism; summary-only path; idempotency; out-of-order; migration tests;
  reference-DDL↔migration drift (`delivery` reported partitioned).
- **Acceptance:** ≥ N historical matches imported with correct totals; scorecard
  endpoints serve from DB; scoring tests green.
- **Risks:** incomplete historical data (handled by `batch_complete`). **Effort:** 25.
- **DoD:** a real historical match (e.g. an ODI) round-trips with totals matching
  the provider's final card.

---

## Phase D — Live matches

**Goal:** live fixtures, match state, innings progression, deliveries, an event
stream, and live ingestion from a **licensed managed provider** (or the
ReplayAdapter until a licence exists).

- **Tasks**
  1. Fixture sync (cron + push), match status transitions, state machine.
  2. Live ingestion adapter for the chosen provider (push or poll).
  3. **ReplayAdapter** (`providers/adapters/replay.py`): replay recorded
     Cricsheet matches through the live pipeline as a fake provider so this phase
     can be built and load-tested before a licensed contract. Depends on Phase C
     data; only the *real* provider is gated by Phase 0.
  4. Delivery append path (idempotent, revisioned via `is_current`/`revision`) +
     Tier 1 rebuild.
  5. Event publication: append to `match_event_log` with a monotonic **per-match**
     `seq` and publish to Redis; define the consumer contract (WS/SSE consumers
     arrive in Phase E).
  6. `result_service` (completion, margins, tie, draw, super over, forfeit).
     **DLS stance:** targets/par scores are stored and displayed only from a
     licensed/provider source with `source=provider` (or a licensed engine marked
     `source=licensed`); **never self-computed**.
  7. Reconciliation jobs (gap detection, state diff) + corrections.
  8. Freshness monitoring & `meta.stale`.
- **Tables/modules:** `matches, innings, deliveries, synchronization, ingestion,
  providers, realtime` (event log + publication).
- **Endpoints:** `/matches/live`, `/matches/upcoming`, `/matches/recent`,
  `/matches/{id}/innings/{n}/deliveries`.
- **Tests:** idempotency, out-of-order, correction, restart safety, event-log
  `seq` monotonicity, lifecycle E2E (Test multi-innings, ODI DLS, T20 Super Over,
  abandoned) driven by the ReplayAdapter.
- **Acceptance:** a live match updates end-to-end within the freshness SLO; a
  restart mid-match loses nothing; a replayed match produces a complete
  `match_event_log`.
- **Risks:** provider licensing/latency (Phase 0 gate); anti-bot **not**
  applicable (licensed feed). **Effort:** 50.
- **DoD:** live score + deliveries + event stream correct and auditable for a full
  match day.

---

## Phase E — Frontend APIs & real-time updates

**Goal:** cached live-score/scorecard APIs with WebSocket/SSE delivery,
reconnection and recovery (event publication and the per-match `seq` already
exist from Phase D).

- **Tasks**
  1. Redis caching layer + key-versioning invalidation.
  2. CDN/`Cache-Control`/`ETag` polish, including **state-dependent** caching
     (live vs. completed vs. post-reconciliation-window `immutable`); cursor
     pagination everywhere.
  3. `realtime` module **consumers**: WS consumers + SSE; consume the
     `match_event_log` stream produced in Phase D; replay serving for `last_seq`;
     the `resync_required` path.
  4. Frontend integration contract (event types, coalescing).
  5. Throttling/rate limits on hot endpoints.
- **Tables/modules:** `realtime` (consumers), `core (pagination)`, all read selectors.
- **Endpoints:** WS/SSE `/ws|/sse/matches/{id}`; all Phase C/D reads cached.
- **Tests:** realtime delivery, reconnect/replay + `resync_required`, coalescing,
  cache invalidation, state-dependent cache headers, load test (read soak + spike).
- **Acceptance:** p95 live read ≤ 200 ms cached; realtime event ≤ 1 s; no gaps on
  reconnect; `resync_required` on a stale `last_seq`.
- **Risks:** fan-out cost on marquee matches (mitigated by SSE/coalescing/CDN). **Effort:** 25.
- **DoD:** live scorecard updates in a browser via WS and via SSE fallback.

---

## Phase F — Statistics & analytics

**Goal:** career/format/team aggregates, rankings, leaderboards, comparisons,
points tables, H2H, records, form.

- **Tasks**
  1. `PlayerCareerStat/TeamStat/HeadToHead/Leaderboard/FormTrend` recompute jobs.
  2. Standings + NRR (`standings.py`) with all NRR edge cases.
  3. Rankings import (provider-supplied ICC) + history.
  4. Compare players; search/filtering (trigram) with ranking.
  5. Historical records aggregations.
- **Tables/modules:** `statistics, rankings, competitions, search`.
- **Endpoints:** `/statistics/{batting,bowling,fielding,teams}`, `/records/*`,
  `/rankings`, `/head-to-head`, `/players/compare`, `/series/{id}/points-table`,
  `/search`.
- **Tests:** aggregate correctness vs. provider; NRR edge cases; H2H;
  leaderboard tie-breaking; search relevance; performance of aggregates.
- **Acceptance:** career stats and points tables match provider references for a
  sample of players/series.
- **Risks:** metric-convention mismatch with provider (document and reconcile). **Effort:** 35.
- **DoD:** statistics pages served entirely from precomputed tables, sub-200 ms.

---

## Phase G — Performance & production

**Goal:** production hardening, scaling, backups, deployment, recovery testing.

- **Tasks**
  1. Read replicas + routing; PgBouncer; connection-pool tuning.
  2. Partition maintenance job for `delivery`; archival/detach of old partitions.
  3. Load testing (all §12 scenarios) + tuning.
  4. Observability (metrics, dashboards, alerts, runbooks).
  5. Backups + PITR + cross-region; quarterly restore drill runbook.
  6. Deployment pipeline (blue/green), migration gates, rollback drill.
  7. Security review (RBAC, secrets, WAF, rate limits).
- **Tables/modules:** infra + `administration`, all modules.
- **Tests:** performance suite; migration tests; disaster-recovery drill;
  security checks.
- **Acceptance:** SLOs met under load; a restore drill succeeds within RTO.
- **Risks:** over-provisioning (avoid Kafka/K8s without measured need). **Effort:** 30.
- **DoD:** production runbook, SLO dashboards, and a successful DR drill.

---

## Phase H — Optional fantasy statistics

**Goal:** versioned fantasy scoring rules, points computation, summaries, audit.

- **Tasks**
  1. Fantasy models (`ruleset`, `version`, `point_rule`, `player_match_performance`,
     `ledger`, `adjustment`, `calc_audit`).
  2. `fantasy_service.compute` (pure, replays from `PlayerMatchStat`).
  3. Live/final fantasy recompute; adjustments with audit.
  4. Ruleset authoring + version freezing.
- **Tables/modules:** `fantasy`.
- **Endpoints:** `/fantasy/points`, `/fantasy/rulesets`, `/fantasy/matches/{id}`.
- **Tests:** ruleset versioning, recompute determinism, adjustment audit,
  frozen-version immutability.
- **Acceptance:** points reproducible from a frozen ruleset version + a match snapshot.
- **Risks:** rule ambiguity (mitigated by versioning + audit). **Effort:** 25.
- **DoD:** fantasy points computed and auditable; no paid-contest functionality.

---

## 1. Cross-phase discipline

- **Every phase ships behind feature flags** and with migrations that pass the
  forward/backward test and the **reference-DDL ↔ migration drift check**
  (Testing Strategy §13.1).
- **No phase writes derived data without a rebuild path** (all derived tables
  must be reconstructable from canonical source).
- **Every endpoint added is contract-tested** and documented in
  [CRICKET_API_SPECIFICATION.md](CRICKET_API_SPECIFICATION.md).
- **Every ingestion path is idempotent** (Phase 8 discipline) before it is used
  against a real provider.

## 2. Critical path

`Phase 0 (licensing, non-engineering) → A → (A+ scoring engine, parallel) → B → C
→ D → E` is the SVR critical path. `F`, `G`, `H` can proceed partly in parallel
once `D` is stable (F needs C+D; G needs E+F; H needs F). A+ starts right after A
and must finish before C integrates it; Phase 0 gates the *live* provider in D,
the commentary endpoints and `/rankings`.
