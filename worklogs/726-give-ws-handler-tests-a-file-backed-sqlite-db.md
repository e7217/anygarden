# fix(tests): give WS handler tests a file-backed SQLite DB (#726)

- Commit: `d93c690` (d93c69049d5763ad70ec719c5c872b981ed932dd)
- Author: Changyong Um
- Date: 2026-09-30
- PR: #726 (issue; PR number assigned on creation)

## Situation

`tests/test_ws_handler.py` failed intermittently on Linux CI, and more often under load: sequentially now and then, and in about half of the local runs with 8 xdist workers. The errors differed from run to run: aiosqlite `no active connection`, `sqlite3.IntegrityError: FOREIGN KEY constraint failed` on an `agent_turns` insert, and `no such table: agent_turns`. Most failures were in `TestAgentCausalLink`. Before #744 the handler returned without closing the socket, so these errors showed up as 6-hour CI hangs. The flake also blocked enabling 4 xdist workers in CI (#753 / PR #755).

## Task

- Find the actual mechanism behind the three errors.
- Make the WS tests independent of it without changing production code.
- Verify under the load that used to trigger it.

## Action

- Recorded SQLAlchemy pool events (`connect`/`invalidate`/`close`) during full-suite runs with 8 workers: connections are invalidated with `CancelledError` from `Connection._handle_dbapi_exception` whenever a query is cancelled mid-flight.
- Reproduced it in isolation: with `build_engine("sqlite+aiosqlite://")` (StaticPool), cancelling a running query and then querying again gives `no such table`. The invalidated connection is replaced by a new, empty in-memory DB.
- `packages/cluster/tests/test_ws_handler.py`:
  - added a module-level `config` fixture that overrides the conftest one with `db_url` pointing to a file under `tmp_path` (`_file_db_url`), so `ws_env` and the other fixtures that call `build_engine(config.db_url)` get a file-backed DB;
  - `TestAgentCausalLink.make_room` now builds each room's engine on its own file (`_file_db_url(tmp_path)`), since the factory can be called more than once per test.

## Decisions

- Options weighed:
  - File-backed DB for the WS test module — chosen: a new connection to the same file sees the same data, so invalidation no longer loses state.
  - Change the global conftest `config` to a file DB — rejected for now: it would change about 20 other test files and the `app`/`engine` fixtures would start sharing one DB, which is a wider behavior change than needed.
  - Stop the cancellation or invalidation itself (shield queries, custom pool) — rejected: cancellation on socket close is real server behavior, and SQLAlchemy invalidating a connection after a cancelled query is intended.
- #750 made file-backed `create_all` as fast as in-memory, so the fixture change costs almost nothing (the module ran in ~48 s sequentially).
- Assumption: other test modules that use `sqlite+aiosqlite://` with a TestClient WS and cancellation can hit the same issue. If they start flaking, apply the same override there or move it to conftest.

## Result

- `tests/test_ws_handler.py`: 149 passed sequentially.
- Full cluster suite with 8 xdist workers: 6 of 6 runs passed (2627 passed, 2 skipped); before the change about half the runs had one failure.
- Unblocks `-n logical` in CI (#753 / PR #755).
