# Game day: scenario_failed_migration

- Date (UTC): 2026-11-13T16:03:47.769583+00:00
- Target: `localhost:55435`
- Detection time: 4056.22 ms
- Time to mitigate: 4055.9 ms
- Runbook section used: `Failed migration`
- Verdict: **sufficient**

## Detail

```json
{
  "baseline_migrations_clean": true,
  "dev_db": "cricket",
  "dev_db_intact": true,
  "failing_migration_halted": true,
  "flow": "scratch DB -> clean baseline -> broken migration halts -> scratch dropped, dev untouched",
  "scratch_db": "cricket_mig_drill_1378145_1586463267",
  "scratch_db_dropped": true,
  "unverified_reason": "gate G2 FAILS: local topology only, not production evidence"
}
```
