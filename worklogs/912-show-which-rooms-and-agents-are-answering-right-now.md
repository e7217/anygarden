# feat(sidebar): show which rooms and agents are answering right now (#912)

- Commit: `4c64c14` (4c64c143e462d0871b0cdf994ea519eb49ca3799)
- Author: Changyong Um
- Date: 2026-10-19T20:02:03+09:00
- PR: #912

## Situation

The typing indicator was only forwarded to the room the user currently had open. Once a user sent a message in one room and navigated away, the sidebar gave no signal that an agent was still working there. A user who opens several rooms in quick succession had no way to know which rooms still had agents actively generating a reply without switching back to each one.

## Task

Make the sidebar show, for every accessible room, whether an agent is answering right now — and which agent — without requiring the user to open each room. Constraints:

- Must respect the same access scope as the room list (no leaking turns from rooms the caller cannot see).
- `pending` turns must be excluded because they linger while an agent is offline and would create a false "busy" signal.
- Must not degrade performance: polling rather than a persistent WebSocket subscription, paused when the tab is hidden.
- Guest users must not hit the endpoint (they have no rooms to mark).
- Indicators must be accessible and motion-safe.

## Action

**Backend — `packages/cluster/anygarden/api/v1/turns.py`**
- Added `ACTIVE_TURN_STATES = ("leased", "retrying", "completing")` constant (line ~174) — `pending` deliberately excluded.
- Added `ActiveTurnOut` Pydantic model (room_id + agent_ids list).
- Added `GET /api/v1/turns/active` endpoint (MSG-09): queries `AgentTurn` rows scoped to rooms the caller can see via `accessible_room_ids`, groups by room_id, returns sorted results.

**Backend tests — `packages/cluster/tests/test_active_turns.py`** (89 lines, new file)
- Pytest coverage for MSG-09: scope filtering, state filtering, empty response for guests, execution-bound turns.

**Frontend hook — `packages/cluster/frontend/src/hooks/useActiveTurns.ts`** (76 lines, new file)
- `useActiveTurns(enabled)`: polls `/api/v1/turns/active` every `ACTIVE_TURNS_POLL_MS` (5 s) while the tab is visible.
- Exposes `{ roomIds: Set<string>, agentIds: Set<string> }` via `ActiveTurnsContext`.
- `ACTIVE_TURNS_INVALIDATE_EVENT` window event lets the open ChatPage nudge it immediately when its local typing set changes.
- Keeps last state on poll failure to avoid flickering.

**Frontend hook tests — `packages/cluster/frontend/src/hooks/useActiveTurns.test.ts`** (93 lines, new file)
- Vitest coverage: exposes answering rooms/agents, polls while visible, skips hidden tab, refetches on invalidate event, disabled path, keeps state on error.

**`packages/cluster/frontend/src/components/RespondingIndicator.tsx`** (new)
- Three-dot typing bubble, `role="status"`, `aria-label` from i18n `common.responding`, static under `prefers-reduced-motion`.
- Styled as a bordered pill so it is visually distinct from the unread `UpdateDot` and the `⋯` menu.

**`packages/cluster/frontend/src/components/PresenceDot.tsx`**
- New `working?: boolean` prop: adds `presence-working` CSS class, `data-working` attribute, CSS custom properties for the pulse animation, and appends `· Responding` to the accessible name.

**`packages/cluster/frontend/src/components/EntityAvatar.tsx`**
- Forwarded `working` through the `presence` prop shape (line 39).

**`packages/cluster/frontend/src/components/Sidebar.tsx`**
- `Sidebar` wraps its tree in `<ActiveTurnsContext.Provider>`, polling disabled for guests.
- New `RoomStatus` helper (line ~838): returns `<RespondingIndicator>` when `roomIds.has(roomId)`, else `<UpdateDot>` when `hasUpdates`, else null. Replaced every `{room.has_updates && <UpdateDot>}` call site with `<RoomStatus>` (project rooms, DM rows, pinned rooms — 5 call sites).
- `AgentDMListAdmin` reads `activeAgentIds` from context and passes `working` to agent avatars.

**`packages/cluster/frontend/src/pages/ChatPage.tsx`**
- Fires `ACTIVE_TURNS_INVALIDATE_EVENT` when the local typing set changes to trigger an immediate poll refresh.

**`packages/cluster/frontend/src/index.css`**
- Added `.responding-dot` keyframe animation (step sequence, 3 × 160 ms delay) and `@media (prefers-reduced-motion)` override that holds the dots still.

**`packages/cluster/frontend/src/i18n/catalogs/core.ts`**
- Added `common.responding` key.

**`docs/requirements.md`**
- Added MSG-09 row: sidebar shows answering rooms and agents scoped to accessible rooms, verified by pytest + vitest.

## Decisions

- **Polling vs. WebSocket push**: polling was chosen because the existing WebSocket channel carries typed events per room, not cross-room aggregates. Adding a new broadcast type would require coordinating across all cluster nodes; a 5 s poll from an already-open sidebar is low traffic and avoids that complexity. If load or latency become concerns, a dedicated SSE stream or the existing presence channel could replace it.
- **Exclude `pending` state**: `pending` persists while an agent is unreachable (e.g., machine offline). Including it would show rooms as "busy" indefinitely with no agent working, which is misleading. Only states where an engine has taken the turn — `leased`, `retrying`, `completing` — count as "active".
- **`RespondingIndicator` as a three-dot bubble, not a dot**: the unread indicator is a single colored dot; the presence indicator is also a single dot. A second dot would be ambiguous. A three-dot typing bubble is a widely understood convention for "someone is writing", and the border pill shape makes it distinct from both.
- **Context + `RoomStatus` helper instead of prop-drilling**: passing `roomIds` down through Sidebar's deeply nested tree would have required touching many components. A React context consumed by a small `RoomStatus` helper keeps the change localized and avoids polluting component interfaces.
- **Assumption to revisit**: if turn states are ever renamed or new "active" states are added (e.g., `executing`), `ACTIVE_TURNS_STATES` in `turns.py` must be updated — the constant is the single source of truth but is not derived from an enum.

## Result

- Sidebar room rows now show a three-dot `RespondingIndicator` instead of the unread dot while an agent is answering; the unread dot reappears once the turn completes and the reply arrives.
- Agent avatars in the admin DM list pulse with a ring while the agent holds an active turn anywhere.
- Both indicators hold still under `prefers-reduced-motion` and carry an accessible "Responding" label.
- MSG-09 is covered by 89 lines of pytest (backend scope/state filtering) and vitest tests in `useActiveTurns.test.ts`, `PresenceDot.test.tsx`, `RespondingIndicator.test.tsx`, and `Sidebar.test.tsx` (4 new MSG-09 cases).
- Nothing pending.
