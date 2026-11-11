# Phase H — Optional fantasy statistics (execution ledger + plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

Branch: `phase-h` (from `develop` HEAD `ff47393` — Phase F merge). Inline
execution with TDD. Ledger updated per commit; each task is independently
reviewable.

**Goal:** Fantasy points for any match are computed from a versioned, frozen
ruleset and an immutable snapshot of that match's statistics, stored in an
append-only ledger with a full audit chain, recomputed correctly after
corrections and player merges, and served read-only. The same (frozen ruleset
version, match snapshot) always reproduces byte-identical points.

**Architecture:** a pure `apps/fantasy/engine` (declarative rule vocabulary,
data-requirements registry, digest — imports only stdlib + `core`, enforced by
import-linter) sits behind a Django `apps/fantasy` module that owns the 7
§3.8 tables, a snapshot builder, a Tier-2 recompute pipeline + sweep (subscribes
to `match.completed` / `correction.applied`, never `delivery.appended`), an
adjustment service, a ruleset authoring surface, and three read-only public
endpoints. DATABASE_SCHEMA wins on table structure; ARCHITECTURE wins on module
boundaries.

**Tech Stack:** Django 6.1.2, DRF, Celery (eager in tests), PostgreSQL 16,
Redis (Phase E cache), `Decimal` everywhere in point math, import-linter,
pytest + hypothesis.

**Spec:** the user's Phase H prompt (the binding brief, in-session) + the
REVISED docs it names (roadmap Phase H, DATABASE_SCHEMA §3.8/§4.11, DOMAIN_MODEL
§5, ARCHITECTURE §2/§3, API_SPEC §2.6/§6, SCORING_RULES §6, SCORING_ENGINE_API,
SECURITY §4/§1, TESTING_STRATEGY §13.1/§15/§16, RISKS RSK-15, STATISTICS_PIPELINE,
LIVE_INGESTION, REALTIME_AND_CACHING, RUNBOOKS, ENDPOINT_COVERAGE).

## Global Constraints

- **No contests/wagering/payments/wallets/user teams or accounts** — anywhere,
  including stubs. Fantasy = computed player points only.
- **No floats** in point math or storage: `Decimal` (or integer hundredths) end
  to end, documented quantization + rounding mode, `numeric` columns.
- **No `eval` / expression strings** in rules: a validated declarative
  vocabulary only.
- **Frozen rulesets and ledger rows are never updated or deleted** (DB trigger
  + service checks; test proves the trigger for every DB role).
- **No network in tests**; coverage on `fantasy` ≥ 90% line, engine/freeze/
  ledger ≥ 95% branch (`.coveragerc-phaseh`).
- **Phases A–F gates stay on**: `make check` = lint typecheck imports test
  test-scoring test-identity test-phasec test-phasef (+ `test-phaseh` added).
- **schema-ddl + migration-drift stay green**: every new table added to
  `ci/schema_drift_scope.txt`; DATABASE_SCHEMA §3.8 DDL updated in the same
  change as the models; all migrations reversible.
- **DRAFT-only rulesets + `FANTASY_PUBLIC_API_ENABLED=false`** by default
  (FANTASY_PRODUCT_DECISION.md is ABSENT). Seeds are "illustrative, requires
  product-owner approval before freezing", never frozen/default by a seed.
- **Statistical rules stay in `scoring`**; fantasy reads from it and never
  re-derives cricket rules.
- Internal UUIDs only in the public API; merged players 301; no endpoint
  touches `delivery`; queries single indexed reads with query-count + EXPLAIN
  gates.

## Pre-flight: conflicts resolved (record in the final report)

1. **Phase G is NOT merged.** The brief says "G (production, admin API with
   MFA, DB roles, append-only enforcement, drills) are merged", but the repo's
   `develop` is the Phase F merge (`ff47393`) and no `phase-g` branch or commits
   exist: **no admin REST API, no MFA, no DB roles, no append-only triggers**.
   **Resolution:** Phase H implements the fantasy append-only enforcement
   (REVOKE UPDATE/DELETE + blocking triggers) itself — the brief's Task 1
   requires it regardless. The authoring surface (Task 6) uses the existing
   authenticated surface — Django admin + the Phase B role Groups
   (`viewer/operator/data_steward/admin` permission matrix) — plus management
   commands; freeze/set-default gated on the `admin` group, adjustments on
   `data_steward`/`admin`. A REST admin API with MFA is deferred to Phase G
   (recorded; the brief's "uses the Phase G authenticated surface" is satisfied
   by the Django admin + Groups that DO exist). MFA is documented as a Phase G
   dependency.
