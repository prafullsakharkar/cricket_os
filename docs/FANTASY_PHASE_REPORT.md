# Phase H — Fantasy statistics: final report

Branch `phase-h` (`54c309a`…`2ee2dd2`, 10 tasks, each a separate commit).
Executed inline with TDD under the SDD ledger
(`.superpowers/sdd/2026-11-11-phase-h-fantasy/progress.md`). This report covers
the plan's required items (a)–(g), the product/legal gate, and the effort
deviation.

## (a) Built items

| # | Item | Commit |
|---|---|---|
| 1 | 7 §3.8 tables + expression uniques (`ux_fpr`, `ux_frv_default`) + append-only & frozen-immutability DB triggers (0002) + administration permissions (0006); migrations reversible | `54c309a` |
| 2 | Pure stdlib-only engine: closed 22-metric registry, 5 operators, Decimal end-to-end (`ROUND_HALF_UP`, 2 dp, no floats), content digest, no eval/expression strings, import-linter contract #10; policies JSON column (0003) | `f9ad8c0` |
| 3 | Snapshot builder + selectors (4 indexed queries), coverage flags, derived fielding credits, gzip-compressed canonical payload | `7466a8b` |
| 4 | Tier-2 pipeline: idempotent `recompute_match` (advisory lock + `select_for_update`), append-only ledger with derived supersession (`calc_run_id` + `version_hash`, 0004), sweep/backfill/verify commands, `match.completed` / `correction.applied` / `player.merged` wiring, `fantasy.computed` cache invalidation | `67bfb32` |
| 5 | Adjustments: role-gated (`data_steward`/`admin`), `\|delta\| ≤ 1000.00`, 2 dp, mandatory reason + external reference, append-only void (offsetting entry), audit + cache invalidation, re-fold on recompute; 0005 `reference` NOT NULL | `923d7bd` |
| 6 | Authoring + admin: create/validate/dry-run (writes nothing)/freeze/set-default/retire-by-fork; admin actions + confirm/dry-run templates; whole-version validation before any write | `eb0d7f6` |
| 7 | Flag-gated public read API (`/fantasy/rulesets` + version detail, `/fantasy/points`, `/fantasy/matches/{id}`) with meta `label: derived` + third-party notice, calc identity, drafts-never-public, merged→301, ETag/304, read cache, OpenAPI contract | `7856e55` |
| 8 | Observability: 9-metric family + `FantasyStaleCalc` / `FantasyRecomputeFailures` / `FantasyVerifyMismatch` alerts + runbook anchors; phase-d alert contract extended | `a61dca5` |
| 9 | Docs: `docs/FANTASY.md` + schema/domain/API/architecture/endpoint-coverage/testing/security/roadmap updates; `.env.example` | `36a0797` |
| 10 | Coverage gate (`backend/.coveragerc-phaseh`, `test-phaseh` in `make check`), 65 new branch/edge tests (318 total), `backend/scripts/fantasy_benchmark.py` (report-only) | `2ee2dd2` |

## (b) Commands + results per acceptance criterion

| Criterion | Command | Result |
|---|---|---|
| `make check` green (lint/typecheck/imports/test/scoring/identity/phasec/phasef/phaseh) | `make check` | ✅ lint clean, mypy clean, import-linter clean; `test` 1668 passed (94.98%); scoring 172 (99.07%); identity 40 (100%); phasec 184 (98.85%); phasef 137 (93.96%); **test-phaseh 318 (100% line + 100% branch)** |
| Fantasy app ≥ 90% line | `make test-phaseh` (first leg) | ✅ 100.00% line on `apps/fantasy` |
| Engine/authoring/ledger/adjustments ≥ 95% branch | `make test-phaseh` (second leg) | ✅ 100.00% branch (`backend/.coveragerc-phaseh`) |
| Migration drift green | `makemigrations --check --dry-run` + manifest diff | ✅ "No changes detected"; **NO-DRIFT across 57 scoped tables** (all 7 fantasy tables scoped) |
| schema-ddl green | `extract_ddl.py \| psql -d ref_db` + `backend/backend/scripts/ci/ddl_checks.sql` | ✅ reference DDL applies cleanly (`ON_ERROR_STOP=1`), structural guards pass |
| No floats / no eval / Decimal end-to-end | engine purity contract + golden/oracle/property tests | ✅ import-linter forbids `engine → apps.core`; engine tests pass |
| Append-only + frozen immutability (every role) | `test_append_only.py` / `test_migrations.py` trigger tests | ✅ UPDATE/DELETE raise for every role incl. table owner; frozen version + rules immutable |
| All migrations reversible | `test_migrations.py` reverse/reapply | ✅ fantasy tables drop + restore cleanly |
| No network in tests | CI/test config | ✅ no network-dependent tests |
| No contests/wagering/user entities | whole-branch review | ✅ none (computed player points only) |
| Public API off by default | `FANTASY_PUBLIC_API_ENABLED` default | ✅ `False` in `base.py`; URLs unregistered → 404 |

