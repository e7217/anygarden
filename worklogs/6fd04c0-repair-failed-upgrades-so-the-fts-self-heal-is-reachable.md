# fix(cluster): repair failed upgrades so the FTS self-heal is reachable (#646)

- Commits: `6fd04c0`
- Author: Changyong Um
- Date: 2026-09-22
- Issue: #646
- Related: #520 (added the self-heal), #473 (original missing `messages_fts`)

## Situation

`_ensure_schema_ready` Case 1 ran `alembic upgrade head` and then
`_self_heal_message_fts`. The self-heal exists to repair one specific state
(#520): `messages_fts` triggers still registered, the FTS virtual table gone.
That state makes the upgrade immediately ahead of it fail, so the self-heal
was unreachable on precisely the databases that needed it.

The mechanism is a SQLite property rather than an Alembic bug. `ALTER TABLE
... RENAME` reparses the *entire* schema, so a trigger referencing a missing
table aborts the statement — even when the rename targets an unrelated table.
Alembic's batch mode is temp-table + rename, so in practice every batch
migration is blocked. Reproduced on sqlite 3.45.1:

```
DROP TABLE messages_fts;                      -- triggers survive
ALTER TABLE _alembic_tmp_tasks RENAME TO tasks;
-- OperationalError: error in trigger messages_fts_insert:
--   no such table: main.messages_fts
```

The issue proposed moving the self-heal ahead of the upgrade. Two findings
made that insufficient, both established by running the code rather than
reading it.

**A failed upgrade is not rolled back.** pysqlite opens an implicit
transaction only before DML, so the `CREATE TABLE _alembic_tmp_x` that opens
a batch migration executes in autocommit and is committed before the failing
`ALTER`. Exercised through the application's own `build_engine`:

```
failure: error in trigger messages_fts_insert: no such table: main.messages_fts
tables after failure: ['_alembic_tmp_tasks', 'messages', 'tasks']
```

`tasks` came back (its `DROP` was inside the implicit transaction);
`_alembic_tmp_tasks` did not. Batch mode then issues a bare `CREATE TABLE`,
so the retry collides on the leftover. Any database that has failed to boot
once — which is every database that meets this bug — needs both repairs, not
one. The issue reporter's own note, that they had to run the FTS statements
*and* drop `_alembic_tmp_tasks` by hand, says the same thing.

**The self-heal is not purely DDL.** It also calls `backfill_message_fts`,
which selects concrete `messages` columns. Running it ahead of the upgrade
would execute it against a pre-migration schema, and a later
`batch_alter_table("messages")` would silently drop the triggers it had just
created, with no post-upgrade call left to restore them.

## Task

- Make the self-heal reachable on the databases it was written for.
- Leave the healthy boot path untouched — this code is on the path of every
  startup, and a regression here presents as "the server will not start".
- Do not let the recovery swallow unrelated migration failures.

## Action

- **`_repair_failed_upgrade(engine)`** (`anygarden/app.py`) — invoked only
  from the upgrade's `except` path. Drops stale `_alembic_tmp_*` tables, and
  recreates `messages_fts` when its triggers are present but the table is
  not. Returns a description of what it repaired, or `None` when it found
  nothing it understands; the caller then re-raises the original exception
  unchanged, so a real schema problem is not hidden behind a second identical
  traceback. SQLite-only; other dialects return `None` immediately.

- **DDL only, on purpose.** Re-indexing stays with the existing
  post-upgrade `_self_heal_message_fts` so `backfill_message_fts` sees the
  final schema. The repair step answers "why can migrations not run"; the
  self-heal answers "is the index consistent with the schema we ended at".

- **Call site** — the `try` body is the original single line, so on a healthy
  database the executed path is identical to before. Everything added lives
  inside `except`. One repair, one retry; no loop.

- **Temp-table cleanup is unconditional.** Batch mode renames its scratch
  table into place on success, so a surviving `_alembic_tmp_*` is always
  residue. The names are logged at `warning` level rather than dropped
  silently. Startup is single-writer (integrated mode pins
  `WEB_CONCURRENCY=1`), so there is no concurrent-migration case to protect.

- **Tests** — new `TestUpgradeFailureRecovery` in
  `tests/test_migrations.py`, kept separate from `TestEnsureSchemaReady`
  whose docstring scopes it to the three schema-classification paths. Three
  cases, each isolating one condition: a dangling FTS trigger, a leftover
  `_alembic_tmp_tasks`, and an unrelated failure that must propagate after
  exactly one attempt. Revision `059` is the fixture point because `060`
  does `batch_alter_table("tasks")` — the migration named in the report.

## Result

- **cluster**: 1778 passed, 1 deselected, 0 failed (914s). The three new
  tests reproduce byte-for-byte the error from the issue before the fix
  (`error in trigger messages_fts_insert: no such table: main.messages_fts`
  / `[SQL: ALTER TABLE _alembic_tmp_tasks RENAME TO tasks]`) and pass after.
  The third test passes both before and after by design — it is the guard
  that the new `except` does not start swallowing failures.
- **Lint**: `ruff check --select F,E9` clean on both touched files. (The repo
  ships no ruff configuration, so the default ruleset reports a large
  pre-existing baseline; only the touched files were checked.)
- **Real data**: a copy of a live database (515 messages) was downgraded to
  `059` and forced into the reported state. Boot logged
  `startup.schema_upgrade_repaired dropped_tmp_tables=['_alembic_tmp_tasks']
  fts_recreated=True`, reached `072`, re-indexed all 515 rows, and left no
  `_alembic_tmp_*` behind; FTS `MATCH` queries returned results.

  The `_alembic_tmp_tasks` in that log was not seeded by the test — it was
  created by the failing upgrade attempt moments earlier. That is the
  clearest evidence available that repairing the trigger alone would not
  have been enough.

  The original database was opened read-only throughout and verified
  unchanged afterwards (`072`, 515 messages, 515 FTS rows); the node serving
  it stayed up.

Behavioural impact:
- A database in the #520 state now boots instead of exiting with an ~80-line
  traceback, and comes up with a populated search index.
- Operators see one `warning` line naming what was repaired. Silence means
  nothing was wrong.
- Unrelated upgrade failures behave exactly as before, including the
  traceback, and are attempted once.

Deliberately out of scope: attaching database path, current revision and a
recovery hint to the failure message (#650 — it builds on the `except` block
this change introduces), and the mistargeted `make migrate` entry point
(#647).
