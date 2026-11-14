# Game day: scenario_provider_outage

- Date (UTC): 2026-11-13T14:50:20.682837+00:00
- Target: `localhost:55435`
- Detection time: 77.36 ms
- Time to mitigate: 8.89 ms
- Runbook section used: `Provider outage`
- Verdict: **sufficient**

## Detail

```json
{
  "breaker_closed_after_recovery": true,
  "breaker_contract": "detected -> open (stale reads) -> half-open probe -> closed",
  "failure_threshold": 3,
  "open_poll_skipped_stale": true,
  "outage_calls": 5,
  "polls_to_open": 3,
  "recovery_probe_polled": true,
  "recovery_timeout_seconds": 30.0,
  "unverified_reason": "gate G2 FAILS: local topology only, not production evidence"
}
```
