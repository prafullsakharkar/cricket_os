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

## Phase A+ — Pure scoring engine (parallel track) — **DONE (commit `…`, branch `phase-a-foundation`)**

**Goal:** build the pure `scoring` engine (no DB, no ORM) as soon as Phase A
scaffolding exists, so Phase C *integrates* it with persistence rather than
building it.

- **Tasks**
  1. `scoring.laws/` (pure functions), `engine.py`, `aggregates.py`,
     `standings.py`, `results.py`, `dls.py`, `config.py` (`RuleConfig`).
  2. Unit + property tests for the critical algorithms (Phase 5 §5), including
     the `is_wicket`/retirement rules.
  3. Versioned presets matching the `format_catalogue` seed; typed
     `ScoringInputError` boundary; DLS provider-only interface; §14 lifecycle
     scenarios; purity (Django-blocked import) and determinism checks.
- **Tables/modules:** `scoring` (pure; depends only on `core` types).
- **Endpoints:** none.
- **Tests:** 168 scoring tests — unit, Hypothesis properties
  (`--hypothesis-profile=ci`), §14 scenarios, purity, determinism. Coverage
  gate **≥ 95% line + branch** on `apps/scoring` (measured 99%); import-linter
  contract `scoring may import only core`; mutmut report in the final review.
- **Acceptance:** the engine reproduces expected innings totals/cards for
  recorded deliveries; full ODI rebuild ~3 ms, 600-ball day ~6 ms (benchmarked).
- **Risks:** rule-convention ambiguity (mitigated by a versioned `RuleConfig`).
  **Effort:** 10 planned; delivered within scope.
- **DoD:** pure engine + rule tests green; no database dependency. API surface
  documented in [`SCORING_ENGINE_API.md`](SCORING_ENGINE_API.md).

---

## Phase B — Reference data — **DONE (8 commits `b1693bb`…, branch `phase-b`)**

**Goal:** countries, venues, teams, players, competitions, seasons — with the
provider-identity mapping infrastructure.

Delivered as **8 reviewable group commits** on branch `phase-b`
(Groups 1–8, `b1693bb`…). `make check` is green through the final
commit: **613 suite tests + 172 scoring-engine tests** under three coverage
gates — combined core + six Phase B apps **≥ 90% line+branch (94.5% at the
last run)**, pure scoring engine **≥ 95% (99.1%)**, identity engine
(`identity_scoring.py` + `services/merge.py`) **100% line+branch**.

- **Tasks** (as delivered)
  1. Models + migrations for `venues, teams, players, competitions, providers,
     administration` — 19 tables with exact DDL parity (`name_key` IMMUTABLE
     `GeneratedField`, `source_provider_id` plain UUID, soft-deletes, exact
     constraint/index names, trigram GIN indexes).
  2. Selectors + services per module (six-layer rule; CRUD via ingestion, not
     public writes); generic `resolve()` in `providers/services.py` with
     per-entity resolvers (team / venue / competition / series / player).
  3. Identity resolution + merge: pure `identity_scoring.py`, duplicate-
     candidate detection, steward merge service (re-point, idempotent,
     cycle/self refusal, **no unmerge**, synchronous `player.merged` audit).
  4. Admin UI + roles (`administration`): duplicate queue + Groups, four roles
     with an exact `has_perm` matrix, confirm-flow merge action.
  5. Cricsheet register importer (`import_cricsheet_register`): local-file
     allowlist, fixtures ≤ 200 rows, per-input error CSV, dry-run; **30k-row
     benchmark: 30000 created / 0 errors / 278.90 s (~108 rows/s) /
     91.9 MiB peak RSS**.
  6. Public read API — 10 GET endpoints under `/api/v1/`: `{"data","meta"}`
     envelope, UTC timestamps, `?fields=` sparse fields, allow-listed
     sorting, dual pagination (`/players`), trigram search, ETag/304, merge
     `301`, JSON error catalog, and `/api/v1/schema` (drf-spectacular OpenAPI
     3.0.3).
- **Tables/modules:** `venues, teams, players, competitions, providers,
  administration` (+ `core`).