## (c) Benchmarks + hardware

Environment: Intel Xeon Platinum 8273CL @ 2.20 GHz (112 vCPU host), 251 GiB RAM,
PostgreSQL 17.11, Python 3.14.6, Django 6.1.2, Redis (cache under test =
in-process LocMem). Corpus: 1,000 synthetic completed matches (T20I, 4 players
each, realistic 9-rule frozen ruleset), scratch DB. `backend/scripts/fantasy_benchmark.py`
(report-only, token-bucket-aware sampling).

| Measurement | Result |
|---|---|
| Tier-2 backfill (1,000 matches) | 32.16 s → **31 matches/s** |
| Linear extrapolation | 10,000 matches ≈ 5.4 min; 100,000 ≈ 53.6 min |
| Compute per match (frozen `calc_audit.timings.compute_ms`, n=1000) | **p50 0.22 ms, p95 0.59 ms, p99 0.68 ms, max 1.22 ms** |
| `/fantasy/rulesets` endpoint | uncached p95 **9.25 ms**; cached p95 **3.20 ms** (n=50) |
| `/fantasy/points?match=` endpoint | uncached p95 **17.42 ms**; cached p95 **3.33 ms** (n=50) |
| `/fantasy/matches/{id}` endpoint | uncached p95 **17.10 ms**; cached p95 **2.30 ms** (n=50) |
| EXPLAIN gates | `player_match_performance` reads are **Index Scans on `ux_pmp`** (no seq scan) — PASS |
| EXPLAIN observation | latest-calc-run lookup on `fantasy_calc_audit` is a seq scan (the §3.8 DDL adds no index); fine at this scale, noted for a larger corpus |

## (d) Conflicting / open decisions (one-line reasons)

1. **Phase G absent** → fantasy ships its own append-only/frozen triggers and
   uses Django admin + the existing role Groups; a REST admin API with MFA is
   deferred to Phase G.
2. **`docs/OPERATIONS.md` absent** → referenced `CRICKET_PERFORMANCE_AND_SCALING.md`
   instead.
3. **`FANTASY_PRODUCT_DECISION.md` ABSENT** → gate fails closed (see (e)).
4. **§3.8 DDL minimal vs brief** → DATABASE_SCHEMA wins on base columns; brief
   columns added to doc DDL + models + drift manifest in the same change.
5. **Merge integration vs append-only ledger** → fantasy tables are NOT in the
   generic merge registry (bulk FK re-point would hit the append-only trigger
   and `ux_pmp`); `player.merged` supersedes + recomputes the winner instead.
6. **Tier-2 queue** → `fantasy.recompute_match` routed to a `tier2` queue via
   `CELERY_TASK_ROUTES`; eager in tests.
7. **Live vs final** → final from `match.completed`/`correction.applied`;
   provisional is a separate time-based task behind `FANTASY_PROVISIONAL_ENABLED`
   (default off), never touches final points.
8. **Ledger supersession** → append-only forbids UPDATE, so "current" is
   derived from the latest calc run (`calc_run_id`); `is_current`/
   `superseded_by` stay informational.
9. **Adjustments storage** → live only in `fantasy_point_adjustment` (own
   ledger); computed + net adjustment = combined total.
10. **Void semantics** → void = new offsetting entry (original never edited).
11. **Default lifecycle** → a frozen version is immutable, so the default is set
    on a draft and frozen with `is_default` intact.
12. **Retire semantics** → forks a new draft; never mutates the frozen original.
13. **API flag re-check** → every view re-checks the flag in `dispatch` (404),
    so flag-off is deterministic even with stale cache entries.
14. **Route convention** → no trailing slash (statistics convention).
15. **Points meta** → summarises the latest calc run among the page's rows;
    match endpoint presents the primary version (format default → generic
    default → latest non-provisional run).
16. **Points pagination** → invalid `page` answers 404 `not_found` (not 400);
    recorded, accepted.
17. **Failures metric** → `fantasy_recompute_failures_total` added beyond the
    plan's Task 8 list because the alert needs a failure signal.
