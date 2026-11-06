# Phase F — Statistics & analytics (execution ledger)

Branch: `phase-f` (from `phase-e` HEAD `2616132`; phase-e is NOT yet merged into
`develop`, and Phase F builds on E's cache/event/throttling surface, so the F
branch cuts from E — a merge to `develop` is a shared-branch side effect that
will be requested at the end). Inline execution with TDD.
Ledger updated per commit; each group is independently reviewable.

The task brief (this phase's spec) is the user's Phase F prompt: 10 task
groups, acceptance criteria 1-9, deliverable = commit in reviewable steps +
final (a)-(f) report. Sources of truth and conflict precedence are per the
brief: DATABASE_SCHEMA wins on tables, SCORING_RULES/engine docs win on rule
behaviour, ARCHITECTURE wins on module boundaries.

## Pre-flight: conflicts resolved (record in final report)

1. **Base branch.** `phase-e` (2616132) is ahead of `develop` and unmerged.
   Phase F depends on E (cache mixin, invalidation registry, events, throttling,
   licensing). **Resolution:** branch `phase-f` from `phase-e` HEAD; merging
   phase-e into develop is deferred to the end (side-effect gate).
2. **`tournament_standings` module ownership.** ARCHITECTURE's module catalog
   lists `statistics` owning PlayerCareerStat/TeamStat/HeadToHead/Leaderboard/
   FormTrend but does NOT assign `tournament_standings`. **Resolution:** assign
   it to `apps/statistics` — it is a precomputed aggregate produced by the Tier-2
   recompute pipeline from `match_result` rows (like `team_stat`); `competitions`
   owns the source models (`Series`, `MatchStage`) it references. Update
   ARCHITECTURE (module catalog + dependency row).
3. **Watermark/labelling columns absent from the schema DDL.** The brief
   requires every aggregate row to carry `rule_version`, `computed_at`, an
   `inputs_digest`/source-revision watermark, coverage (matches counted,
   first/last match date, `is_partial`, confidence) and the official/derived
   label; the §3.7 DDL has only `revision` + `computed_at`. **Resolution:**
   DATABASE_SCHEMA wins on the base columns/names; the brief's required columns
   are added to the doc DDL, the models, and `ci/schema_manifest.sql` in the same
   change so `schema-ddl`/`migration-drift` stay green. Added columns:
   `rule_version text`, `inputs_digest text`, `source_kind text`
   (`computed`|`provider`), `matches_counted int`, `partial_matches int`,
   `first_match_date date`, `last_match_date date`, `is_partial bool`.
4. **Rankings idempotency key.** Brief says "idempotent by (gender, format,
   category, as_of_date, rank) per the schema"; the §3.7 DDL unique is
   `ux_ranking (player_id, gender, format_type, category, as_of_date)`.
   **Resolution:** the schema wins — one row per player per (gender, format,
   category, date); a re-import upserts by that key.
5. **`stats.updated`/`standings.updated` event schemas.** These are new bus
   topics, not new durable per-match event-log types (new realtime streams are
   out of scope). **Resolution:** add `stats.updated`, `standings.updated`,
   `ranking.updated` to `apps/core/events.py` as in-process post-commit topics,
   and register invalidators (namespaces `standings:{series_id}`,
   `statistics:*`, `rankings:...`) in the Phase E invalidation registry. The
   durable `apps/matches/event_schemas.py` catalogue is unchanged.
6. **`get_rankings` + provider decision.** `docs/PROVIDER_DECISION.md` does not
   exist (RSK-12 open). `get_rankings(gender)` is already on the `ProviderAdapter`
   protocol; `ReplayAdapter` returns `[]`. **Resolution:** implement a
   fixture-backed rankings adapter (recorded fixtures shipped in-repo) and the
   full pipeline; never guess a vendor API shape. `/rankings` calls
   `check_license(FEATURE_RANKINGS, provider)` → `403 license_required` unless
   the provider's `LicensedContentPolicy` allows display; attribution in `meta`.
   `FEATURE_RANKINGS` added to `apps/providers/licensing.py`.
7. **`docs/HISTORICAL_IMPORT.md` missing.** The Phase C import design lives in
   `CRICKET_DATA_INGESTION.md` + `LIVE_INGESTION.md`. **Resolution:** reference
   those; no new historical-import doc.
