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
| 2 Recompute pipeline + sweep | done | (next) |
| 3 Standings + NRR | — | — |
| 4 Leaderboards/records/H2H/form/compare | — | — |
| 5 Rankings | — | — |
| 6 Search | — | — |
| 7 Read API | — | — |
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