18. **Sweep lag semantics** → `fantasy_recompute_lag` stamps the observed
    backlog (before healing), mirroring `statistics_watermark_lag`.
19. **Coverage-gate file set (D17)** → the plan globbed `services/freeze*.py`,
    but the freeze lifecycle lives in `authoring.py`; the gate globs
    `engine/*.py`, `authoring.py`, `ledger.py`, `adjustments.py`.
20. **Benchmark scrub of append-only rows (D18)** → ledger/adjustment/audit and
    frozen rows are undeletable by design; the benchmark's `--reset` drops +
    re-applies the fantasy migrations.

## (e) Product/legal gate outcome

**`docs/FANTASY_PRODUCT_DECISION.md` is ABSENT** → the gate fails closed and
Phase H ships accordingly:

- Every shipped ruleset is **DRAFT only**; nothing is frozen/default by a seed.
  Seeds (when added) are labelled illustrative and require product-owner
  approval before freezing.
- `FANTASY_PUBLIC_API_ENABLED` defaults to **`false`** — the read endpoints are
  not registered (404 at the URL level) until a product/legal decision enables
  them.
- The project **never copies commercial operators' point tables**; the example
  rules in docs are illustrative.
- Contests, wagering/wallets/payments, user teams/entries, captain multipliers,
  user leaderboards and notifications are **out of scope** — fantasy is computed
  player points only.

## (f) Unsupported-rule metrics and what data unlocks them

The engine computes a rule only when the match snapshot carries its inputs
(`SnapshotCoverage` flags); otherwise the rule is reported in
`unsupported_rules` and the run is flagged `is_partial` (never guessed as
zero). For a summary-only match the unsupported set is:

| Metrics | Data requirement (flag) | What unlocks them |
|---|---|---|
| `balls`, `strike_rate_band`, `maiden`, `economy_band`, `dot_ball` | `has_ball_data` (false when any stat row is `is_partial`) | Ball-by-ball / aggregated ball data (delivery-level counts per player) |
| `catch`, `multi_catch_bonus`, `stumping`, `run_out` | `has_fielding_data` | Fielding counts (stat-row `fielding` JSON, or dismissal fielder credits in the batting lines) |
| `run_out_direct`, `run_out_indirect` | `has_fielder_credit_types` (implies fielding data) | Delivery-level attribution of *who* threw and *who* received for each run-out |
| `duck`, `bowled_lbw_bonus` | `has_dismissal_kinds` | Dismissal kinds (already present in summary batting lines — only unavailable on partial rows) |

`hat_trick` and `player_of_match` have no data-requirement flag (available from
summary rows when present).

## (g) Unverified / deferred items

- **REST admin API + MFA + DB roles + drills** — deferred to Phase G (absent
  from `develop`; fantasy enforces append-only/frozen itself).
- **Provisional points** — implemented and tested behind
  `FANTASY_PROVISIONAL_ENABLED` (default off); not exercised in production.
- **Public API** — contract-tested under `config.settings.test`
  (`FANTASY_PUBLIC_API_ENABLED=True`); not live behind the product gate.
- **Latency budgets** — the benchmarks are report-only; no p95 budget is
  enforced by CI for the fantasy endpoints (the Phase E smoke gate covers the
  live-matches read path).
- **Real-concurrency capacity** — endpoint numbers are in-process
  (`django.test.Client`); no k6-style concurrent-load run for the fantasy
  endpoints.
- **Calc-audit index** — the latest-run lookup seq-scans `fantasy_calc_audit`
  (no index in the §3.8 DDL); acceptable at 1k matches, revisit before large
  corpora.
- **Seeds** — none shipped (product gate); no frozen/default production ruleset
  exists to validate end-to-end on real data.

## Effort deviation vs the 25 planned dev-days

The plan budgeted **25 dev-days**. The actual effort is estimated at
**≈ 10–12 dev-days equivalent** (a ~50–60% under-run), based on the delivered
volume: 10 commits, 90 files, +14,153 lines (≈ 5,449 in `apps/fantasy`,
≈ 6,707 in `tests/phase_h`), 318 Phase H tests, 57-table migration-drift +
schema-ddl green. Reasons: (1) the plan's 25 days priced in unknowns — the
drift gates, oracle and property tests landed cheaper than feared; (2) an
agentic/automated workflow compressed the mechanical steps. This is an
estimate — wall-clock was not tracked per task; treat it as indicative, and the
roadmap points here for the figure.

---

*Generated at the end of Task 10. Merge of `phase-h` → `develop` requires
explicit approval (shared-branch side effect).*
