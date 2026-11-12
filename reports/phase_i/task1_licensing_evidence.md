# Task 1 — licensing policy verification evidence (dry-run)

Date: 2026-11-13. Branch: `phase-i`. Provider gate: **NOT met** — no licensed
provider; verification runs against the **replay** policy (the only registered
adapter). Everything here is `MEASURED` against the replay policy, and
`UNVERIFIED` for any real licensed provider (which must add its own
`LICENSED_CONTENT_POLICIES` entry and re-run this evidence).

## 1. Policy configuration (configuration, not code)

Source: `config/settings/base.py` → `LICENSED_CONTENT_POLICIES["replay"]`:

| Item | Value (measured) |
|---|---|
| display_rights | True |
| redistribution_rights | False |
| attribution_required | True |
| attribution | "Replay (internal test corpus)" |
| commentary_allowed | False |
| derived_feeds_allowed | False |
| ball_data_retention_days | None |

## 2. Attribution rendering

`attribution_for("replay")` → `"Replay (internal test corpus)"`;
`license_meta("replay")` → `{"ball_data": True, "commentary": False, "rankings": False}`.
Covered by `tests/phase_d/test_licensing.py::test_attribution_and_meta_flags`
(10/10 licensing tests pass, run 2026-11-13).

## 3. `403 license_required` degradation

`check_license(FEATURE_COMMENTARY, "replay")` raises `LicenseRequired`
(`code="license_required"`, `details={"field": "commentary", "provider": "replay"}`);
ball data is allowed. Covered by
`test_licensing.py::test_check_license_raises_for_unlicensed_feature` and the
demo transcript below.

## 4. Retention job (dry-run evidence)

`manage.py enforce_retention` is dry-run by default (`--apply` performs
deletion). Replay policy has `ball_data_retention_days=None` ⇒ `candidates=0`,
nothing deleted. Covered by `test_licensing.py::test_retention_skips_provider_without_window`
and the demo transcript below.

### Demo transcript (2026-11-13, rolled back — no DB writes)

```
policy: replay display= True commentary_allowed= False retention_days= None attribution= 'Replay (internal test corpus)'
commentary check: license_required {'field': 'commentary', 'provider': 'replay'}
ball_data check: allowed
retention dry-run reports: [('replay', None, 0, 0, True)]
```

## 5. Corrections behaviour alignment (replay provider)

ReplayAdapter serves a correction after the recorded stream is fully consumed,
with `revision=2` (see `tests/contracts/test_replay_adapter.py::test_correction_after_completion_serves_revision_2`).
This matches DATA_INGESTION's corrections semantics: never destructive
(superseding row + `is_current=false`), revisioned, audited via
`DataCorrection`. A real provider's push-vs-poll correction marking is
**UNVERIFIED** (provider gate).

## 6. What a real provider must re-run

When the gate is met, re-run: the contract kit, the golden mapping suite, this
licensing evidence against the provider's policy, and the redaction suite
(`tests/phase_i/test_redaction.py`) against the real adapter's request/response
logging. Checklist in `docs/PROVIDER_DECISION.md` §2.
