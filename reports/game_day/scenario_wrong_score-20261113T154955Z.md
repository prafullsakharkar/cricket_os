# Game day: scenario_wrong_score

- Date (UTC): 2026-11-13T15:49:55.521125+00:00
- Target: `localhost:55435`
- Detection time: 11.06 ms
- Time to mitigate: 272.51 ms
- Runbook section used: `Wrong score complaint`
- Verdict: **sufficient**

## Detail

```json
{
  "etag_version_changed": true,
  "flow": "complaint -> reconcile -> correction -> match:{id} invalidated",
  "match_version_after": 4,
  "match_version_before": 2,
  "reconciliation_job_opened": true,
  "reconciliation_mismatch_dlq": true,
  "scope_key": "state:01a1d599-e3ee-7000-b0d6-d10c96c0fd0b",
  "unverified_reason": "gate G2 FAILS: local topology only, not production evidence"
}
```
