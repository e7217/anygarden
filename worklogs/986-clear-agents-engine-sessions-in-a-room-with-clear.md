# feat(rooms): clear agents' engine sessions in a room with /clear (#986)

- Commit: `54b8a50` (54b8a50f12316039dfa28154165c2ce78bb1c0b3)
- Author: Changyong Um
- Date: 2026-11-03T10:55:56+09:00
- PR: — (issue #986)

## Situation

As a room conversation grew longer, agents accumulated a growing engine session (codex/Pi) that could not be reset without leaving the room or making structural changes. Room admins had no way to start an agent's context over while keeping the room's messages and memory notes. This meant agents could carry irrelevant or stale context indefinitely, degrading response quality in long-running rooms.

## Task

- Add a `/clear` (or `/clear @agent`) slash command that a room admin or owner can issue to start one or all agents' engine sessions over.
- Post a "session cleared" system notice to the room so participants see what happened.
- Bump each target agent's `session_epoch` and `revision` in `agent_room_memories` so the runtime opens a new engine session on the next turn and a stale older snapshot cannot undo the reset.
- Store the notice's `seq` as `session_reset_seq` (migration 102) so conversation context for subsequent turns starts after the clear point.
- Drop any pending context lines buffered under the old session in the runtime adapter.
- Keep room memory notes and messages intact.

## Action

- `packages/cluster/anygarden/rooms/session_reset.py` (new, 109 lines): FastAPI router `POST /{room_id}/session-reset`. Validates that the caller is a user with `ROOM_SETTINGS_MANAGE` capability, resolves target agents, appends a system notice message with `system_origin="session_reset"`, calls `reset_room_sessions`, commits, broadcasts the notice, and triggers `push_room_memory` per agent.
- `packages/cluster/anygarden/rooms/router.py:2183`: Includes the new `session_reset_router`.
- `packages/cluster/anygarden/memory/service.py:236-265` (`reset_room_sessions`): Upserts `AgentRoomMemory` rows (creates if absent), then atomically increments `revision`, `session_epoch`, sets `session_reset_seq = reset_seq`, and updates `updated_at`.
- `packages/cluster/anygarden/db/models.py:488-491`: Adds nullable `session_reset_seq: Mapped[Optional[int]]` column to `AgentRoomMemory`.
- `packages/cluster/anygarden/db/migrations/versions/102_agent_room_session_reset.py` (new, 29 lines): Migration adding `session_reset_seq` to `agent_room_memories`.
- `packages/cluster/anygarden/turns/conversation_context.py:86-106`: Reads `AgentRoomMemory.session_reset_seq` for the agent/room pair; if the session was cleared after the last baseline turn, resets baseline to `None` and uses `cleared_at` as the floor for the context window (`max(baseline or 0, cleared_at or 0)`).
- `packages/cluster/anygarden/agent/integrations/room_execution.py:130,238-255,300`: Adds `_buffered_memory_scope` dict; introduces `_forget_context_of_a_cleared_session(room_id)` which compares the current `memory_session_scope` against the buffered one and drops `_pending_context[room_id]` if the scope changed (i.e. the session epoch rotated). Called both on `ingest_context` and at the start of each turn execution.
- Frontend: `MessageInput.tsx` and `slashCommands.ts` register `/clear` as a slash command; `systemMessages.ts` parses the "session cleared" notice (`parseSessionReset`) and `MessageBubble.tsx` renders it as a divider instead of a "(left the room)" bubble; `chat.ts` adds i18n strings; tests added for `MessageBubble`, `MessageInput`, and `slashCommands`.
- `packages/cluster/tests/test_room_session_reset.py` (new, 175 lines): HTTP-level tests for the reset endpoint (authorization, single-agent, all-agents, ephemeral-room rejection).
- `packages/cluster/tests/agent/test_integrations/test_session_reset_context.py` (new, 61 lines): Runtime adapter drops pending-context lines buffered under a cleared session.
- `packages/cluster/tests/test_conversation_context.py`: Extended with 92 additional lines covering the new baseline logic.

## Decisions

- **`session_reset_seq` stored on `AgentRoomMemory`, not a separate table**: The field is a property of an agent's relationship to a room, which `AgentRoomMemory` already models. A join table would add complexity with no benefit; a nullable column suffices and is directly queryable in `conversation_context.py` with one scalar subquery.
- **`session_epoch` bump instead of rotating a UUID key**: The epoch is already the runtime's session-scope discriminator. Incrementing it is atomic with the revision bump in a single `UPDATE`, avoids a separate key-rotation mechanism, and the runtime's `memory_session_scope` already incorporates it so the adapter's comparison detects the change without extra plumbing.
- **Drop buffered pending-context lines in the adapter, not the server**: Pending-context is held in-process in the runtime adapter. The server cannot reach it directly. The adapter checks `memory_session_scope` before each `ingest_context` and before each turn execution; if the scope changed, it discards the buffer. This is the minimal, race-safe point to intercept.
- **Conversation context window floor = `max(baseline, cleared_at)`**: Even if a fresh-session turn (no prior turn baseline) would otherwise start from message 0, the cleared_at floor prevents context from before the clear notice reaching the agent. Because baseline becomes `None`, the existing root insertion in `build_conversation_context` keeps a thread's root even when it predates the clear, and the agent's own replies after the clear are included (the new session does not hold them).
- **Rejected: clearing by deleting `AgentRoomMemory` rows**: Would lose accumulated memory notes attached to the row. Upsert-and-update was chosen to preserve notes while rotating the session.

- **Who may clear: `ROOM_SETTINGS_MANAGE` (room admin/owner)**: global-admin-only (like the memory panel) would block people in their own rooms; any member could wipe context someone else relies on in a shared room. Same tier as `/goal`. Shared-channel rooms are rejected by `require_capability` by default.
- **Admin memory edits still do not set a reset point**: they bump the epoch too, but posting a chat notice on every memory edit would be noise. Their fresh session still gets only what it missed (unchanged gap, possible follow-up).
- **Assumption**: snapshot delivery is best-effort; if it fails, the runtime keeps the old session until the next welcome/manifest. Carrying the epoch on each delivery would close this (not done).

## Result

- Room admins and owners can now issue `/clear` or `/clear @AgentName` in the chat to start one or all agents' engine sessions over.
- The server posts a visible "session cleared" notice; agents' next turns open a new codex/Pi session and receive only messages after the clear point as conversation context.
- A late-arriving snapshot from before the clear cannot undo the reset (revision bump guards this).
- Pending context lines buffered under the old session are discarded by the runtime adapter.
- Room memory notes and all existing messages are preserved.
- 21 files changed, 738 insertions, 34 deletions; covered by 3 new test files and extensions to existing tests.
