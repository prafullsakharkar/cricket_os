"""Optional fantasy statistics (Phase H).

Fantasy points for any match are computed from a versioned, frozen ruleset and
an immutable snapshot of that match's statistics, stored in an append-only
ledger with a full audit chain, recomputed correctly after corrections and
player merges, and served read-only. Fantasy here means *computed player
points only* — no contests, wagering, payments, wallets, user teams or
accounts anywhere (roadmap Phase H scope, RSK-15).
"""
