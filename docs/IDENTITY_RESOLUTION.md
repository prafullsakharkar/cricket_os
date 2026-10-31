# Identity Resolution

How the platform decides whether a provider's entity is the same as one it
already knows — and what happens when it cannot be sure. The scoring engine is
**pure** (`apps/players/identity_scoring.py`, no Django imports) and unit-tested
to 100% line **and** branch coverage; everything else is orchestration in
`apps/providers/services.py` with one per-entity-type resolver per domain app.

## 1. The problem

Cricket data arrives from many providers (Opta, KeyPulse, Cricsheet, …), each
with its own identifier and its own spelling of the same real-world entity.
"Virat Kohli", "V Kohli", "Virat Kohli (India)" and a provider-internal
`player:12345` may all be the same person. We must:

1. **never create duplicates silently** — a new provider row must link to an
   existing entity whenever we are confident, and
2. **never merge automatically** — linking two identities is a data-steward
   decision; a scoring mistake is permanent (there is **no unmerge**), so the
   bar for trusting the machine is deliberately high.

## 2. The scoring engine

Two players are compared on five distinguishing features. The weights sum to
1.0 and the weighted total is clamped to `[0, 1]`.

| Feature          | Weight | Equal | One missing | Different |
|------------------|-------:|------:|------------:|----------:|
| name             |   0.35 |   1.0 |           — | see §2.1 |
| date_of_birth    |   0.25 |   1.0 |        0.5  | 0.0 **+ cap** |
| birth_country    |   0.15 |   1.0 |        0.5  |       0.0 |
| gender           |   0.10 |   1.0 |        0.5  |       0.0 |
| shared_teams     |   0.15 |  Dice |         —   |       0.0 |

`shared_teams` is the Dice coefficient `2·|A∩B| / (|A|+|B|)` over the two
players' team-membership id sets; it scores 0 when both sets are empty (no
evidence, not evidence of difference).

### 2.1 Name scoring

Names are first normalised (see below), then:

1. **normalised-equal** → 1.0;
2. **initials vs full** — every single-letter token of the short name equals
   the first letter of some token of the long name *and* all multi-letter
   tokens appear verbatim ("V Kohli" vs "Virat Kohli" → 1.0). The short side
   must contain at least one single-letter token, so a plain verbatim subset
   ("Virat Kohli" vs "Virat Kohli Sharma") is *not* treated as an initialled
   form and falls through to the ratio;
3. **token overlap** — Dice over the token sets otherwise (0.8 for
   "Virat Kohli" vs "Virat Kohli Sharma").

Normalisation (`normalize_name`): Unicode NFKD decompose → strip combining
marks → `casefold()` → remove punctuation → collapse whitespace. So `José`,
`JOSE` and `Jose` compare equal.

### 2.2 The DoB mismatch cap

If **both** dates of birth are present **and differ**, the pair is almost
certainly two different people, so:

- `date_of_birth` scores 0.0, **and**
- the total is capped at **0.25** (`DOB_MISMATCH_CAP`).

The cap is the critical guardrail: without it, identical names + country +
gender + a large shared team history would push a mismatched pair to 0.95 and
it would be auto-linked — a permanent, wrong merge. 0.25 is comfortably below
the 0.60 duplicate-candidate threshold, so a DoB-mismatched pair is **never**
even flagged for review.

## 3. Thresholds

Defined in `config/settings/base.py`:

| Setting | Default | Meaning |
|---------|--------:|---------|
| `IDENTITY_MATCH_THRESHOLD` | 0.95 | A **unique** candidate scoring at/above this is linked without review. |
| `DUPLICATE_CANDIDATE_THRESHOLD` | 0.60 | A new stub with any candidate at/above this is flagged for steward review (`PlayerDuplicateCandidate`, `status="open"`). |

The gap between 0.60 and 0.95 is the "maybe" band: high enough to be worth a
human's time, low enough to never be trusted automatically. Note that with no
shared-team evidence identical features score **0.85** (name 0.35 + DoB 0.25 +
country 0.15 + gender 0.10), which is why a perfect match needs corroboration
from team history or an exact map hit.