2. **`docs/OPERATIONS.md` does not exist.** The brief lists it among the docs
   to read; the repo's equivalent is `docs/CRICKET_PERFORMANCE_AND_SCALING.md`
   (read; no fantasy content). **Resolution:** reference that file; no new
   OPERATIONS.md.
3. **`docs/FANTASY_PRODUCT_DECISION.md` ABSENT** → the product/legal gate fails
   closed: every shipped ruleset is DRAFT, `FANTASY_PUBLIC_API_ENABLED=false`
   by default, seeds labelled illustrative. Stated prominently in the report.
4. **§3.8 fantasy DDL is minimal** vs the brief's semantics. Per the Phase F
   precedent (ledger decision 3), DATABASE_SCHEMA wins on the base columns/
   names; the brief-required columns are **added to the doc DDL, the models,
   and `ci/schema_manifest.sql` in the same change** so schema-ddl /
   migration-drift stay green. Extensions: `fantasy_ruleset_version` gets
   `status` (`draft|frozen|retired`), `content_hash`, `frozen_at`, `frozen_by`,
   `is_default`, `auto_recompute` (replacing the bare `is_frozen` as the
   lifecycle column — kept in DDL as `is_frozen` derived flag? **Ruling:** keep
   `is_frozen` per DDL and ADD `status`; `is_frozen` is maintained as
   `status='frozen'` for the drift-check contract); `fantasy_points_ledger`
   gets `is_current` + `superseded_by` (append-only supersession, per the
   brief); `fantasy_calc_audit` gets `snapshot bytea` (compressed JSON) +
   `snapshot_digest` + `trigger` + `is_provisional` + `timings`; 
   `player_match_performance` gets `calc_run_id` FK + `unsupported_rules jsonb`
   + `is_partial`. `fantasy_point_rule.condition_hash` stays a GENERATED column
   (Django 6.1 `GeneratedField` with `MD5(Coalesce(Cast(...)))`; the drift
   manifest compares only the `generated` flag, not the expression).
5. **Merge integration vs append-only ledger.** The brief says "register all
   fantasy tables with Phase B's player-merge registry", but the registry does
   a bulk FK re-point (`UPDATE`), which (a) the append-only ledger trigger must
   block and (b) would violate `ux_pmp (match_id, player_id, ruleset_version_id)`
   when both identities played the same match — the same failure the Phase F
   ledger decision 9 records for aggregates. **Resolution (Phase F precedent):**
   fantasy tables are NOT registered with the generic registry. The
   `player.merged` subscriber supersedes the loser's current ledger entries and
   recomputes the affected matches for the winner from the stored snapshot
   (digest-identical on rebuild; the loser's `player_match_performance` rows
   are superseded, not mutated).
6. **Tier-2 queue.** The brief says subscribers "enqueue a recompute on a
   dedicated `tier2`-class queue". No `CELERY_TASK_ROUTES` exist. **Resolution:**
   add a Celery task `fantasy.recompute_match` (and a provisional variant)
   routed to a `tier2` queue via `CELERY_TASK_ROUTES`; tests run eager. The
   sweep heals matches whose calc runs are missing/stale (worker-killed test).
7. **Live vs final (tension 1).** Final points come from `match.completed` /
   `correction.applied` (Tier-2, after commit). Provisional, if enabled, is a
   separate throttled time-based task behind `FANTASY_PROVISIONAL_ENABLED`
   (default off), reads current Tier-1 rows, labelled `provisional`, never
   writes Tier-2 statistics, superseded by final. Per-delivery events never
   trigger either.
8. **Data availability (tension 2).** A data-requirements registry declares
   each metric's snapshot fields and availability conditions; `compute` skips
   unavailable rules, lists them in `unsupported_rules`, and flags `is_partial`.
   Never guess.
9. **Float arithmetic (tension 3).** `Decimal` end to end; quantization to 2 dp
   with `ROUND_HALF_UP`; `numeric(8,2)` storage; no float in engine, pipeline,
   or API.