- **Endpoints:** `GET /teams`, `/teams/{id}`, `/players`, `/players/{id}`,
  `/competitions`, `/competitions/{id}`, `/series`, `/series/{id}`,
  `/venues`, `/venues/{id}`, `/api/v1/schema`. (The roadmap's original list
  omitted the venues and schema endpoints — both delivered.)
- **Tests:** model constraints, membership temporality, provider-map
  uniqueness, name search, resolve/merge, admin permissions, importer, and a
  68-test API contract group (envelope, sparse fields, sorting, pagination,
  cache/ETag/304/301, query ceilings, trigram SQL contract, golden payloads,
  jsonschema-vs-OpenAPI payload validation, schemathesis smoke).
- **Acceptance:** reference data importable (30k benchmark) and browsable via
  the public API; steward de-dup review flow works. **DoD met.**
- **Risks:** identity conflicts mitigated by steward review; **MFA for
  `admin`/`data_steward` deferred to Phase G** (see ticket there). **Effort:**
  25 planned; delivered within scope — two material scope additions (the
  public reference API, Group 7; the 7-app coverage gate, Group 8) were offset
  by reusing the Phase A+ scoring engine and importing the venues register from
  a curated CSV rather than a live provider.

### Phase B decisions (conflicts resolved — recorded in the final report)

- **D1 — Timestamps.** Reference DDL lists no `created_at`/`updated_at` on
  reference entities; DATABASE_SCHEMA wins ⇒ models carry exactly the DDL
  columns (`TimeStampedModel`/`ProvenanceModel` only where the DDL declares the
  fields).
- **D2 — `source_provider_id`.** Plain `UUIDField(null=True)` on every model
  that has it — DDL says `uuid`, not an FK, not a TextField.
- **D3 — `FormatCatalogue` ownership.** Moved `matches` → `competitions`;
  ARCHITECTURE updated in the same change; the seed matches the DDL exactly.