8. **Aggregate-level "official/derived" label.** SCORING_RULES §4 classifies
   per-metric official vs derived; the aggregate ROW label is about source.
   **Resolution:** every aggregate row stores `source_kind` (`computed` vs
   `provider`) + `rule_version`; per-metric classification comes from the
   engine's `Classified` mechanism at render time; API `meta` carries
   `official_source` + `as_of` + `coverage` + attribution.
9. **Merge + aggregate rows.** `apps/players/services/merge.py` re-points
   registered FKs inside the merge transaction. **Resolution (refined at
   implementation):** registering the Phase F *aggregate* FKs (player_career_stat,
   form_trend, player_ranking) for re-pointing would violate their expression
   uniques on any real merge (both identities have career rows — the same
   failure `test_failed_reference_repaint_rolls_back` proves for squads).
   Instead: register the **source** table (`player_match_stat.player`) so the
   loser's per-match lines follow the winner; the `player.merged` subscriber
   deletes the loser's aggregate rows (career/form/leaderboard/ranking) and
   recomputes the winner's scopes from source (digest-identical on rebuild).
   Recorded as a conflict in the final report.
10. **Dependency-set changes on correction.** To make "include the previous set
    if the result or participants changed" testable and correct, each match gets
    a watermark row (`statistics_watermark`) recording `source_digest` and the
    `aggregate_keys` it contributed to; a correction recomputes the union of
    previous+current keys (recompute-from-source naturally drops a removed match;
    a key with no remaining source deletes its row).
11. **`stat_scope`/`ranking_category` enums.** Django models use
    `TextField(choices=...)`; the drift manifest reports enums as `text`, so no
    PG enum migration is needed (existing `format_type`/`match_status` precedent).

## Commit groups

1. **Models + migrations** — the 7 §3.7 tables + `statistics_watermark` helper,
   watermark/label columns, reversible migrations; DATABASE_SCHEMA §3.7 DDL
   updated; `ci/schema_drift_scope.txt` + `ci/schema_manifest.sql` updated;
   model tests.
2. **Tier-2 recompute pipeline + sweep** — dependency sets, recompute-from-
   source, advisory-lock serialization, watermark + sweep healing (crash test),
   merge integration, `rebuild_statistics` command, `stats.updated`/
   `standings.updated` topics + invalidation, backfill digest==incremental test.
3. **Standings + NRR** — standings recompute per series/stage/group with
   `points_system` config, form string, `qualify_status` passthrough, NRR edge
   cases, reconciliation hook (`reconciliation_mismatch` DLQ).
4. **Leaderboards / records / H2H / form / compare** — leaderboard population
   with rank policy, records catalogue, H2H canonical ordering, form windows,
   compare validation + logic.
5. **Rankings** — `rankings` app: model (G1), fixture-backed adapter, ImportJob
   pipeline, idempotency + history + trend, DLQ for unresolved players, licensing
   degrade; `/rankings` + `/players/{id}/rankings`.
6. **Search** — `search` app: trigram over normalized names, weighted ranking,
   safety (min/max length, statement_timeout, result cap), EXPLAIN gate, golden
   relevance set.
7. **Read API** — all Phase F endpoints with envelope/UTC/fields/ETag/cache/
   pagination/invalidation; query-count + no-`delivery` guards; merged-player
   301; contract tests.
8. **Endpoint coverage audit** — `docs/ENDPOINT_COVERAGE.md` + thin unassigned
   endpoints (`/matches`, `/matches/{id}/overs|squads|facts`,
   `/teams/{id}/squad`, `/series/{id}/matches|squads|venues`, `/archives`).
9. **Observability** — Phase F metrics, `ops/alerts/statistics_alerts.yml`,
   RUNBOOKS sections.
10. **Docs** — `docs/STATISTICS_PIPELINE.md`, `docs/SEARCH.md`, roadmap Phase F
    decisions + status + effort deviation, DATABASE_SCHEMA/DOMAIN_MODEL/API/
    ARCHITECTURE/PERFORMANCE/SCORING_RULES/TESTING_STRATEGY updates.

## Progress