10. **Public flag-off response.** When `FANTASY_PUBLIC_API_ENABLED=false`, the
    three fantasy routes are not registered (404), mirroring the
    `ENABLE_METRICS_ENDPOINT` precedent. Contract-tested.
11. **`condition_hash` generated column.** Declared via Django 6.1
    `GeneratedField` (`MD5(Coalesce(Cast("condition", TextField()), Value("")))`,
    `db_persist=True`) so `makemigrations --check` stays clean and the drift
    manifest sees `generated`.

## Task 1: Models, migrations, append-only + frozen-immutability enforcement

**Files:**
- Create: `apps/fantasy/__init__.py`, `apps/fantasy/apps.py`,
  `apps/fantasy/models.py`, `apps/fantasy/migrations/0001_initial.py`,
  `apps/fantasy/admin.py` (stub register — full authoring in Task 6)
- Modify: `docs/CRICKET_DATABASE_SCHEMA.md` (§3.8 DDL), `ci/schema_drift_scope.txt`,
  `config/settings/base.py` (`INSTALLED_APPS` + fantasy settings),
  `config/settings/test.py`, `apps/core/events.py` (add `TOPIC_FANTASY_COMPUTED`),
  `apps/administration/migrations/0006_phase_h_model_permissions.py` (new)
- Test: `tests/phase_h/test_models.py`, `tests/phase_h/test_append_only.py`,
  `tests/phase_h/test_migrations.py`

**Interfaces:**
- Consumes: `apps.core.models.UUIDPrimaryKeyModel`, `apps.core.enums.FormatType`,
  `apps.matches.models.Match`, `apps.players.models.Player`,
  `apps.competitions.models.Series`; `apps.administration` Group seeds (Phase B).
- Produces (used by Tasks 2–7):
  - `FantasyRuleset`, `FantasyRulesetVersion` (with `status`, `content_hash`,
    `is_frozen`, `is_default`, `auto_recompute`, `frozen_at/by`),
    `FantasyPointRule` (`condition_hash` generated), `PlayerMatchPerformance`
    (`total_points`, `breakdown`, `calc_run_id`, `unsupported_rules`,
    `is_partial`, `revision`), `FantasyPointsLedger` (`delta`, `event_type`,
    `reason`, `is_current`, `superseded_by`), `FantasyPointAdjustment`,
    `FantasyCalcAudit` (`input_digest`, `output_digest`, `engine_version`,
    `snapshot`, `snapshot_digest`, `trigger`, `is_provisional`, `timings`,
    `notes`).
  - DB functions/triggers `block_frozen_mutation`, `block_ledger_mutation`,
    `block_adjustment_mutation`; roles `REVOKE UPDATE/DELETE` on ledger +
    adjustment + calc_audit.
  - `apps.core.events.TOPIC_FANTASY_COMPUTED = "fantasy.computed"`.

- [ ] **Step 1: Write the failing model tests** (`tests/phase_h/test_models.py`):
  exact db_table/column names per §3.8 DDL; `ux_fpr` expression unique
  (`(ruleset_version_id, coalesce(role_scope,'any'), event_type, condition_hash)`);
  `ux_frv (ruleset_id, version)`; `ux_pmp (match_id, player_id, ruleset_version_id)`;
  `condition_hash` equals `md5(condition::text)`; version lifecycle transitions.
- [ ] **Step 2: Run and watch fail** — `pytest tests/phase_h/test_models.py` →
  FAIL (no `apps.fantasy`).
- [ ] **Step 3: Add `apps.fantasy` to `config/settings/base.py` LOCAL_APPS +
  fantasy settings block** (all flags default-off: `FANTASY_PUBLIC_API_ENABLED`,
  `FANTASY_PROVISIONAL_ENABLED`, `FANTASY_PROVISIONAL_INTERVAL_SECONDS`,
  `FANTASY_DEFAULT_AUTO_RECOMPUTE`, `FANTASY_ENGINE_VERSION`). Add
  `TOPIC_FANTASY_COMPUTED` to `apps/core/events.py`.
- [ ] **Step 4: Implement the models + `0001_initial` migration** (models mirror
  the extended DDL; expression uniques as verbatim `RunSQL` `CREATE UNIQUE INDEX`,
  the delivery/Phase-F precedent; `condition_hash` as `GeneratedField`). Update
  `docs/CRICKET_DATABASE_SCHEMA.md` §3.8 and `ci/schema_drift_scope.txt`
  (7 tables). `makemigrations --check` clean.
