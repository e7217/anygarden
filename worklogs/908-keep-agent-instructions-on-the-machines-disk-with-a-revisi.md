# feat(instructions): keep agent instructions on the machine's disk with a revision history (#908)

- Commit: `c6afde9` (c6afde972d0930c402a52de817a593af62d010e1)
- Author: Changyong Um
- Date: 2026-10-19T17:48:10+09:00
- PR: #908

## Situation

Previously, agent instructions (`AGENTS.md` and `skills/`) existed only as a server-side snapshot. Every restart would re-render the instructions from the server's snapshot, discarding any in-flight edits the agent had made on disk. There was no revision history, no mechanism to restore earlier versions, and the server had no visibility into whether the on-disk state matched what it had last sent. Requirement AGT-11 called for instructions to live durably on the machine's disk, with the server tracking the full history and providing restore capability.

## Task

- Move the authoritative copy of `AGENTS.md` (user area) and `skills/` files to the agent machine's disk.
- Keep a server-side snapshot for offline reading and seeding fresh machines.
- Record every accepted version in a new `agent_instruction_revisions` table (up to 50 per agent), tracking source (`user`/`agent`/`system`) and `restored_from`.
- Introduce a single server write path (`instructions/service.write_instructions`) that routes through the machine when online (`disk` mode) or rejects the write when offline (`disk_offline` mode), falling back to direct snapshot edit when the machine has no capability (`server` mode).
- Provide REST endpoints: `GET /agents/{id}/instructions/revisions` and `POST /agents/{id}/instructions/revisions/{n}/restore`.
- Add `instructions_mode`, `instructions_revision`, and `synced_at` to `AgentOut`.
- Expose history/restore in the UI's instructions panel; show read-only + last-sync-time when the machine is offline.

## Action

**New module — `packages/cluster/anygarden/instructions/service.py`**:  
Core write path. `instructions_mode()` determines routing based on machine capability and bus connectivity. `write_instructions()` sends `AgentInstructionsRequestFrame` to the machine (disk mode) or edits the snapshot directly (server mode). `record_snapshot()` persists the new revision and purges older than 50. `409 Conflict` is raised for disk-offline writes.

**Machine-side — `packages/cluster/anygarden/machine/instructions.py`** (new, ~273 lines):  
`InstructionsManager` owns the on-disk state. `materialize()` seeds a fresh directory from the server snapshot, detects pre-upgrade rendered files and archives them, keeps unsynced local edits, and installs a newer server revision (restore) after archiving local changes. Writes are idempotent: only changed files are touched.

**Machine daemon — `packages/cluster/anygarden/machine/daemon.py`** (+204 lines):  
Handles `agent_instructions_update` (disk → server sync on tick), `agent_instructions_ack` (server confirms or sends a newer revision on conflict), and `agent_instructions_request` (server-initiated write; replies with new snapshot).

**Protocol frames — `packages/cluster/anygarden/machine/protocol/frames.py`** (+80 lines):  
`AgentInstructionsUpdateFrame`, `AgentInstructionsAckFrame`, `AgentInstructionsRequestFrame`, `AgentInstructionsResultFrame`.

**DB migration — `packages/cluster/anygarden/db/versions/095_agent_instruction_revisions.py`** (new):  
Adds `agents.instructions_revision` (int), `agents.instructions_synced_at` (timestamptz), and `agent_instruction_revisions` table (id, agent_id, revision, agents_md, skills JSONB, source, by_user_id, restored_from, created_at). Index on `(agent_id, revision)`.

**API — `packages/cluster/anygarden/api/v1/agents.py`** (+145 lines):  
`GET /agents/{id}/instructions/revisions`, `POST /agents/{id}/instructions/revisions/{n}/restore`. `PUT /agents` and files API for `skills/` now route through `write_instructions`. `AgentOut` gains `instructions_mode`, `instructions_revision`, `instructions_synced_at`.

**Spawner — `packages/cluster/anygarden/machine/spawner.py`** (`render_managed_block`, line 292):  
Added a `## Your instructions file` header block explaining that the user area is editable and changes are synced. Platform sections follow. Prune no longer deletes `AGENTS.md`.

**Scheduler / bus — `lifecycle.py`, `machine_bus.py`** (+60 lines combined):  
Manifest carries `instructions_revision` only to capable machines. `MachineBus.is_connected()` used to gate disk-mode writes.

**Frontend — `InstructionHistoryDialog.tsx`** (new, ~138 lines), `ManifestPanel.tsx` (+68 lines), `AdminMachines.tsx`, `Sidebar.tsx`, `AgentSettingsDialog.tsx`, `useAgents.ts`, `admin.ts` (i18n):  
Instructions panel shows mode and last sync time; is read-only when `disk_offline`. History dialog lists revisions with source and timestamp; restore button POSTs to the restore endpoint.

**Tests** (+647 passing, 2 skipped):  
`test_daemon_instructions.py`, `test_instructions.py`, `test_materialize_instructions.py`, `test_instructions_sync.py`, `test_agents_api.py` (partial), `test_lifecycle.py`, `test_machine_handler.py`, `test_migrations.py`.

## Decisions

- **Single write path through the machine (not a direct DB write for disk mode)**: Ensures the machine is always the source of truth for its own disk. A direct DB write followed by a push would create a race where the machine's local edits could be overwritten silently. Routing through the machine serialises writes and lets it reply with the ground-truth snapshot after applying the change.

- **409 for offline disk agents instead of queuing the write**: Queuing would require a durable outbox, conflict resolution on reconnect, and UI to surface pending writes. The simpler model (read-only when offline, write when online) was chosen; the admin can restore a revision once the machine reconnects if needed. (unclear from available context whether async queuing was formally evaluated or simply deferred.)

- **50-revision cap instead of unlimited history**: Keeps the `agent_instruction_revisions` table bounded without a background sweep job. The limit mirrors what other revision-tracked resources in the project use. Older versions are pruned automatically in `record_snapshot`.

- **Archive-before-overwrite on materialize conflict**: When the machine has local unsaved edits and the server sends a newer revision (e.g. a restore), the local copy is archived to a timestamped path before the server version is installed. This preserves the agent's work while honouring the operator's restore intent, avoiding silent data loss.

- **`agent_instructions_disk_v1` capability flag**: Allows old daemon versions to continue with server-mode instructions. No forced migration; the daemon opts in by advertising the capability on connect.

## Result

- Agent instructions (`AGENTS.md` user area + `skills/`) now persist on the machine's disk and survive container restarts without re-rendering from the server.
- Every accepted change is recorded in `agent_instruction_revisions`; admins can browse history and restore any of the last 50 versions via the UI or API.
- Offline disk agents are read-only in the UI (last sync time shown); writes are blocked with a clear 409 until the machine reconnects.
- 647 tests pass (2 skipped), covering daemon message handling, materialize edge cases, sync protocol, API endpoints, and the migration.
- `render_managed_block` now includes an explanatory header so agents know their instructions are editable and synced.
