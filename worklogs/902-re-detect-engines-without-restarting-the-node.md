# feat(machines): re-detect engines without restarting the node (#902)

- Commit: `5036d1e` (5036d1e6c27a349662fa63a14f510e61d1bc8636)
- Author: Changyong Um
- Date: 2026-10-19T15:25:29+09:00
- PR: #902

## Situation

The daemon's engine list was populated only during `_register()`, which runs once when the daemon connects. Any engine installed after `anygarden start` (e.g. adding Codex CLI or a Pi later) was invisible to the server until the user restarted the whole node. This was a friction point for onboarding: new users had to know to restart just to have their freshly-installed engine appear.

## Task

- Add a server-side `POST /machines/{id}/engines/rescan` endpoint that forwards an `engine_rescan` frame to the connected daemon.
- Add the `engine_rescan` server frame and `engines_report` daemon frame to the protocol.
- Make the daemon handle `engine_rescan` by running `ensure_managed_pi()` first (so a globally installed Pi gets its managed copy before detection) and then re-detecting; reply with `engines_report`.
- On the server side, handle `engines_report` the same way `register` replaces the engine list.
- Fold repeated `engine_rescan` arrivals into a single running background task.
- Guard: 409 when machine is offline; owner-only authz; add `NODE-16` to requirements.
- No daemon restart required; older daemons silently ignore the unknown frame.

## Action

- `packages/cluster/anygarden/api/v1/machines.py`: Added `POST /{machine_id}/engines/rescan` route (line 679–703). Returns 202 with `{"status": "rescanning"}` or 409 `MACHINE_OFFLINE`.
- `packages/cluster/anygarden/machine/protocol/frames.py`: Added `EngineRescanFrame` (server→daemon, line 281–290), `EnginesReportFrame` (daemon→server, line 546–558); registered both in `ServerFrame`, `MachineFrame`, and `_SERVER_FRAME_MAP`.
- `packages/cluster/anygarden/machine/daemon.py`: Extracted `_engine_capabilities()` helper to de-duplicate engine list serialization (line 104–116). Added `_rescan_task` field; `engine_rescan` case in `_handle()` creates/reuses a background task (line 490–493). Added `_handle_engine_rescan()` (line 1590–1600): calls `ensure_managed_pi()`, detects, sends `engines_report`.
- `packages/cluster/anygarden/ws/machine_handler.py`: Added `engines_report` branch in `handle_machine_frame` (line 193–194). Extracted `_replace_machine_engines()` (line 377–397) shared by `_handle_register` and the new `_handle_engines_report()` (line 400–414).
- `docs/requirements.md`: Added `NODE-16` requirement row.
- Tests: `test_daemon_engine.py` (2 new test classes), `test_frames_engine.py` (2 new tests), `test_machine_engine_endpoint.py` (3 new endpoint tests), `test_machine_handler.py` (2 new handler tests).

## Decisions

- **Background task with deduplication**: A full engine detection + potential Pi provisioning can take tens of seconds. Running it in a background task (`asyncio.create_task`) keeps the WebSocket frame loop unblocked. Repeated rescan requests while one is running are silently dropped (check `_rescan_task is None or done()`), preventing thundering-herd provisioning.
  - Alternative considered: queue up rescans and process serially — rejected as unnecessary given the use case (user manually triggers once).
  - Alternative considered: block the frame loop synchronously — rejected because it would stall all incoming frames during a slow Pi provisioning run.

- **`ensure_managed_pi()` before detection**: #688 established that detection looks for the *managed* Pi copy, not the global install. Running provisioning first means a user who just installed Pi globally gets correct detection on the first rescan without needing a second attempt.
  - Assumption: if `ensure_managed_pi()` starts failing (e.g. filesystem permission issue), the rescan still reports whatever engines are detectable; provisioning errors are non-fatal here.

- **`engines_report` replaces rather than merges**: Replacing the full list (same as `register`) gives a single consistent code path (`_replace_machine_engines`) and avoids stale entries from engines that were uninstalled between rescans.
  - Alternative: differential update (add new, mark missing as absent) — rejected as over-engineering for a low-frequency user action.

- **Older daemons ignore `engine_rescan`**: The protocol's union discriminator means an unknown `type` is simply not matched; no explicit forward-compatibility shim needed.

## Result

- `POST /machines/{id}/engines/rescan` triggers a fresh engine detection on the connected daemon without requiring a node restart.
- The returned engine list (including new engines and auth status) replaces the stored list exactly as a re-register would.
- Requirement `NODE-16` is defined and covered by pytest (`@pytest.mark.req("NODE-16")`).
- 357 lines added across 10 files; 29 lines removed (refactored into shared helper).
- Nothing pending; the PR description noted older daemons ignore the frame gracefully.
