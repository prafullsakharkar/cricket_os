# Game day: scenario_redis_failover

- Date (UTC): 2026-11-13T16:02:51.187657+00:00
- Target: `localhost:55435`
- Detection time: 1034.27 ms
- Time to mitigate: 876.5 ms
- Runbook section used: `Cache breaker`
- Verdict: **sufficient**

## Detail

```json
{
  "breaker_recovered": true,
  "builder_calls_during_outage": 7,
  "cache_breaker_opened": true,
  "cache_warmed_before": true,
  "degraded_direct_served": true,
  "fail_open_reads_succeeded": true,
  "flow": "redis down -> real get_or_build path -> LocalBreaker OPEN -> degraded _direct served -> redis up -> breaker reset -> cached reads resume",
  "read_latencies_ms": [
    2.89,
    2.97,
    1.76,
    0.03,
    0.02,
    0.01
  ],
  "redis_down_after_stop": true,
  "redis_restarted": true,
  "unverified_reason": "gate G2 FAILS: local topology only, not production evidence"
}
```