| Group | Status | Commit |
|---|---|---|
| 1 Models + migrations | done | 5075536 |
| 2 Recompute pipeline + sweep | done | 700baa6 |
| 3 Standings + NRR | done | 196e1cc |
| 4 Leaderboards/records/H2H/form/compare | done | 5a4a4aa |
| 5 Rankings | done | 0707517 |
| 6 Search | done | 1589333 |
| 7 Read API | done | — |
| 8 Endpoint coverage audit | — | — |
| 9 Observability | — | — |
| 10 Docs | — | — |

## Notes / risks

- Gates: `make check` green (incl. new `test-phasef` coverage target ≥90%, and
  ≥95% branch for recompute/watermark, standings, search ranking); schema-ddl +
  migration-drift workflows green; import-linter contracts extended for
  statistics/rankings/search; no network in tests; no DLS/rating computation.
- Oracle: an independent plain-Python reference implementation over raw fixture
  rows must agree with the pipeline (averages, SR, economy, best figures, NRR,
  points) — guards against engine+pipeline sharing a bug.
- Latency: report p95 (uncached) for leaderboard, points-table, player-stats,
  search; backfill time for ~1,000 matches with extrapolation note.
- `docs/PROVIDER_DECISION.md` absent → fixture-backed rankings adapter only.
- **Group 2 rulings (implementation):**
  - Merge refinement (decision 9, cost if wrong: a re-pointed `player_match_stat`
    with both identities in one match would violate `ux_player_match_stat`): the
    generic merge registry is NOT used for `player_match_stat`; the
    `player.merged` subscriber re-points source rows itself, merging same-match
    rows' lines before recomputing the winner.
  - Two-key advisory locks are `(int, int)` in PostgreSQL (no int8 overload);
    `_lock_pair` folds SHA-256 halves to signed 32-bit and casts `::int`.
  - `stats.updated`/`standings.updated` publish on-commit, so the publish test
    uses `django_db(transaction=True)` (the `test_events.py` precedent).
  - CAREER-scope digests are global: recompute reflects every source row present
    at recompute time (add-then-remove test sequences around that).
- **Group 3 rulings (implementation):**
  - `points_system` overrides only the five points fields via
    `dataclasses.replace` on the format's frozen `RuleConfig`; the effective
    points config is folded into the standings inputs digest, so a
    `points_system` edit re-triggers recompute.
  - Form string: per-team result chars in match order, `_form_string` takes the
    last 5 and reverses → most-recent-first `W/L/T/D/N` (window 5).
  - `qualify_status`/`no_of_qual` are steward-set passthroughs: preserved on
    every recompute; only fresh rows get `no_of_qual` from `points_system`.
  - Reconciliation scope key `standings:{series}:{stage|'-'}`; the hook
    recomputes fresh then diffs (NRR rounded to 3 dp on both sides); public
    `report_mismatch` alias added to the synchronization service so statistics
    never imports ingestion (dependency inversion preserved).
  - Group 1 follow-up surfaced by the full suite: the Phase F aggregate +
    `rankings` models' permissions were missing from the admin "every
    permission" group → `administration/0005_phase_f_model_permissions`.
- **Group 4 rulings (implementation):**
  - Leaderboards read the digest-validated `PlayerCareerStat` aggregates, never
    raw matches; `ux_leaderboard` folds `rank` into the unique, so a scope is
    delete + bulk-inserted atomically when its inputs digest changes (rank
    shifts never collide); ties break on (value, player id) — stable.
  - `recompute_keys` gained dependency-aware ordering: aggregates (career/team/
    h2h/standings) before the leaderboards that read them (a team leaderboard
    must see freshly recomputed `TeamStat` rows).
  - Team leaderboards were dropped at implementation: `ux_leaderboard` does not
    distinguish player vs team rows, and the spec's `/statistics/teams` is a
    *records* endpoint — `records.team_records` covers it (conflict recorded).
  - Records catalogue is on-demand (no table): milestones from aggregates,
    innings-level records (highest score, best bowling figures, highest team
    total) from the per-match source rows; averages qualification-gated
    (min 20 innings / 20 wickets / 500 balls).
  - Fielding metrics have no per-match source yet, so fielding leaderboards are
    honest empties (catalogue present, zeros excluded).