- [ ] **Step 5: Write failing append-only/frozen tests** (`test_append_only.py`):
  the ledger/adjustment triggers reject UPDATE/DELETE for the `admin` DB role
  and the app role; `fantasy_ruleset_version` + its point_rules reject
  UPDATE/DELETE once `is_frozen`; REVOKE blocks at the SQL level.
- [ ] **Step 6: Implement enforcement** — migration `0002_append_only.py`
  (REVOKE UPDATE/DELETE on ledger/adjustment/calc_audit from `web`/`worker`/
  `ingest` + blocking trigger functions for all roles; frozen-immutability
  trigger on version + point_rule). Add `administration/0006` so the 4 Groups
  get fantasy model permissions.
- [ ] **Step 7: Run verification** — `pytest tests/phase_h/test_models.py
  tests/phase_h/test_append_only.py tests/phase_h/test_migrations.py`;
  `make test-nocov -k phase_h`; migration-drift + schema-ddl local gates;
  `make lint typecheck imports`.
- [ ] **Step 8: Commit** — `git add apps/fantasy apps/core/events.py
  apps/administration/migrations docs/CRICKET_DATABASE_SCHEMA.md
  ci/schema_drift_scope.txt config/settings tests/phase_h` →
  `feat(fantasy): ruleset/version/point-rule/performance/ledger/adjustment/calc-audit models + append-only and frozen-immutability enforcement (Task 1)`.

## Task 2: Pure fantasy engine (`apps/fantasy/engine`)

**Files:**
- Create: `apps/fantasy/engine/__init__.py`, `apps/fantasy/engine/types.py`,
  `apps/fantasy/engine/metrics_registry.py`, `apps/fantasy/engine/rules.py`
  (vocabulary + validation), `apps/fantasy/engine/compute.py`,
  `apps/fantasy/engine/digest.py`, `apps/fantasy/engine/requirements.py`
  (data-requirements registry)
- Modify: `pyproject.toml` (import-linter purity contract for
  `apps.fantasy.engine` + add `apps.fantasy` to the domain-never-imports
  administration/ingestion/realtime contract)
- Test: `tests/phase_h/test_engine_*.py` (vocabulary, golden cases, oracle,
  properties), `tests/phase_h/test_engine_purity.py`

**Interfaces:**
- Consumes: stdlib + `apps.core` only (no Django/ORM/clock/randomness).
- Produces (used by Tasks 3–7):
  - `FantasyMatchSnapshot` (frozen): match context (id, format, result_type,
    status, series), per-player lines (batting/bowling/fielding + participation
    flags + dismissal kinds + award flags), `coverage` block, canonical `digest`.
  - `RulesetSpec`, `PointRuleSpec`, `PlayerPoints` (component breakdown),
    `FantasyResult` (per-player totals, `unsupported_rules`, `is_partial`,
    `digest`).
  - `compute(ruleset: RulesetSpec, snapshot: FantasyMatchSnapshot) ->
    FantasyResult` — deterministic, order-independent, no I/O.
  - `validate_ruleset(spec) -> None` (raises precise `RulesetValidationError`
    subclasses), `content_hash(ruleset)` (canonical serialization, stable under
    key reordering), `data_requirements(metric) -> set[str]`.
  - Metric keys (closed registry): `playing_xi`, `runs`, `balls`, `fours`,
    `sixes`, `duck`, `run_milestone`, `strike_rate_band`, `wicket`,
    `bowled_lbw_bonus`, `wicket_haul`, `maiden`, `economy_band`, `dot_ball`,
    `catch`, `multi_catch_bonus`, `stumping`, `run_out`, `player_of_match`,
    `hat_trick`. Operators: `per_unit`, `threshold_bonus`, `tiered`, `flag`,
    `capped`; `tier_mode` `highest_only|cumulative`; scope
    `batting|bowling|fielding|participation`; qualifiers `min_balls`,
    `min_overs`.

