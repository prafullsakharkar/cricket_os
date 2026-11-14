# Game day: scenario_reconnect_storm

- Date (UTC): 2026-11-13T16:04:11.218067+00:00
- Target: `localhost:55435`
- Detection time: n/a ms
- Time to mitigate: 5.34 ms
- Runbook section used: `Realtime resync`
- Verdict: **sufficient**

## Detail

```json
{
  "clients": 20,
  "events_behind_each": 3,
  "flow": "deploy restart -> clients reconnect with from_seq -> seq-based replay, no full re-read",
  "from_seq": 0,
  "latest_seq": 2,
  "no_resync_storm": true,
  "replay_frames_per_client_exact": true,
  "replay_p95_ms": 5.34,
  "resync_required_count": 0,
  "unverified_reason": "gate G2 FAILS: local topology only, not production evidence"
}
```
