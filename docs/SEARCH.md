# Cross-entity search

`GET /search?q=&types=` — one endpoint, four entity types (player, team,
series, competition), index-backed trigram matching. Implemented in
`apps/search/` (service-only app; the read API view lives in
`apps/search/api`).

## How it works

**Index-backed trigram matching.** Every search runs the GIN `%` operator
(`pg_trgm`) against the name trigram indexes (`ix_player_name_trgm`,
`ix_team_name_trgm`, and the series/competition name indexes). The EXPLAIN
gate in the test suite runs with `enable_seqscan=off` and asserts the plan
references those indexes — a search is never a sequential scan.

**Ranking.** Within a type, rows are ordered by `trigram_similarity`. Across
types the merged result is ranked by

```
score = similarity * type_weight
        + exact_name_boost (0.25)   # name == query exactly
        + prefix_boost   (0.10)     # name starts with query
```

with weights player `1.0`, team `0.95`, series `0.9`, competition `0.85` — so
a strong team match can outrank a weak player match. Ties break by
(type order, name). The merged set is truncated to `limit` (default 20, max
100).

## Safety (layered)

| Layer | Mechanism |
|---|---|
| Normalisation | lowercase, collapse whitespace |
| Length bounds | 2–100 characters (`MIN`/`MAX_QUERY_LENGTH`); anything else → `ValueError` → API `400 invalid_query`, never an unbounded `LIKE` |
| Statement timeout | every search runs in one atomic block with `SET LOCAL statement_timeout` (default 200 ms) so one pathological query cannot stall the pool |
| Candidate cap | 50 candidates fetched per type (`_PER_TYPE_CANDIDATES`) before the merged ranking |

A statement timeout is counted on `search_timeouts_total` and surfaces as a
5xx — never a silent partial page. The timeout is configurable per call
(`timeout_ms`; `None` disables it for tests/perf) and from settings.

## API shape

`GET /search?q=&types=player,team` → the standard `{"data", "meta"}` envelope;
`data` items carry `id`, `name`, `type` and `score`. Unknown `types` or an
out-of-bounds `q` → `400 invalid_query`; unknown entity ids elsewhere are 404.
The endpoint is cached under the `search` namespace (invalidated by the Group 2
data-change invalidators) with a 30 s TTL.

## Observability

- `search_queries_total{types}` — completed searches by requested type set.
- `search_timeouts_total` — searches cancelled by the statement timeout
  (alert: `SearchTimeoutHigh`, runbook: Search).
- `search_latency_seconds` — histogram of search wall time.

## Operational notes

- A sustained timeout rate usually means a trigram index is missing (a `%`
  search falls back to a sequential scan and blows the 200 ms budget). Verify
  the indexes exist before raising `SEARCH_STATEMENT_TIMEOUT_MS`.
- Search reads live tables (players/teams/series/competitions) — it is the one
  Phase F read surface that does not go through the Tier-2 aggregates, and it
  is bounded by the timeout + caps above.