- [ ] **Step 1: Write failing engine tests** — golden cases with arithmetic
  comments (runs per unit, milestone thresholds exactly at the boundary, SR
  band just below/at `min_balls`, tiered both modes, wicket haul, maiden,
  economy band, dot ball, catch + multi-catch, stumping, run-out direct vs
  indirect only when data distinguishes, player-of-match, hat-trick, duck,
  playing-XI, capped rule). Property tests: determinism across hash seeds,
  `total == sum(components)`, order independence, positive per-unit never
  decreases with a larger unit, content-hash stability, validation rejects
  every invalid class (unknown metric, overlapping/non-monotonic tiers,
  contradictory rules, missing qualifiers).
- [ ] **Step 2: Run and watch fail** — `pytest tests/phase_h/test_engine_*.py`
  → FAIL (no `apps.fantasy.engine`).
- [ ] **Step 3: Implement the engine** — types, metric registry, rule
  vocabulary + validation, `compute` (Decimal math, ROUND_HALF_UP to 2 dp),
  digest (engine digest approach, stable across hash seeds), requirements
  registry. Policy flags for Super Over (excluded by default), abandoned/no-
  result (no points unless configured), reduced overs, retirements
  (hurt/out), substitutes/impact replacements.
- [ ] **Step 4: Add the import-linter purity contract** in `pyproject.toml`
  (`apps.fantasy.engine` may import only `apps.core`; Django-blocked test).
- [ ] **Step 5: Run verification** — the engine suite + purity test +
  `make imports`.
- [ ] **Step 6: Commit** — `feat(fantasy): pure engine with declarative rule
  vocabulary, data-requirements registry, digest (Task 2)`.

## Task 3: Snapshot builder (`apps/fantasy/services/snapshot.py`)

**Files:**
- Create: `apps/fantasy/services/__init__.py`,
  `apps/fantasy/services/snapshot.py`, `apps/fantasy/selectors.py` (read-side)
- Test: `tests/phase_h/test_snapshot.py`

**Interfaces:**
- Consumes: Task 2 `FantasyMatchSnapshot`; canonical rows — `Match`,
  `MatchResult`, `MatchPlayer`, `PlayerMatchStat` (batting/bowling/fielding
  JSON), `MatchAward`; `apps/statistics` reads (never `delivery`).
- Produces: `build_snapshot(match) -> FantasyMatchSnapshot` (reads the primary
  DB only), `snapshot_payload(snapshot) -> bytes` (canonical JSON, compressed),
  `snapshot_digest(snapshot) -> str` (== snapshot.digest). Summary-only matches
  → reduced coverage (no ball-dependent fields), `is_partial` carried.

- [ ] **Step 1: Write failing tests** — a full ball-by-ball fixture match
  produces a snapshot with coverage; a summary-only match produces reduced
  coverage + partial flag; digest is stable across two builds; snapshot JSON
  round-trips; playing-XI / dismissal kinds / awards are captured.
- [ ] **Step 2: Run and watch fail.**
- [ ] **Step 3: Implement** — read canonical rows via selectors (indexed),
  assemble the frozen snapshot, compress + digest. Retention decision:
  snapshots persist with the calc audit row (Task 4) and are pruned only by a
  documented retention policy (default keep; documented in FANTASY.md).
- [ ] **Step 4: Run verification** — snapshot suite; query-count ceiling.
- [ ] **Step 5: Commit** — `feat(fantasy): snapshot builder with coverage and
  digest (Task 3)`.

## Task 4: Pipeline — recompute, ledger writes, sweep, commands

**Files:**
- Create: `apps/fantasy/services/recompute.py`,
  `apps/fantasy/services/ledger.py`, `apps/fantasy/tasks.py`,
  `apps/fantasy/management/commands/fantasy_backfill.py`,
  `apps/fantasy/management/commands/fantasy_verify.py`
- Modify: `apps/fantasy/apps.py` (wire subscribers), `apps/statistics/services/recompute.py`
  (extend sweep to heal stale fantasy calc runs), `config/celery.py`/settings
  (`CELERY_TASK_ROUTES` → `tier2` queue)
- Test: `tests/phase_h/test_pipeline.py`, `tests/phase_h/test_sweep.py`,
  `tests/phase_h/test_commands.py`, `tests/phase_h/test_merge.py`

**Interfaces:**
- Consumes: `TOPIC_MATCH_COMPLETED` / `TOPIC_CORRECTION_APPLIED` payloads
  (`{"match_id", "source", "digest"}`); `TOPIC_PLAYER_MERGED`; Tasks 2–3.