- **Group 5 rulings (implementation):**
  - The fixture-backed adapter is registered under its own provider code
    `fixture` (not `replay`): `get_rankings` is part of the `ProviderAdapter`
    protocol, and overwriting the `replay` factory would break match replay.
    The corpus lives at `tests/fixtures/rankings/{men,women}.yaml`
    (`settings.RANKINGS_FIXTURE_DIR`); missing corpus = honest empty list.
  - `FEATURE_RANKINGS` gates the whole import (403 `license_required` when the
    provider's `LicensedContentPolicy` does not set `rankings_allowed`); the
    `fixture` policy allows rankings, `cricsheet` (default policy) does not.
  - Trend is derived from the previous dated snapshot (`"+N"` gained /
    `"-N"` dropped / `"0"` unchanged, `None` without a baseline) unless the
    row carries a provider trend; best rank/rating accumulate over the full
    history for (player, gender, format, category).
  - Unresolved provider player ids are reported through the dependency-inverted
    reporter hook (`register_unresolved_reporter`); `apps.ingestion` wires the
    `rankings_import` `IngestionError` DLQ writer in `register_default_reporters`
    — `apps.rankings` never imports `apps.ingestion` (import-linter green).
  - `license_meta` now also exposes the `rankings` flag (existing phase-d
    licensing test updated to the new key).
  - Test-isolation note: phase-d tests `clear_registry()` (wiping every
    adapter); the rankings suite re-registers the fixture adapter in an
    autouse fixture, matching the explicit-registration convention there.
- **Group 6 rulings (implementation):**
  - `apps.search` is a **service-only app** (no tables): it reads the domain
    tables through their GIN trigram indexes. The `%` operator (index-backed)
    is used exclusively — never `icontains`/LIKE — and the recall threshold is
    the pg_trgm session default (0.3), matching the documented behaviour.
  - Weighted ranking: `score = similarity * type_weight + exact(0.25)/prefix(0.1)
    boost`; weights player 1.0 / team 0.95 / series 0.9 / competition 0.85.
    Ties break by (type order, name) — deterministic.
  - Safety: query length 2..100 (`ValueError` → `invalid_query`), per-statement
    `SET LOCAL statement_timeout` (default 200 ms) around the whole search, and
    a per-type candidate fetch (50) + merged `limit` cap (≤100).
  - The EXPLAIN gate runs with `enable_seqscan=off` and asserts the plan names
    `ix_player_name_trgm`/`ix_team_name_trgm` — the only way the planner can
    satisfy `%` with seqscan disabled — rather than relying on planner choices
    against live stats (the phase-c trigram test precedent).
  - The golden relevance set reflects real trigram behaviour: a short query
    that is only a fragment of a long name does not clear the 0.3 threshold
    (verified against PG; "india" matches only the exact team). The corpus
    asserts exact-team-beats-prefix-series and surname-prefix resolution.
  - `/search` endpoint is Group 7 (Read API); Group 6 delivers the service,
    safety, EXPLAIN gate and golden set it is built on.
- **Group 7 rulings (implementation):**
  - 14 endpoints: player statistics/form/compare, team statistics, match
    statistics, series points-table + stats, batting/bowling/fielding
    leaderboards, `/statistics/teams`, `/records/players`, `/records/teams`,
    `/head-to-head`, `/search`. Views live in `apps.statistics.api` +
    `apps.search.api`; the reads in `apps.statistics.services.reads`
    (views → services is allowed by import-linter; no selector module needed).
  - Series leaderboards needed SERIES-scope `leaderboard` rows: the recompute
    dependency set now adds `leaderboard:player:SERIES:{series}:-|{fmt}` and
    `recompute_player_leaderboard`/`_rebuild_scope` accept `scope_ref_id`
    (digest-gated delete+bulk-insert per series).
  - The leaderboard endpoints filter rows to their metric family, so
    `/statistics/fielding` stays an honest empty list until a per-match
    fielding source exists (a bare "all metrics" read would leak bowling rows).
  - Player-scoped sub-resources (statistics/form/rankings) answer `301` for
    merged ids via `resolve_player_redirect`, mirroring the profile route.
  - Cache version namespaces reuse the Group 2/3 invalidators: player-scoped
    `statistics:player:{id}` (stats + form), `statistics` (leaderboards +
    records + h2h + team/series stats), `standings:{series}` (points table),
    `statistics:match:{id}` (match stats), `search` (search).
  - Guards: query-count ceilings (≤6) + a no-`delivery`-table scan assertion
    across a representative endpoint set; `InvalidQuery` (400) for bad scope/
    metric/compare/search params; unknown entity ids → 404.
