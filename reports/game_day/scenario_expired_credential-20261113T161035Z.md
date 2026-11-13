# Game day: scenario_expired_credential

- Date (UTC): 2026-11-13T16:10:35.140295+00:00
- Target: `localhost:55435`
- Detection time: 0.03 ms
- Time to mitigate: 0.04 ms
- Runbook section used: `Credential rotation`
- Verdict: **sufficient**

## Detail

```json
{
  "breaker_opened_after_rejections": true,
  "breaker_recovered": true,
  "clean_error_surfaced": true,
  "expired_header": "Bearer expired-token-123",
  "flow": "expired token -> clean auth header -> rejections -> breaker OPEN -> rotate store value -> HALF_OPEN probe -> fresh token -> CLOSED",
  "half_open_probe_window": true,
  "rotated_header": "Bearer rotated-token-456",
  "rotation_without_redeploy": true,
  "unverified_reason": "gate G2 FAILS: local topology only, not production evidence"
}
```