- Produces: `recompute_match(match, *, provisional=False, trigger=...) ->
  FantasyCalcRunReport`; `enqueue_recompute(match_id)` (Celery `tier2`);
  `sweep_stale_fantasy() -> int`; `fantasy_backfill` / `fantasy_verify`
  commands; `fantasy.computed` topic + cache invalidation registration.

- [ ] **Step 1: Write failing pipeline tests** — idempotent (same snapshot
  digest + version hash = no-op); each run writes a calc_audit row (trigger,
  version hash, snapshot digest, output digest, engine version, timings),
  appends ledger entries, supersedes previous current entries, records
  per-player deltas, publishes `fantasy.computed` after commit; a correction
  that changes an input recomputes with a diff, one that doesn't leaves points
  untouched; ruleset changes never mutate old results (v2 never alters v1);
  worker-killed-then-sweep healing; merge fallout (winner recomputed,
  digest-identical rebuild); `fantasy_verify` detects a deliberately corrupted
  ledger (nonzero exit); backfill resumable + safe twice + dry-run.
- [ ] **Step 2: Run and watch fail.**
- [ ] **Step 3: Implement** — ledger service (append current entries,
  supersede old, sum current per match/player/version), recompute service
  (per auto-recompute version, `select_for_update` per match, advisory lock),
  Celery task routed to `tier2`, sweep extension, commands.
- [ ] **Step 4: Run verification** — pipeline/sweep/commands/merge suites +
  full `tests/phase_h`.
- [ ] **Step 5: Commit** — `feat(fantasy): Tier-2 recompute pipeline, ledger
  supersession, sweep healing, backfill/verify commands (Task 4)`.

## Task 5: Adjustments

**Files:**
- Create: `apps/fantasy/services/adjustments.py`
- Modify: `apps/fantasy/admin.py` (adjustment action, role-gated)
- Test: `tests/phase_h/test_adjustments.py`

**Interfaces:**
- Consumes: `FantasyPointAdjustment`, `TOPIC_FANTASY_COMPUTED`,
  `administration` audit subscriber; role Groups.
- Produces: `apply_adjustment(match_id, player_id, delta, reason, reference,
  actor) -> FantasyPointAdjustment` (bounded |delta|, mandatory reason +
  reference, `data_steward`/`admin` role check), `void_adjustment(adj, actor)`
  (new offsetting entry, never edits the original), adjustment component in
  totals (distinct from computed), preserved across recompute.

- [ ] **Step 1: Write failing tests** — permission matrix (viewer/operator
  denied, data_steward/admin allowed); bounded amount; mandatory reason +
  reference; separate append-only entry (never editing computed entries);
  shown as a distinct component; later recompute preserves adjustments;
  voiding = new offsetting adjustment; audit trail via the existing audit
  subscriber.
- [ ] **Step 2: Run and watch fail.**
- [ ] **Step 3: Implement** — adjustment service + role checks + admin action.
- [ ] **Step 4: Run verification** — adjustments suite + `make check`.
- [ ] **Step 5: Commit** — `feat(fantasy): manual adjustments with role gate,
  audit, offset-void (Task 5)`.

## Task 6: Authoring + admin surface (freeze, set default, retire, validate)

**Files:**
- Create: `apps/fantasy/authoring.py` (service), `apps/fantasy/forms.py`
  (validate/dry-run), extend `apps/fantasy/admin.py`
- Test: `tests/phase_h/test_authoring.py`

**Interfaces:**
- Consumes: Task 1 models, Task 2 `validate_ruleset` / `content_hash` / dry-run
  compute, role Groups.
- Produces: `create_ruleset`, `create_version`, `validate_version` (precise
  errors), `dry_run_version(version, match)` (per-player diff vs another
  version), `freeze_version` (irreversible; computes + records content_hash,
  sets `is_frozen`/`status`; `admin` role), `set_default_version` (`admin`,
  at most one default frozen per (ruleset, format)), `retire_version`
  (new version, never edits), admin actions for all.

- [ ] **Step 1: Write failing tests** — draft CRUD; validation rejects invalid
  rulesets with precise errors; dry-run shows per-player diff; freeze is
  irreversible (frozen version + rules cannot change, DB + service level);
  set-default enforces at-most-one; retire creates a new version and never
  mutates; role matrix (freeze/set-default need `admin`).
