# test(cluster): skip fsync on test SQLite DBs (#749)

- Commit: `69a4740` (69a474059d3bfdfd6a824cabbc4f17f3b9bf55d4)
- Author: Changyong Um
- Date: 2026-09-30
- PR: #749 (issue; PR number assigned on creation)

## Situation

The Linux CI `Test cluster` step took about 11 of the job's 12 minutes, and a local run of the cluster suite took 19 minutes. All 40 slowest tests took 5–10 s each, almost all of it in setup. Their files (federation, delegation channel, shared channel, migration tests) use file-backed SQLite DBs under `tmp_path`, and cProfile put that setup time in `Base.metadata.create_all`: SQLite synced to disk after each of ~140 `CREATE TABLE` / `CREATE INDEX` statements. The federation fixtures did this for two nodes per test.

## Task

- Cut the fsync cost of test DBs without changing production code.
- Cover every SQLite engine created during tests: the aiosqlite app engines, file-backed fixture engines, and alembic's sync pysqlite engines.
- Keep the test suite green.

## Action

- `packages/cluster/tests/conftest.py`: added a global SQLAlchemy `Engine` `connect` listener (`_skip_sqlite_fsync`) that runs `PRAGMA synchronous=OFF` on every connection whose DBAPI class module contains `sqlite`. Because it listens on `Engine`, it applies to both sync engines and the sync engine behind async engines. Checked that `PRAGMA synchronous` reads `0` for both pysqlite and aiosqlite connections.

## Decisions

- Options weighed:
  - `PRAGMA synchronous=OFF` via a global listener in conftest — one place, covers every engine, no test changes.
  - Switch the file-backed fixtures to in-memory DBs — rejected: several tests (two-node federation, alembic round-trips, CLI migrate) need real files or separate processes/connections.
  - Build the schema once and copy a template DB file per test — faster still, but more code and each fixture would need to change.
- Chose the PRAGMA because it gave nearly all of the gain (create_all 1.457 s → 0.056 s, in-memory 0.051 s) with a ~15-line change.
- Did not use `journal_mode=MEMORY`/`OFF`: some tests may depend on journal behavior, and `synchronous=OFF` alone was enough.
- Parallelism (`pytest-xdist`) was left out: it interacts with the shared in-memory connection issue in #726 and needs its own change.
- Assumption: no test checks durability after a crash or power loss. If one is added, it has to opt out of this listener.

## Result

- The five slowest files (151 tests): 10m49s → 1m13s.
- Full local cluster suite: 19m04s → 6m21s, 2034 passed.
- CI effect still to be measured on the PR; CI runners may sync to disk faster than local disks, so the gain there may be smaller.
