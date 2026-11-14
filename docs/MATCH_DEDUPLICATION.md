# Match de-duplication — canonicalisation policy

Phase D deferred cross-provider match de-duplication to Phase I (Task 4):
recently completed matches now arrive from **both** the live provider and the
historical source (cricsheet), so the same fixture can be created twice. This
document states which source is authoritative and how conflicts are reconciled.
It is the source-precedence policy referenced by `GO_NO_GO.md`.

> **Gate context (2026-11-13):** the provider gate (`docs/PROVIDER_DECISION.md`)
> and the environment gate (`docs/STAGING_ACCESS.md`) both fail. Everything in
> this document is policy + implementation on the local topology; live-provider
> numbers are `UNVERIFIED`.

## 1. Source-precedence policy

| Phase of the match | Authoritative source | Rationale |
|---|---|---|
| Scheduled / in progress | **Live provider** (ball-by-ball) | It is the freshest and only source with ball data. |
| Completed, within the 24 h reconciliation window | **Live provider's final card** | Its final card is the freshest; cricsheet may lag. |
| Completed, after 24 h | **Live provider if it covers the match**, else cricsheet | The live provider's final card wins when present (freshest); cricsheet is authoritative for cards the live provider does not cover. |

**Conflicts** (result disagreement, score disagreement) are **never
auto-corrected**: they are flagged to the steward via the reconciliation / DLQ
path (SECURITY §9 flag-and-queue invariant). Merging two match rows is a
**reversible**, audited operation (unlike player merges): `match_merge` rows
record the exact before/after child-row inventory and `split_match` reverses
them.

## 2. Canonicalisation predicate

Two fixtures are the same match when they agree on:

1. **series or competition** (same one; both must have one),
2. **the team pair** (both teams, order-insensitive),
3. **start time** within `MATCH_DEDUP_START_TOLERANCE_MINUTES` (default 180,
   PROPOSED) — UTC-normalised, so a start on either side of UTC midnight still
   matches when the local date differs only by the timezone offset,
4. **venue** — only when *both* candidates have one (a missing venue is no
   evidence either way),
5. **match_number / match_desc** — when *both* present, used as tie-breakers so
   a same-teams-twice-in-a-day double-header does not collide.

`canonical_key(series_or_competition_id, team_ids, start_time_utc, venue_id,
match_number, match_desc)` is the matching predicate; `score_candidate(existing,
incoming)` turns the agreement into `[0, 1]` (1.0 when every present signal
agrees; 0.0 when teams or series/competition differ; partial credit when venue
is missing on one side or `match_number` differs).

## 3. Decisions by score band

| Score | Decision |
|---|---|
| `≥ MATCH_DEDUP_AUTO_THRESHOLD` (0.95, PROPOSED) | **Auto-canonicalise**: the incoming fixture resolves onto the existing match; a `provider_entity_map(entity_type='match')` row is created/refreshed and the incoming provider's deliveries append into the canonical match. No new `match` row. |
| `≥ MATCH_DEDUP_REVIEW_THRESHOLD` (0.60, PROPOSED) | **Steward queue**: a `match_duplicate_candidate` row (`status='open'`) is created; the incoming fixture is still created as a new match (never auto-merged below the auto threshold). |
| below 0.60 | Not a duplicate. |

The thresholds are configuration, not code (`config/settings/base.py`), and are
PROPOSED until the product owner ratifies them.

## 4. Merge and split (reversible)

- `merge_matches(merged, canonical, *, actor, reason)` moves child rows into the
  canonical match where they fit without conflict (by policy), records
  `before`/`after` inventories in `match_merge`, sets
  `merged.merged_into_id = canonical.id`, bumps `revision`, and appends
  `AuditLog(action='match.merged')`. **No data is deleted.**
- **Conflicting innings stay on the merged row** (same
  `(innings_number, is_super_over, super_over_number)` on both sides) and are
  excluded from all selectors/statistics via `merged_into_id` — a split restores
  them under their original match.
- `split_match(merge_row, *, actor)` reverses exactly the `before` inventory,
  clears `merged_into_id`, marks `status='split'`, and appends
  `AuditLog(action='match.split')`.
- `Match.objects.exclude_merged()` hides merged-away rows at every selector
  boundary and in the statistics sweep; a read of a merged match's id returns a
  301 to the canonical (mirroring player merge).

## 5. Recorded decision

This policy is SDD ledger decision **D-I8** (Phase I Task 4) and is referenced
from `GO_NO_GO.md`. Thresholds remain PROPOSED until ratified.