- [ ] **Step 2: Run and watch fail.**
- [ ] **Step 3: Implement** — authoring service + admin actions (Django admin
  is the Phase G-deferred authenticated surface; role-gated by Groups).
- [ ] **Step 4: Run verification** — authoring suite; frozen-immutability
  re-verified end to end.
- [ ] **Step 5: Commit** — `feat(fantasy): ruleset authoring — validate,
  dry-run, freeze, set-default, retire (Task 6)`.

## Task 7: Public read API (behind `FANTASY_PUBLIC_API_ENABLED`)

**Files:**
- Create: `apps/fantasy/api/__init__.py`, `apps/fantasy/api/serializers.py`,
  `apps/fantasy/api/views.py`, `apps/fantasy/api/urls.py`
- Modify: `config/urls.py` (register under the flag), `apps/core/cache/invalidation.py`
  (`fantasy.computed` → namespace invalidators)
- Test: `tests/phase_h/test_api.py`, `tests/phase_h/test_api_contract.py`

**Interfaces:**
- Consumes: Task 4 ledger/performance rows, Task 1 models, Phase E cache.
- Produces: `GET /fantasy/rulesets` (+ version detail, `max-age=3600`),
  `GET /fantasy/points` (`match`, `player`, `ruleset_version`; page-number),
  `GET /fantasy/matches/{id}` (all players, ordered, component breakdown,
  `max-age=120`). Every response carries `meta`: ruleset id/version/hash,
  `calc_run_id`, `status` (`final|provisional`), `is_partial`,
  `unsupported_rules`, `as_of`, `label: derived`, and a notice that points are
  computed under the stated ruleset and are not any third party's official
  fantasy points. Envelope, UTC timestamps, error catalog, sparse fields,
  ETag/304, merged players 301, no `delivery` touch, query-count + EXPLAIN
  gates. Flag-off → routes unregistered (404).

- [ ] **Step 1: Write failing API tests** — golden payloads; `meta` labelling
  (ruleset hash, calc_run_id, status, is_partial, unsupported_rules, as_of,
  label, notice); drafts never public; flag-off 404s; ETag/304 + cache headers;
  page-number pagination; 301 for merged players; 400/404 error catalog;
  schemathesis contract on the three endpoints; query-count + no-`delivery`
  guards; `fantasy.computed` invalidates the read cache.
- [ ] **Step 2: Run and watch fail.**
- [ ] **Step 3: Implement** — serializers, views (CachedReadMixin, page-number
  pagination), urls behind the flag, invalidation registration.
- [ ] **Step 4: Run verification** — API + contract suites; `make check`.
- [ ] **Step 5: Commit** — `feat(api): fantasy read endpoints with meta
  labelling, caching, contracts (Task 7)`.

## Task 8: Observability — metrics, alerts, runbooks

**Files:**
- Create: `apps/fantasy/metrics.py`, `ops/alerts/fantasy_alerts.yml`
- Modify: `apps/core/metrics.py` (no change unless a family is missing),
  `docs/RUNBOOKS.md` (fantasy sections), `tests/phase_h/test_observability.py`
- Test: `tests/phase_d/test_observability.py` (extend alert contract)

**Interfaces:**
- Consumes: registered metric names, `docs/RUNBOOKS.md` headings.
- Produces: metrics — `fantasy_recompute_lag` (gauge), `fantasy_queue_depth`,
  `fantasy_sweep_findings`, `fantasy_recompute_seconds` (histogram),
  `fantasy_partial_rate`, `fantasy_unsupported_rule_counts{metric}`,
  `fantasy_ledger_growth`, `fantasy_verify_mismatches_total`. Alerts —
  `FantasyStaleCalc`, `FantasyRecomputeFailures`, `FantasyVerifyMismatch`,
  each with a runbook anchor.

- [ ] **Step 1: Write failing tests** — every alert references a registered
  metric and a runbook heading; metric names follow the facade rules (no
  unbounded-id labels).
- [ ] **Step 2: Run and watch fail.**
- [ ] **Step 3: Implement** — metrics facade, alerts YAML, RUNBOOKS sections
  (wrong-points complaint trace, recompute/backfill, adjustment reversal,
  ruleset freeze mistake → new version, never edit).
- [ ] **Step 4: Run verification** — observability suite + alert contract.
- [ ] **Step 5: Commit** — `feat(obs): fantasy metrics, alerts, runbooks (Task 8)`.

