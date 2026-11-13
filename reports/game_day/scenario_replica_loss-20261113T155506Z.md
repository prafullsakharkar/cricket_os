# Game day: scenario_replica_loss

- Date (UTC): 2026-11-13T15:55:06.336789+00:00
- Target: `localhost:55435`
- Detection time: n/a ms
- Time to mitigate: n/a ms
- Runbook section used: `Backup & restore (replica loss)`
- Verdict: **UNVERIFIED**
- Note: docker-compose.yml has no 'db-replica' service (pg_basebackup bootstrap and primary_conninfo absent) and the local Postgres is not configured for streaming replication (wal_level/max_wal_senders); a replica-loss failover cannot be attempted here and is not faked

## Detail

```json
{
  "db_replica_service_present": false,
  "matches_visible": 145,
  "missing_piece": "docker-compose.yml has no 'db-replica' service (pg_basebackup bootstrap and primary_conninfo absent) and the local Postgres is not configured for streaming replication (wal_level/max_wal_senders); a replica-loss failover cannot be attempted here and is not faked",
  "primary_read_continues": true,
  "primary_read_ms": 41.8
}
```
