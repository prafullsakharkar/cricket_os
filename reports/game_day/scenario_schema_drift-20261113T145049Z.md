# Game day: scenario_schema_drift

- Date (UTC): 2026-11-13T14:50:49.753702+00:00
- Target: `localhost:55435`
- Detection time: 16.69 ms
- Time to mitigate: 84.78 ms
- Runbook section used: `Schema drift`
- Verdict: **sufficient**

## Detail

```json
{
  "boundary_rejected_validation_code": "wicket_requires_dismissal",
  "dismissal_enum": "NOT_A_DISMISSAL",
  "dlq_open_after_redrive": 1,
  "dlq_payload_redacted": true,
  "dlq_row_written": true,
  "flow": "reject at boundary -> redacted DLQ row -> clean re-drive",
  "malformed_seq": 1,
  "unverified_reason": "gate G2 FAILS: local topology only, not production evidence"
}
```
