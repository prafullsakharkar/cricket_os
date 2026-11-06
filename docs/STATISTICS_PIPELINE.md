# Statistics pipeline (Tier-2 aggregates)

How the Phase F Tier-2 aggregate tables are produced, validated and served.
Read this before touching `apps/statistics/services/recompute.py` or the
`rebuild_statistics` / `sweep_statistics` backfill paths.

## Layering

There are two tiers of statistics:

- **Tier-1 (Phase C):** `player_match_stat` — one row per player per completed
  match, built by `stats.build_match_stats` from innings/deliveries/scorecards
  when a match completes (or on rebuild).
- **Tier-2 (Phase F):** `player_career_stat`, `team_stat`, `head_to_head`,
  `tournament_standings`, `leaderboard`, `form_trend` — recomputed **from
  Tier-1 source** by the pipeline described here.

Tier-2 never reads the partitioned `delivery` table. It reads `player_match_stat`
rows plus match/innings/result metadata. The API serves only digest-validated
Tier-2 rows.

## The key scheme

Every aggregate is addressable by a **key** — the pipeline recomputes keys, the
watermark records them, and the API invalidates by them:

```
career:{player}:{scope}:{ref|'-'}:{fmt|'-'}:{gender}
team:{team}:{scope}:{ref|'-'}:{fmt|'-'}
h2h:{low}:{high}:{fmt|'-'}                 # low/high = canonical id ordering
standings:{series}:{stage|'-'}
leaderboard:{player|team}:{scope}:{ref|'-'}:{fmt|'-'}
form:{player}:{fmt|'-'}
```

`scope` is one of `CAREER`, `FORMAT`, `SEASON`, `SERIES`, `TOURNAMENT`, `TEAM`,
`OPPOSITION`, `VENUE`; `ref` is the scope reference id (season/series/team/
venue) or `-`; `fmt` is the format code (`T20I`, `ODI`, `TEST`, `-` for all).

A match's **dependency set** (`dependency_keys_for_match`) is every key the
match contributes to: for each of its `player_match_stat` rows the player's
career/format/season/series/team/opposition/venue keys; for each participating
team the team keys; the H2H pair; and — when the match belongs to a series —
the standings key. A match with no stats and no teams contributes nothing.

## Recompute-from-source, digest-validated

`recompute_match(match, correction=)` recomputes the match's dependency set.
Each key is recomputed **from source** (Tier-1 rows), never by delta. The
inputs are canonicalised (sorted source rows + rule version) and hashed into an
`inputs_digest` on the aggregate row; a recompute that produces the same digest
leaves the row untouched (idempotent — revision is not bumped, no write
happens). The `statistics_watermark` row records the match's source digest and
the aggregate keys it produced.

`recompute_keys` runs a set of keys in **dependency order** (`_KEY_PRIORITY`:
career < team < h2h < standings < leaderboard < form) then lexically, one
transaction + advisory lock per key, so concurrent sweeps cannot interleave.
A key that raises is counted on `statistics_recompute_errors_total{scope}`
and re-raised — one key never poisons the rest of the set.

**Correction semantics.** A correction (recompute after the match changed
behind its back) unions the match's *previous* watermark keys with the *current*
dependency set, so removing a match from a series still recomputes the
standings/team keys that previously contained it. The sweep detects corrections
by comparing the watermark's source digest against a fresh digest of the
match's source rows.

## Watermark & sweep

`StatisticsWatermark(match, source_digest, status, aggregate_keys)` is the
pipeline's checkpoint. `sweep_stale_statistics()` scans completed matches whose
aggregates are missing or stale (no watermark, changed source digest, or a
non-`done` watermark), stamps the backlog on `statistics_watermark_lag`, then
heals each one. `rebuild_statistics` (the backfill command) recomputes a scope
(`all` | `player`/`team`/`series` with `--ids`, `--since`, `--dry-run`) and
first rebuilds any missing Tier-1 rows through the Phase C per-match builder.

## Standings + NRR

`standings:{series}:{stage}` recomputes a series' points table from completed
matches via the pure `apps.scoring.standings.build_standings` engine (same code
path the tests oracle against). The points system is part of the inputs digest,
so changing a series' `points_system` triggers a real recompute. NRR uses the
full-quota-when-bowled-out rule; no-result matches are excluded; a series with
only no-result matches reports `nrr: null`.

**Reconciliation.** `reconcile_standings` diffs the pipeline's standings against
a provider's points table. A mismatch opens a `ReconciliationJob` + DLQ row and
increments `standings_reconciliation_mismatches_total` — never a silent
correction (the reconciliation invariant). The operator adjudicates (runbook:
Statistics pipeline).

## Leaderboards, records, form, compare

- **Leaderboard** rows are precomputed, ranked aggregates per scope/metric/
  format (`leaderboard:{player}:...`). `_rebuild_scope` is digest-gated:
  identical inputs skip the write; a change deletes + bulk-inserts the scope's
  rows (folding `rank` into the unique key, so rank changes always rewrite).
- **Records** are computed on demand from digest-validated career rows with
  qualification gates (min matches/innings per format).
- **Form** windows (5/10/20) come from `form:{player}:{fmt}` recomputes; result
  characters are W/L/T/D/N (TIE/DRAW/NO_RESULT + win/loss), most recent first.
- **Compare** reads career rows directly; deltas are relative to the first id.

## Merge integration

`player.merged` (published inside the merge transaction) runs
`_on_player_merged`: it re-points the loser's `player_match_stat` rows —
merging same-match rows and re-pointing distinct ones — deletes the loser's
aggregate/leaderboard/form/ranking rows, then recomputes the winner's scopes
from source. A failure rolls the merge back.

## Serving

The read API (`apps/statistics/api`, `apps/statistics/services/reads`) reads
only digest-validated aggregate rows; it never scans `delivery`. Cache version
namespaces map to the pipeline's invalidators (`statistics`, `standings:{series}`,
`statistics:player:{id}`, `statistics:match:{id}`), so a recompute/correction
purges the affected read caches. Query-count ceilings and a no-`delivery`-scan
guard are enforced by the Group 7 tests.

## Operations

- **Metrics:** `statistics_recompute_jobs_total{scope}`,
  `statistics_recompute_errors_total{scope}`, `statistics_recompute_seconds`,
  `statistics_watermark_lag`, `standings_reconciliation_mismatches_total`,
  `leaderboard_rebuilds_total{scope}` — see `ops/alerts/statistics_alerts.yml`
  and the runbooks.
- **Backfill:** `python manage.py rebuild_statistics all [--since YYYY-MM-DD]
  [--dry-run]`; per-scope `player|team|series --ids <uuid>...`.
- **Sweep:** scheduled `sweep_stale_statistics()` (management path; see the
  runbook for the backlog procedure).