## Task 9: Docs

**Files:**
- Create: `docs/FANTASY.md`
- Modify: `docs/CRICKET_DATABASE_SCHEMA.md`, `docs/CRICKET_DOMAIN_MODEL.md`,
  `docs/CRICKET_API_SPECIFICATION.md`, `docs/CRICKET_BACKEND_ARCHITECTURE.md`,
  `docs/ENDPOINT_COVERAGE.md`, `docs/CRICKET_TESTING_STRATEGY.md`,
  `docs/CRICKET_SECURITY_AND_RELIABILITY.md`,
  `docs/CRICKET_IMPLEMENTATION_ROADMAP.md` (Phase H status + decisions +
  effort deviation), `.env.example`

**Interfaces:** no code; documents the Task 1–8 surface.

- [ ] **Step 1: Write `docs/FANTASY.md`** — rule vocabulary with worked
  examples, data requirements table, policy flags, versioning/freeze lifecycle,
  ledger + adjustment model, reproducibility guarantee, live/provisional
  decision, retention of stored snapshots, DRAFT-only + flag-off stance.
- [ ] **Step 2: Update the named docs** — schema (already in Task 1), domain
  model §5, API spec §2.6, architecture module row, endpoint coverage audit
  (3 fantasy rows ✅ live), testing strategy Phase H additions, security
  (append-only enforcement + roles), roadmap Phase H (status, decisions list,
  effort deviation if any).
- [ ] **Step 3: Run verification** — `make check` (docs changes are lint-clean);
  re-read for consistency.
- [ ] **Step 4: Commit** — `docs: fantasy module docs + roadmap/status/decision
  updates (Task 9)`.

## Task 10: Coverage gate, performance benchmarks, whole-branch review prep

**Files:**
- Create: `.coveragerc-phaseh`
- Modify: `Makefile` (`test-phaseh` + wire into `check`), `pyproject.toml`
  (hypothesis profile if needed), `scripts/fantasy_benchmark.py` (report-only)

**Interfaces:** consumes the whole Task 1–9 surface.

- [ ] **Step 1: Write the coverage gate** — `.coveragerc-phaseh`: `include`
  `*/apps/fantasy/engine/*.py`, `*/apps/fantasy/services/freeze*.py`,
  `*/apps/fantasy/services/ledger.py`, `*/apps/fantasy/services/adjustments.py`
  at ≥ 95% branch; app-wide `apps/fantasy` ≥ 90% line. Wire `test-phaseh` into
  `make check`.
- [ ] **Step 2: Run the gate** — fix uncovered branches until green.
- [ ] **Step 3: Benchmarks (report numbers)** — compute time per match (p95),
  backfill of ~1,000 synthetic matches (extrapolate linear), endpoint p95
  uncached/cached, EXPLAIN gates.
- [ ] **Step 4: Run the full suite** — `make check`, migration-drift +
  schema-ddl local gates, import-linter.
- [ ] **Step 5: Commit** — `feat: fantasy coverage gate + benchmarks (Task 10)`.

## Progress

| Task | Status | Commit |
|---|---|---|
| 1 Models + migrations + enforcement | | |
| 2 Pure engine | | |
| 3 Snapshot builder | | |
| 4 Pipeline + sweep + commands | | |
| 5 Adjustments | | |
| 6 Authoring + admin | | |
| 7 Public read API | | |
| 8 Observability | | |
| 9 Docs | | |
| 10 Gate + benchmarks | | |

## Notes / risks

- Gates: `make check` green (incl. the new `test-phaseh`); schema-ddl +
  migration-drift green (57 scoped tables); import-linter contracts extended
  (fantasy engine purity + domain-never-imports administration/ingestion/
  realtime); no network in tests; no floats; no contests/user entities.
- Oracle: an independent plain-Python reference calculator over raw fixture
  rows (Decimal) must agree with the engine for every rule type.
- The product/legal gate outcome (FANTASY_PRODUCT_DECISION.md absent) is stated
  prominently in the final report: DRAFT-only rulesets,
  `FANTASY_PUBLIC_API_ENABLED=false` default, seeds labelled illustrative.
- Phase G is absent from the repo (verified): MFA, the admin REST API, DB roles
  and generic append-only enforcement are Phase G tickets; fantasy ships its own
  append-only/frozen triggers and uses Django admin + the existing role Groups.