- **D4 — Where `resolve()` lives.** Generic `resolve()` in
  `providers/services.py` with a per-entity resolver registry (registered in
  each app's `ready()`); the player resolver + scoring live in
  `players/services/identity_resolution.py`; `players/identity_scoring.py`
  stays pure.
- **D5 — `merged_by`/`actor_id`.** Plain UUIDs (no user model until the auth
  phase): the merge service takes `actor: UUID | str | None`; a UUID → stored
  in `merged_by`, `str()` → `AuditLog.actor_label`, `actor_id` stays NULL.
- **D6 — DoB-mismatch cap.** Both DoBs present and different ⇒ the DoB
  feature scores 0 and the total caps at 0.25 (below both thresholds) — a
  mismatched pair can never auto-link or auto-merge.
- **D7 — `--names` variants.** The first variant sets `known_as` only when it
  is blank; all variants are validated and counted (no alias table until
  Phase F).
- **D8 — OpenAPI/schemathesis.** schemathesis 4.32.7 runs on Python 3.14
  (`get_all_operations()` now returns `Ok|Err` Results; the smoke test unwraps
  them). The fallback that *did* materialise: drf-spectacular cannot
  introspect the custom `{"data","meta"}` collection envelope and documents
  the DRF default `{count, next, previous, results}` shape; the real envelope
  is asserted in `test_reference_api.py` and payloads are validated against
  the component schemas with jsonschema.

---

## Phase C — Historical data — **DONE (8 commits `eaef83c`…, branch `phase-c`)**

**Goal:** import historical matches, scorecards and stats from a
licensed/authorized source; **integrate** the pure scoring engine (built in
Phase A+) with persistence; backfill aggregates.

Delivered as **8 reviewable group commits** on branch `phase-c`
(Groups 1–8, `eaef83c`…). `make check` is green through the final commit:
**797 suite tests + 172 scoring-engine tests** under four coverage gates —
combined core + Phase B + Phase C apps **≥ 90% line+branch (94.7% at the last
run)**, pure scoring engine **≥ 95% (99.1%)**, identity engine **100%
line+branch**, and the new **Phase C gate** (write path + rebuild + Cricsheet
mapper) **≥ 95% line+branch (100%)**.

- **Tasks** (as delivered)
  1. Models + partitions (G1): `match, match_team, toss, match_player,
     match_result, innings, innings_event, innings_summary, delivery, over,
     batting_entry, bowling_entry, fall_of_wicket, partnership, player_match_stat,
     import_job, ingestion_error, sync_checkpoint, data_correction`. `delivery`
     is `Meta.managed=False`, RANGE-partitioned by `match_date` with a composite
     `(id, match_date)` PK and **no DEFAULT partition** — created by a
     hand-written `RunSQL` migration; `ensure_partition(year)` creates yearly
     partitions race-safely before every insert. `migration-drift` and
     `schema-ddl` gates stay green.
  2. Idempotent delivery write path (G2): `append_or_revise` is
     order-independent, idempotent, partition-safe, validated at the boundary
     (typed `DeliveryValidationError`s → DLQ) and supersedes changed balls
     (`is_current=false` + `revision` bump + `DataCorrection` audit) in ONE
     transaction; bulk fast path; `delivery.appended`/`delivery.corrected`
     events (suppressible).
  3. Engine-driven rebuild (G3): `rebuild_innings`/`rebuild_match` derive
     summaries, cards, FOW, partnerships and the match result from current
     deliveries in ONE transaction, revisioned and digest-deterministic
     (unchanged data → same digest, no revision bump, no events);
     `player_match_stat` builds exactly once on `match.completed`/`correction`.
  4. Ingestion framework (G4): `ImportJob.idempotency_key` unique,
     `IngestionError` DLQ (redacted payloads), `SyncCheckpoint` advanced after
     commit, Celery exponential backoff (8 max retries) with crash resume;
     `import_cricsheet` / `import_summary_cards` commands with `--dry-run`.
  5. Cricsheet adapter (G5): pure parser (`cricsheet_parse.py`) + Django
     pipeline (`cricsheet.py`) — local file → identity resolution → canonical
     rows → Tier-1 rebuild → reconciliation (`reconciliation_mismatch` DLQ).
     Idempotency key `cricsheet:<match_id>:<content_sha256>`.
  6. Summary-only adapter (G6): `summary_cards.py` writes the provider's final
     figures with `batch_complete=false`, never writes `delivery` rows and never
     calls the engine; shared identity helpers (`adapters/identity.py`).
  7. Read API (G7): `GET /matches/{id}`, `/scorecard`, `/innings`,
     `/innings/{n}/deliveries` with state-dependent caching (`max-age=300`/`5`),
     strong ETags + `304`, partition-pruned delivery reads (`is_current=true`
     only), cursor on `delivery_seq`, `meta.ball_data/revision/commentary`.
  8. Admin/DLQ + docs + gates (G8): admin for `ImportJob`, `IngestionError`
     (mark-resolved/reopen DLQ actions), `SyncCheckpoint`, `DataCorrection`;
     the `test-phasec` coverage gate; roadmap DONE block + decisions.
- **Tables/modules:** `matches, innings, deliveries, scorecards, statistics,
  ingestion, synchronization` (`scoring` built in Phase A+ and integrated).
- **Endpoints:** `GET /api/v1/matches/{id}/`, `/matches/{id}/scorecard`,
  `/matches/{id}/innings`, `/matches/{id}/innings/{n}/deliveries`.
- **Tests:** engine integration, rebuild determinism, idempotency + supersede,
  partition safety, ingestion jobs/DLQ/checkpoints, both adapters end-to-end
  (fixture round-trips incl. reconciliation + dry-run), the 4-endpoint read API
  (envelope, cursor, ETag/304, state-dependent TTLs, ball_data flag),
  migration/partition tests, and the Phase C coverage gate. Suite **797**.
- **Acceptance:** the Cricsheet fixture (T20I India 8/1 → Australia 9/0, win by
  10 wickets) and the summary fixture (ODI India 250/8 → Australia 251/5, win
  by 5 wickets) round-trip through the pipeline and serve from the scorecard
  endpoints with totals matching the provider cards. **DoD met.**
- **Risks:** incomplete historical data handled by `batch_complete` (summary-only
  innings are skipped by the engine; provider-written rows authoritative).
  **Effort:** 25 planned; delivered within scope.
- **Benchmarks:** pure-engine innings rebuild ≈ **3 ms** for a 300-ball ODI and
  ≈ **6 ms** for a 600-ball Test innings (SCORING_ENGINE_API §rebuild; micro-
  benchmarks run via `pytest -m benchmark` in `tests/scoring/test_benchmarks.py`).

### Phase C decisions (conflicts resolved — recorded in the final report)

- **C1 — `delivery` partitioning.** RANGE by `match_date` (= match START date,
  never caller-supplied), composite PK `(id, match_date)`, **no DEFAULT
  partition** (a typo'd year must fail loudly, not silently land in a catch-all);
  `ensure_partition(year)` is race-safe (advisory lock) and called before every
  insert. Hand-written `RunSQL` migration; `Meta.managed=False`.
- **C2 — `Over` placement / columns.** `Over` lives in `apps/deliveries/models.py`
  (not `scorecards`) and has **no `legal_balls` column** (the DDL doesn't define
  one).
- **C3 — stats module shape.** `apps/statistics/services.py` → `services/stats.py`
  so import-linter contracts hold; wired in `apps.py` `ready()` with a
  `_ready_done` guard.
- **C4 — stats timestamps / summary-only.** `computed_at = match.end_time_utc or
  timezone.now()`. An `is_partial` flag exists for summary-only matches but is
  NOT wired from the summary adapter; the summary adapter does not publish
  `match.completed` (per-match stats for summary-only cards are out of scope).
- **C5 — bowler-credited wickets.** Only BOWLED/CAUGHT/CAUGHT_AND_BOWLED/LBW/
  STUMPED/HIT_WICKET/MANKAD set `bowler_credited_wicket=True`. Cricsheet
  `retired hurt`/`retired not out` are a retirement on the ball (is_wicket=False),
  `retired out` is a RETIRED_OUT dismissal NOT credited to the bowler.
- **C6 — engine innings status.** The engine leaves innings `status` at
  IN_PROGRESS on match completion (only `match.status`/`match_result` become
  terminal); tests do not pin innings status.
- **C7 — idempotency keys without parsing.** The adapter factory computes the
  key from the filename stem + raw sha256 (no parse); parsing happens inside the
  stage, so a malformed file is a permanent `PipelineError` → DLQ, never a
  retried transient.
- **C8 — team types.** `TeamType` has no women's "domestic" label ⇒ women's
  non-international teams map to `WOMEN_FRANCHISE` (`adapters/identity.py`).
- **C9 — retry budget.** Global Celery `max_retries` 5→8; `ingest_job` uses
  `autoretry_for=()` + explicit `self.retry()` so only transient pipeline
  errors retry.
- **C10 — deliveries cursor.** `Delivery._meta.pk` is the composite `pk` (no
  single tiebreaker for the core `CursorPagination`), so the deliveries endpoint
  uses its own cursor on `delivery_seq` alone (unique per innings), same
  base64-json codec shape.
- **C11 — cache policy implementation.** `_cache_ttl(match)`: terminal
  `max-age=300, swr=600`; live `max-age=5, swr=5`. The spec's `s-maxage`/
  `immutable` directives are documented but not emitted — `CacheControlMixin`
  supports `max_age` + `swr` only (recorded limitation).
- **C12 — dry-run.** `--dry-run` is threaded through BOTH adapters (parse +
  validate only, `written=0`) so the commands' documented contract is real.

---

## Phase D — Live matches

**Goal:** live fixtures, match state, innings progression, deliveries, an event
stream, and live ingestion from a **licensed managed provider** (or the
ReplayAdapter until a licence exists).

> **Status: implemented** (branch `phase-d`). All ten commit groups landed:
> state machine, durable per-match event log + Redis publication, live write
> path with per-innings rebuild + shadow verify, results/corrections/
> reconciliation, polling + breaker + HMAC push framework, ReplayAdapter +
> provider contract kit, licensing policy + retention, the
> `/matches/live|upcoming|recent` feeds, `/metrics` + alert rules + runbooks,
> and this doc set. See `docs/LIVE_INGESTION.md`. The licensed-provider
> adapter itself remains gated on Phase 0 (`docs/PROVIDER_DECISION.md`).

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

## Phase E — Frontend APIs & real-time updates — **implemented**

**Goal:** cached live-score/scorecard APIs with WebSocket/SSE delivery,
reconnection and recovery (event publication and the per-match `seq` already
exist from Phase D).

**Delivered (branch `phase-e`):**

1. **Redis roles + cache layer** — role-separated Redis databases (cache 0,
   pubsub 3, ratelimit 4, Celery 1/2) with role-appropriate eviction policies;
   key-versioned read-through cache (`apps/core/cache`): namespaces, TTL policy,
   entry store, single-flight, local circuit breaker (fail-open), invalidation
   registry, CDN surrogate-key purge hook, metrics.
2. **HTTP/CDN polish** — state-dependent `Cache-Control` (live `max-age=5,
   swr=5`; completed `max-age=300, s-maxage=3600, stale-while-revalidate=600,
   stale-if-error=86400`; `immutable` past the 24 h reconciliation window);
   strong `ETag`; `Surrogate-Key` header; trusted-proxy client IP
   (`TRUSTED_PROXY_HOPS`); cursor pagination everywhere.
3. **`meta.event_seq`** — every match-scoped REST payload carries the highest
   committed log sequence, captured with a REPEATABLE READ snapshot; the REST →
   realtime subscribe handoff is **exactly-once**.
4. **Realtime core** (`apps/realtime`) — `core.streaming` protocols; hub
   (coalescing ≤1 Hz, immediate correction/terminal flush, bounded queues +
   backpressure drops, gap/backfill via `events_since`, licensing degradation,
   drain with jitter); Redis pub/sub broker with **degraded polling** and
   automatic recovery. `apps/realtime` imports only `core`; the composition root
   (`config/asgi.py` lifespan) injects the `apps/matches` reader/policy and runs
   the broker.
5. **Transports** — SSE (`GET /api/v1/sse/matches/{id}/`, `Last-Event-ID`/
   `from_seq` resume, heartbeats) and WS (`WS /api/v1/ws/matches/{id}/`, ping/
   pong, message limits, close codes 4403/4429/4400/4409/1001/1011) over the
   same hub with byte-identical frames (parity contract).
6. **Policy** — shared Origin allowlist + connection caps (per-IP 8, per-match
   2000, per-node 10000, fail-open) checked before subscription; token-bucket
   throttling (fail-open) with a stricter `deliveries` scope.
7. **Frontend contract + reference client** — `docs/FRONTEND_CONTRACT.md`
   (frames, recovery, close codes, parity) and a dependency-free reference
   client (`tools/reference_client/`, stdlib SSE + minimal RFC 6455 WS).
8. **Observability** — realtime/cache metrics, `ops/alerts/realtime_alerts.yml`
   (9 rules, contract-tested against the metric registry + runbooks),
   `docs/RUNBOOKS.md` realtime/cache sections.
9. **Load tests** — `make test-loadtest-smoke` (CI): cached-read p95 ≤ 200 ms,
   fan-out ≤ 1 s; manual k6 scripts in `loadtests/k6/`.

**Acceptance evidence:** `make check` + `make test-loadtest-smoke` (cached-read
p95 ≈ 0.6 ms, fan-out burst ≈ 0.21 s / immediate ≈ 0.4 ms — budgets 200 ms /
1 s); `tests/phase_e/test_snapshot_stream.py` proves the exactly-once REST →
subscribe rule; `test_realtime_*.py` prove reconnect/replay, `resync_required`,
coalescing, slow-consumer drops, gap backfill, degraded/recovery, and drain.

**Decisions and deferrals (recorded in the Phase E ledger):** career/team
aggregates, standings and rankings stay in **Phase F**; replicas/autoscaling,
channel-layer fan-out across nodes and `AddIndexConcurrently` production
migration stay in **Phase G**. Out of scope for E: JWT/user accounts, push
notifications, real CDN network integration (the purge hook is wired and
no-ops when `CACHE_PURGE_URL` is empty), and any network access in code/tests.
The `realtime` module owns no tables — `match_event_log` lives in `matches`
(DATABASE_SCHEMA wins) and is consumed through `core` protocols (ARCHITECTURE
wins on boundaries).

- **Risks:** fan-out cost on marquee matches (mitigated by SSE/coalescing/CDN).
  **Effort:** 25.
- **DoD:** live scorecard updates in a browser via WS and via SSE fallback —
  met by the reference client + FRONTEND_CONTRACT.

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
  7. Security review (RBAC, secrets, WAF, rate limits); **enforce MFA for
     `admin`/`data_steward`** (required by SECURITY_AND_RELIABILITY §1,
     deferred from Phase B).
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
