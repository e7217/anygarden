# feat(scheduler): move an agent's work files to its new machine (#915)

- Commit: `aa00c30` (aa00c304c353ffb2cbcacbbdf783ef6a94b2d640)
- Author: Changyong Um
- Date: 2026-10-20T09:25:16+09:00
- PR: #915

## Situation

#907 made moving an agent to another machine an explicit admin action, but the move left the agent's work files (results, uploaded files) on the old machine; the agent started empty on the new one. Instructions, skills (#910), engine settings and room memory (#237) already followed the agent. The move dialog also wrongly told the admin that memory would be lost.

## Task

- Let the admin bring the work files along when the old machine is online, reusing the #914 HTTP transfer tickets.
- Never leave the agent half-moved: a failed copy must cancel the move and keep the agent where it was.
- Never delete the old machine's files, never overwrite names that already exist on the new machine.
- Exclude instructions, memory and private files (they travel another way or must not travel).
- Fix the dialog copy about memory.

## Action

- `packages/cluster/anygarden/machine/workspace_transfer.py`: shared `_walk_files` generator; new `measure()` counts exactly what `build_archive(exclude_memory=True)` packs.
- `packages/cluster/anygarden/machine/workspace_import.py` (new): `extract()` unpacks a zip into a descriptor-opened work dir, refusing unsafe names (`path_parts` rules), links and special files, enforcing entry/size caps, creating files with create-only links and skipping existing names; `conflicts()` lists names that already exist; `apply_pending_import()` used by the materializer; `work_dir_name(engine)` (`workspace` for codex-cli, `.` otherwise).
- `packages/cluster/anygarden/machine/spawner.py`: end of `_materialize_agent_dir` unpacks `.anygarden-import/workspace.zip`; failures are logged and the zip kept as `failed-*.zip`, never blocking the start.
- `packages/cluster/anygarden/machine/daemon.py` / `protocol/frames.py`: transfer operations `measure`, `import` (download to the import dir, size + sha256 check, report conflicting names), `discard_import`; result frame carries `skipped`; `run_workspace_transfer` returns result fields.
- `packages/cluster/anygarden/workspaces/moves.py` (new): `preview()` (reasons `not_placed`, `source_offline`, `source_unsupported`, `target_unsupported`, `nothing`, `too_large`) and `move_work_files()` (archive on old machine → spool → import on new machine, spool always discarded).
- `packages/cluster/anygarden/api/v1/agents.py`: `GET /agents/{id}/machine/move-preview`; `move_files` on `PUT /agents/{id}/machine` (preview before the capacity lock, stop and wait up to 30 s, copy, then switch placement; on failure discard import and restart in place, 409); ActivityLog `moved_files` (count, archive_bytes, skipped).
- Frontend: `MachineChangeDialog.tsx` checkbox with file count/size, reason when unavailable, moving status; `useAgents.changeAgentMachine(id, machineId, { moveFiles })`; i18n copy fixed.
- Tests: `tests/machine/test_workspace_import.py`, `test_workspace_transfer.py` (measure), `tests/test_workspace_moves.py` (two real daemons on one server: success, failed copy, nothing to move, existing names kept, running agent stop/timeout), vitest, Playwright; requirement AGT-12; CHANGELOG, `docs/api.md`.

## Decisions

- Where to unpack on the new machine: (a) directly during the move, (b) store the zip and let the materializer unpack, (c) start the agent first. Chose (b): the work directory location is engine-specific and decided by the spawner, and unpacking happens before the agent runs, under the per-agent lock. (a) would duplicate that logic on an unmaterialized directory; (c) would let the agent start a turn in an empty directory.
- Failure handling: rejected "switch placement first, copy best-effort" — the admin asked for the files to come along. Both transfers must succeed before the placement changes; the old copy is never deleted, so cancelling is safe.
- What travels: the whole work directory minus private names (keeps `.codex` sessions and receipts out — they are tied to engine versions and absolute paths), minus `memory/` and instructions (restored by the server). Engine session migration is out of scope.
- Relay through the server spool rather than machine-to-machine: machines only dial out and cannot reach each other.
- Assumption: 200 MiB / 10,000 files covers typical agents; above that the option is disabled with the reason. The skipped-name list is computed at import time against the existing work dir (the actual unpack can only skip the same or fewer names).

## Result

- Admins can move an agent with its work files; preview shows count and size, and the dialog explains when moving is impossible.
- Backend 4034 tests, vitest 1040, Playwright move dialog at 375/1440 pass.
- Live: isolated node plus a second real machine daemon — running codex agent moved with 2 files (3 MiB), restarted on the new machine, files byte-identical, old copy kept, spool empty, `workspace_imported` logged by the new machine.
- Pending: engine conversation sessions still start fresh after a move.