## 4. The resolve flow

`apps/providers/services.resolve(provider, entity_type, provider_entity_id,
attributes) -> UUID` (generic, lives in `providers/services.py`):

```
1. exact ProviderEntityMap hit          ──► return internal_id
2. resolver.match(attributes)             ──► ranked candidates
3. exactly ONE candidate ≥ 0.95           ──► create map row (verified=False) → return it
4. otherwise                              ──► resolver.create_stub() + map row (verified=False) → return stub id
```

- **Step 3 is a link, not a merge.** The existing entity is untouched; only a
  new map row points the provider id at it. A steward can later `verify` it.
- **Step 4 never auto-merges.** If a stub's best candidate lands in
  [0.60, 0.95), a `PlayerDuplicateCandidate(candidate, canonical, score,
  breakdown, status="open")` row is created (`get_or_create`, unique on the
  pair). The stub and the canonical stay separate until a steward decides.
- **Concurrency.** The whole body runs in `transaction.atomic()`. The `ux_pem`
  unique constraint on `(provider, entity_type, provider_entity_id)`
  arbitrates simultaneous resolves of the same provider entity; the loser
  catches the `IntegrityError` (verified via the constraint name —
  `is_pem_race`) and re-reads the winner's `internal_id`. Exactly one map row
  and one entity survive. The race test in `tests/providers/test_services.py`
  runs two barrier-synchronised threads against a real transaction and asserts
  one `Player` + one map row.

Resolvers are registered per entity type from each app's `AppConfig.ready()`:

| Entity type | Resolver | Matching qualifier |
|-------------|----------|--------------------|
| `player` | `players/services/identity_resolution.py::PlayerResolver` | trigram name + DoB/gender/country filters, then `score_pair` |
| `team` | `teams/services/teams.py::TeamResolver` | normalised name + country |
| `venue` | `venues/services/venues.py::VenueResolver` | normalised name + city |
| `series` | `competitions/services/competitions.py::SeriesResolver` | normalised name + start year |
| `competition` | `competitions/services/competitions.py::CompetitionResolver` | normalised name |

## 5. Steward workflow

1. `resolve` flags a candidate pair → `PlayerDuplicateCandidate(status="open")`
   appears in the duplicate-review queue (Group 5 admin, ordered by score desc).
2. The steward reviews the pair side-by-side (features + score breakdown).
3. The steward **merges** the candidate into the canonical player, or
   **rejects** the flag (`status="resolved"`), or **verifies** the provider map
   (`verify_mapping` → `verified=True`, audited via the event bus).

## 6. Merge semantics

Group 4 implements `merge_players`; the rules are fixed here because they shape
the resolver's "never auto-merge" posture:

- **Re-point references** — the merged-away player's memberships, squads and
  provider-map rows point at the canonical player.
- **Idempotent** — merging an already-merged player is a no-op.
- **Cycles refused** — `A → B → A` cannot be merged (a cycle has no canonical
  root).
- **301 redirect** — reads of a merged-away player's id return the canonical
  id, not a 404.
- **No unmerge** — a merge is irreversible. This is why `resolve` requires
  0.95 for an automatic link, caps DoB-mismatched pairs at 0.25, and never
  auto-merges: the cost of a wrong merge is permanent, so the machine is only
  trusted when the evidence is overwhelming.

## 7. Limits

- Name similarity is the strongest signal but the weakest against
  transliteration ("Kohli" vs "कोहली"); normalisation handles accents, not
  scripts.
- Team history is only as good as membership data — empty `team_ids` on both
  sides scores 0 (no evidence), never a penalty.
- The 0.95 link threshold applies to the **unique** best candidate; two
  near-identical candidates at 0.94 each force a stub + review rather than an
  arbitrary pick.
- Scores are rounded to the `PlayerDuplicateCandidate.score` column's
  `Decimal(4,3)` precision when persisted.
