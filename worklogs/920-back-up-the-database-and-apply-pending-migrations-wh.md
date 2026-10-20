# feat(node): back up the database and apply pending migrations when the integrated node starts (#920)

- Commit: `9f80ffd` (9f80ffdbb2816cab5a20c336bcac7dde88cc257f)
- Author: Changyong Um
- Date: 2026-10-20T09:41:59+09:00
- PR: — (issue #920)

## Situation

`anygarden start` (integrated node mode) previously refused to boot if the database schema was not already at head revision. This meant every package upgrade required a manual four-step ceremony: stop the node, back up the database, run `anygarden server migrate`, then start the node again. There was no automated migration path — the operator had to know the right sequence and execute it without error.

## Task

- Auto-detect when the active SQLite database lags behind the current Alembic head revision.
- Before running the migration, write a consistent point-in-time snapshot of the database so the upgrade is always reversible.
- Keep at most three backups to bound disk usage.
- Still refuse to boot in unrecoverable situations: revision written by a newer build (unknown to this binary), multiple `alembic_version` rows (ambiguous schema), or a non-SQLite database (cannot self-backup).
- Propagate the backup file path into the migration failure message so operators know where to find the recovery copy.

## Action

- **`packages/cluster/anygarden/node/schema.py`** (new file): pure-logic helpers extracted for testability:
  - `classify_revision(revisions, head, is_known) → RevisionState` — categorises the stamped revision as `absent`, `current`, `behind`, `unknown`, or `ambiguous`.
  - `sqlite_db_path(db_url) → Path | None` — extracts the filesystem path from a SQLite URL; returns `None` for in-memory or non-SQLite URLs.
  - `backup_sqlite(db_path, backup_dir, from_rev, now) → Path` — uses the SQLite online backup API (`sqlite3.Connection.backup`) for a page-consistent snapshot; writes to a `.partial` temp file first, then `os.replace`s it so a crashed copy is never mistaken for a valid backup. Sets `0o700` / `0o600` permissions on the directory and file.
  - `prune_backups(backup_dir, keep=3)` — deletes all but the three newest backup files (sorted by the UTC timestamp suffix in the filename).
- **`packages/cluster/anygarden/app.py`**:
  - New `_migration_script()` helper deduplicates `ScriptDirectory` construction (previously inlined in `_discover_head_revision`).
  - New async `_prepare_node_schema(engine, db_url, data_dir)` — queries `alembic_version`, classifies via `classify_revision`, refuses `unknown`/`ambiguous` states with user-facing messages naming the database path and the backup directory, calls `backup_sqlite` and `prune_backups` for `behind` state, emits a `startup.schema_backup` structured log warning.
  - `_startup_server`: replaced the old inline revision-equality check with a call to `_prepare_node_schema`; passes the returned `backup_path` into `_ensure_schema_ready`.
  - `_ensure_schema_ready`: accepts `backup_path` keyword argument and forwards it to `_migration_failure_message` on both failure paths.
  - `_migration_failure_message` (`:502`): accepts `backup_path`; when present, appends `backup: <path>` to the diagnostic block and replaces the generic "Back up the database before any manual repair" step-1 line with a concrete instruction to copy the backup over the database file.
- **`packages/cluster/tests/test_node_schema.py`** (new file, 89 lines): unit tests for all four helpers — `classify_revision` parametrised across six states, `sqlite_db_path` across four URL forms, `backup_sqlite` round-trip and partial-file guarantee, `prune_backups` keep-newest logic.
- **`packages/cluster/tests/test_local_node.py`**: replaced the single `test_integrated_node_refuses_implicit_schema_upgrade` integration test with four more-specific ones — upgrade-and-backup happy path, newer-build refusal, backup-failure refusal (schema unchanged), and backup pruning.
- **`packages/cluster/tests/test_migrations.py`**: added `test_names_the_pre_upgrade_backup` to verify the migration failure message includes the backup path.
- **`docs/runbook/local-node.md`** and **`packages/cluster/docs/deployment.md`**: updated upgrade and rollback instructions to describe the automatic snapshot and the new refusal cases.

## Decisions

- **SQLite backup API over `shutil.copy2`**: a file copy races with any open writer; `Connection.backup` is SQLite's own online backup primitive and produces a page-consistent copy even with WAL-mode databases and open connections. The cost is that it pulls Python's `sqlite3` module into scope unconditionally (already a transitive dependency).
- **Refuse non-SQLite rather than delegate**: the backup contract ("always reversible") cannot be fulfilled without database-specific tooling for PostgreSQL/MySQL. Rather than invoke `pg_dump` — adding a hard external dependency — the node refuses and tells the operator to back up manually. This is the existing behaviour for non-SQLite; the message is now more informative.
- **`unknown` revision → refuse, not skip**: a newer build wrote the schema; downgrading the migration without the matching scripts would corrupt data. Refusing is the only safe default. Operators who deliberately want to pin an older binary must restore a backup made before that upgrade.
- **Newest-three pruning by filename sort**: backup filenames embed a UTC timestamp (`%Y%m%dT%H%M%SZ`); lexicographic sort equals chronological sort for ISO-8601 basic format, so no `os.stat` is needed and the logic is filesystem-portable.
- **`backup_path` threaded through `_migration_failure_message`**: the backup is created before the migration attempt; if migration fails, the operator's only recovery path is that file. Surface it at the top of the diagnostic output rather than requiring the operator to remember they saw a `startup.schema_backup` log line minutes earlier.

## Result

- `anygarden start` now auto-migrates an older SQLite database on first boot after a package upgrade, with a point-in-time backup created under `<data-dir>/backups/` before the migration runs.
- Failed snapshots, unknown revisions, ambiguous schemas, and non-SQLite databases still refuse to boot, with messages that name the database path, the conflicting revision, and the recovery path.
- Failed migrations emit the backup file path so operators can restore without log-diving.
- 419 lines added across 7 files; 10 unit tests in `test_node_schema.py`, 4 integration tests in `test_local_node.py` and 1 message test in `test_migrations.py`; verified live with `anygarden start` on a 095 database (backup written, upgraded to 096) and a fake future revision (refused).
