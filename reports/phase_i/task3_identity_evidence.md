# Task 3 — identity continuity across sources (evidence)

Date: 2026-11-13. Branch: `phase-i`. Provider gate: **NOT met**
(`docs/PROVIDER_DECISION.md`) — the "live" provider is the **replay** namespace
seeded by `providers/0003_seed_replay_provider.py` (a stand-in; `UNVERIFIED`
against a real provider's view). Environment gate: **NOT met** — local topology
only.

## 1. What was verified (MEASURED via the phase_i test suite)

| Acceptance | Evidence (test) | Result |
|---|---|---|
| Linking the live provider id onto an existing cricsheet entity keeps **exactly one** `Player` row (no duplicate) | `test_identity_continuity.py::test_auto_link_keeps_one_player_and_identical_career_stats` | `players.count() == 1`, map row → existing id |
| Career statistics are **identical row-for-row** after linking (digests equal, no split career) | same test | 2 `player_career_stat` rows unchanged (CAREER + FORMAT, same `inputs_digest`) |
| Re-running the link is an idempotent no-op (same map row) | `test_repeat_link_is_idempotent_noop` | second run → `already_linked`, 1 map row, 1 player |
| DoB mismatch → stub created, **not** linked, candidate **not even flagged** (DoB cap) | `test_dob_mismatch_creates_stub_without_link_or_candidate` | outcome `stub_created`; 0 `PlayerDuplicateCandidate` |
| Near-duplicate in [0.60, 0.95) → `candidate_pending` with an open duplicate-candidate row | `test_near_duplicate_in_review_band_is_candidate_pending` | 1 flagged row, `canonical_player_id` = existing id |
| Batch `link_all` classifies the full recorded corpus as auto-linked | `test_linking_batch.py::test_link_all_classifies_full_corpus_as_auto_linked` | 3 auto-linked, 0 pending, 0 stubs, 1 map row per entry, all `verified=False` |
| `--dry-run` computes the same classification **without writing anything** | `test_link_all_dry_run_writes_nothing` + command dry-run test | 0 map rows, 3 players, 0 duplicate rows |
| Seed migration forward/backward (reversible) | `tests/test_migrations.py` (LEAVES now `providers/0003`; 9 migration tests pass) | forward seeds 2 providers; reverse to 0001 clears; forward to 0003 restores 2 |

Full suite: **59 phase_i tests pass**; `make check` green (1733 core + all phase
gates + phase_i).

## 2. The batch command (real output, captured from a hermetic run)

`manage.py link_provider_identities --provider replay [--dry-run]` writes
`reports/phase_i/identity_coverage.md` (committed alongside this file):

```
DRY:  identity linking (DRY-RUN (nothing written)): auto=3 already=0 pending=0 stub=0 residual-duplicates=0
FULL: identity linking (linked): auto=3 already=0 pending=0 stub=0 residual-duplicates=0
```

Outcome table from the report: `auto_linked 3 / already_linked 0 /
candidate_pending 0 / stub_created 0`; residual duplicates in corpus: **0**;
steward workload: **0 min** (nothing pending). Every created map row is
`verified=False` — a steward confirms the link explicitly
(`apps/providers/services.py` `resolve()` never auto-verifies).

## 3. Steward path (verified, no new admin needed)

Phase B's admin already surfaces `PlayerDuplicateCandidate`
(`apps/players/admin.py`); the batch leaves map rows `verified=False` so review
is explicit. Verified by the candidate-pending test above — no admin gap found
(plan §7.4).

## 4. Notes (ASSUMED / UNVERIFIED)

- Steward workload estimate uses **2 min/review (ASSUMED)** — a stated
  assumption in the report, not a measurement.
- The corpus (`tests/fixtures/provider_live/players.yaml`) is derived from the
  in-repo register fixtures (`people.csv`/`names.csv`); DoB values are stand-ins.
  A real provider's spelling/DoB/team-id quirks are `UNVERIFIED` (provider gate).
- The replay provider row is credential-free by design; resolving a secret for
  it fails loudly (`test_missing_credential_ref_fails_loudly`).
