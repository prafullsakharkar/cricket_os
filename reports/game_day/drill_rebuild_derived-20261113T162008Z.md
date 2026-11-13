# Game day: drill_rebuild_derived

- Date (UTC): 2026-11-13T16:20:08.942676+00:00
- Target: `localhost:55435`
- Detection time: n/a ms
- Time to mitigate: 556.56 ms
- Runbook section used: `Statistics pipeline`
- Verdict: **sufficient**

## Detail

```json
{
  "effective_scope": "2026-08-15",
  "no_double_count": true,
  "note": "digest-validated rows reproduce (idempotent rebuild); production-scale rebuild time is UNVERIFIED (G2)",
  "player_match_stat_after": 2,
  "player_match_stat_before": 2,
  "rebuild_wall_time_ms": 556.56,
  "scoped_matches": 2
}
```
