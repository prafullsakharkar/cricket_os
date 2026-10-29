# Migration Workflow — Cricket OS

How we change the database safely. Every rule here exists to keep deploys
reversible and to avoid locking a live table.

## 1. The golden rules

1. **Expand / contract.** A schema change is split across releases:
   - **Expand** — add the new column/table/index (nullable, or with a default),
     deploy code that writes *both* shapes.
   - **Backfill** — populate the new shape in the background, separately.
   - **Switch** — deploy code that reads the new shape.
   - **Contract** — remove the old column/constraint in a *later* release.
2. **Never combine a large backfill with a schema change** in one migration.
   Schema changes take locks; backfills are long. Splitting them keeps each
   migration short and its lock brief.
3. **Never drop a column in the same release that stops writing to it.** The
   stop-writing deploy goes first; the drop happens in a later release after the
   change has been proven in production.
4. **Create large indexes concurrently** with
   `django.contrib.postgres.operations.AddIndexConcurrently` (and
   `RemoveIndexConcurrently`), which maps to `CREATE INDEX CONCURRENTLY`.
   Concurrent index builds cannot run inside a transaction, so put them in a
   migration with `atomic = False` and one index per migration.
5. **Migrations are individually reversible.** Each has a sensible down-path.
   Data-destructive contract steps are gated behind a feature flag plus a backup.
6. **Deploys migrate before the code switch-over.** A failed migration blocks the
   deploy rather than letting new code run against an old schema.

## 2. Authoring a migration

- Generate the skeleton with `make makemigrations` (runs
  `python manage.py makemigrations`) and review the output; add explicit names
  for every constraint and index (see [DB_CONVENTIONS.md](DB_CONVENTIONS.md) §5).
- Hand-write `RunSQL` only for things the ORM cannot express (schema creation,
  extensions, partitioning, `CONCURRENTLY` index builds). Give `RunSQL` an
  explicit `reverse_sql` or `migrations.RunSQL.noop` when no safe reverse exists,
  and say why in a docstring.
- Keep one logical change per migration. Small, reviewable migrations are easier
  to roll back.
- CI enforces these rules with `python manage.py makemigrations --check
  --dry-run`, which fails a pull request when a model change has no migration.

### Example: concurrent index

```python
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False  # CONCURRENTLY cannot run inside a transaction

    dependencies = [("matches", "0007_match")]

    operations = [
        AddIndexConcurrently(
            model_name="match",
            index=models.Index(fields=["start_time_utc"], name="ix_match_start_time_utc"),
        ),
    ]
```

### Example: expand / contract

```python
# Release N   — expand: add the new column, nullable so the old code still works.
operations = [migrations.AddField("match", "venue_timezone", models.TextField(null=True))]

# Release N+1 — backfill out-of-band (a management command / Celery task),
#               never inside the migration.
# Release N+2 — switch reads to venue_timezone.
# Release N+3 — contract: drop the superseded column.
operations = [migrations.RemoveField("match", "venue_tz_legacy")]
```

## 3. The migration-halt runbook

From [CRICKET_SECURITY_AND_RELIABILITY.md](CRICKET_SECURITY_AND_RELIABILITY.md)
§7. If a migration fails or causes an incident mid-deploy:

1. **Stop the deploy.** Do not let the new code switch over; the old release is
   still serving.
2. **Assess lock/impact.** Check `pg_stat_activity` and `pg_locks` for blocking
   queries and terminate only the offending migration session
   (`pg_cancel_backend` → `pg_terminate_backend`).
3. **Halt further migrations** (e.g. stop workers/the migration job) so nothing
   else queues behind the failed step.
4. **Decide: forward-fix or roll back.**
   - *Forward-fix* when the migration is additive and a corrected migration
     lands quickly.
   - *Roll back* with `python manage.py migrate <app> <previous_migration>` when
     the down-path is safe (expand steps usually are; contract steps are not).
5. **Prove the rollback** on a copy of production data before running it live.
6. **Record it.** Write the incident and the corrective migration in the change
   log, and add a regression test if the failure was avoidable.

A failed migration **blocks the deploy** — the release does not proceed until the
migration is fixed or cleanly rolled back.

## 4. Local workflow

```bash
make up            # start PostgreSQL 17 (+ Redis)
make migrate       # apply migrations
make makemigrations# author a new one after a model change
make check         # lint + types + import-linter + tests + coverage
```

`pytest` builds a disposable test database and can reuse it with `--reuse-db`
for a faster inner loop (`uv run pytest --reuse-db --no-cov`).
