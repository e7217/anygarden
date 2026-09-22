# feat(cluster): report schema migration failures with context (#650)

- Commits: `6fc31a2`
- Author: Changyong Um
- Date: 2026-09-22
- Issue: #650
- Builds on: #646 (`ce88e61`), which introduced the `except` path this fills in

## Situation

A failed `alembic upgrade head` during startup surfaced as a bare ~80-line
SQLAlchemy/aiosqlite traceback followed by `Application startup failed.
Exiting.` The one actionable line sat mid-traceback:

```
sqlite3.OperationalError: error in trigger messages_fts_insert: no such table: main.messages_fts
[SQL: ALTER TABLE _alembic_tmp_tasks RENAME TO tasks]
```

Absent entirely: which database, what revision it was on, what it was trying
to reach, and what to do next.

The sibling paths in the same function already explain themselves. Case 3
(legacy unstamped) prints a numbered baseline procedure and says why it
refuses to boot; the integrated-node schema guard names what it needs. Case 1
was the one path that did not — and it is the path a routine upgrade takes.

## Task

- Give the Case 1 failure the same treatment its siblings already have.
- Do it without turning the diagnostic into a new failure mode: every lookup
  it performs can itself fail.
- Do not leak anything from the connection string.

## Action

- **`_migration_failure_message`** — a pure function, so the interesting
  properties are directly testable. Renders database, current revision,
  target, and root cause, then a recovery block. Both failure exits of the
  upgrade wrap their exception in a `RuntimeError` built from it, chained with
  `from` so the traceback survives, and the full traceback drops to `debug`.

- **Passwords are never rendered.** `_safe_db_label` goes through
  `make_url(...).render_as_string(hide_password=True)`. This message is the
  most-copied artefact of a broken deployment — logs, issue reports,
  screenshots — and a leaked credential in one cannot be recalled. The
  project's default is SQLite with no secret in the URL, but `db_url` is
  configurable and `ANYGARDEN_DB_URL` accepts any DSN. An unparseable URL is
  elided rather than echoed, since we cannot know what it contains. Both
  behaviours have dedicated tests; the masking one exists specifically to fail
  loudly if someone later "simplifies" the label.

- **The cause is the innermost `__cause__`.** SQLAlchemy wraps the DBAPI
  error, so reporting the outer message would reproduce the original
  complaint in a tidier font. `_innermost_cause` walks the chain, guarding
  against a cycle.

- **The SQLite note came from an experiment that contradicted the draft.**
  The first wording claimed "no partial schema was applied by this process".
  Running a real failed upgrade showed that is false: `alembic_version` *is*
  unchanged (the marker update is DML and rolls back), but
  `_alembic_tmp_tasks` persists, because pysqlite only opens an implicit
  transaction before DML and the batch migration's `CREATE TABLE` lands in
  autocommit.

  ```
  실패 후 revision: 059
  잔재 테이블     : ['_alembic_tmp_tasks']
  ```

  So the message now states the accurate half and warns about the other,
  gated on the dialect so it does not claim SQLite behaviour on Postgres.

- **Nothing in the builder can fail the boot harder than it already has.**
  `_read_current_revision` and `_try_head_revision` both swallow and return
  `None`, rendering as `unknown`.

- **`RuntimeError`, not a new exception class.** Case 3 and the integrated-node
  guard both raise `RuntimeError`; nothing catches these, they all end
  startup. A bespoke type would add a name with no handler.

## Result

- **cluster**: 1785 passed, 1 deselected, 0 failed.
- Seven new tests: six on the builder (password masking, sqlite path
  visibility, innermost cause, repair summary present only after a repair,
  unknown revisions, unparseable URL) and one end-to-end through
  `_ensure_schema_ready` asserting the context is present *and* that
  `__cause__` is still chained.
- #646's `test_unrelated_upgrade_failure_is_not_swallowed` passes unchanged.
  It is the guard that this wrapping did not start swallowing failures: the
  original message still matches, because it is rendered as the `cause:` line.
- `ruff check --select F,E9` clean.
- Rendered against a real failure, at default log level:

  ```
  Schema migration failed.
    database:         sqlite+aiosqlite:////tmp/tmpnhirbfch.db
    current revision: 059
    target:           head (072_drop_agent_collaboration_mode)
    cause:            error in trigger messages_fts_insert: no such table: main.messages_fts

  The alembic_version marker above is unchanged, but SQLite does not roll back
  DDL: a partially applied batch migration can leave _alembic_tmp_* tables
  behind.

  To investigate:
    1. Back up the database before any manual repair.
    2. Inspect the migrations between the two revisions above.
    3. Re-run with --log-level DEBUG for the full traceback.
  ```

Behavioural impact:
- An operator hitting a migration failure gets four facts and three steps
  instead of a traceback. The traceback is still there under
  `--log-level DEBUG`.
- Scripts grepping stderr for the SQLAlchemy traceback at default level will
  no longer find it. Considered unlikely inside this project, noted rather
  than assumed away.

Deliberately out of scope: Case 2 and Case 3 messages, which were already
self-explanatory, and the recovery behaviour itself (#646).

## Note

`packages/cluster/anygarden/app.py` also gained password redaction in #647
(`docs/deployment` / Case 3 recovery text) via a local `make_url(...)` call.
Whichever landed second should collapse onto `_safe_db_label` from this
change rather than keeping two spellings of the same thing.
