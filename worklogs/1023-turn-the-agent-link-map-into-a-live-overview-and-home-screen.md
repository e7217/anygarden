# feat(agent-links): turn the agent link map into a live overview and home screen (#1023)

- Commit: `4080d2c` (4080d2cc83562c33390b1b01e8d338f5435d3985)
- Author: Changyong Um
- Date: 2026-11-12T15:15:18+09:00
- PR: #1023

## Situation

The agent-link map (`/agents/links`, introduced in #1006) was a static relationship editor — it showed which agent could ask which, but gave no live signal. Users landing on `/` without a room saw the welcome screen even when a team of agents was already active. The sidebar's link to the map was a small unlabelled icon tucked beside the Agents heading, easy to overlook. Requirements COL-19 and ROOM-16 called for a live "Overview" that shows responding agents, running peer questions, and actionable inbox items, and for that page to become the home screen for multi-agent workspaces.

## Task

- **COL-19**: Overlay live state on the map — highlight answering agents with their room links, dim unavailable agents with a one-line reason code, draw flowing edges only for active peer questions (resting links stay as still arrows, same-room questions as temporary dashed lines). When nothing is selected, the side panel must list inbox items needing the user, running rooms, and rooms with news (empty sections fold).
- **ROOM-16**: Make `/` with no room open redirect to `/agents/links` when there are ≥ 2 agents, without flashing the welcome screen during the check. Add an "Overview" entry at the top of the sidebar (labelled, not icon-only).
- Expose `unavailable_code` on `GET /api/v1/agent-links` for the UI to word the reason without leaking failure details.
- Add `GET /api/v1/peer-chains/active` returning open agent-to-agent questions in rooms the caller can access; require a person identity (agents get 403).
- Update `docs/agent-links.md` and `docs/requirements.md` (COL-14 wording, COL-19, ROOM-16 entries).

## Action

**Backend**

- `packages/cluster/anygarden/api/v1/agent_links.py` — Added `unavailable_code: str | None` to `AgentBriefOut` and included it in the `list_agent_links` response (line ~100). Added `GET /api/v1/peer-chains/active` endpoint (`list_active_peer_edges`) that resolves the caller's accessible room ids via `accessible_room_ids`, calls the new `list_active_edges` helper, and enforces `_require_person`.
- `packages/cluster/anygarden/orchestration/peer_chains.py` — Added `list_active_edges(db, room_ids)` which queries `PeerAskGroup` joined to `PeerAskTarget` and `Participant` for active, non-terminal rows where both caller and target are agents; deduplicates by `(room_id, from_agent_id, to_agent_id)`.
- `packages/cluster/tests/test_agent_links.py` — 76 lines of new pytest coverage for the new endpoint and `unavailable_code` field.

**New frontend hooks and component**

- `packages/cluster/frontend/src/hooks/useActivePeerEdges.ts` — Polls `/api/v1/peer-chains/active` on the same interval as `useActiveTurns` (MSG-09), only while the tab is visible. Aborts in-flight fetches on unmount or disable.
- `packages/cluster/frontend/src/hooks/useOverviewLanding.ts` — Called from `ChatPage` when no room is selected. Fetches the agent graph once; navigates to `/agents/links` if `agents.length >= 2`, otherwise returns `'welcome'`. Returns `'checking'` while the fetch is in flight so the welcome screen is suppressed until the decision is made.
- `packages/cluster/frontend/src/components/agent-links/AgentOverviewSummary.tsx` — Renders three foldable sections ("Needs you", "Running now", "Rooms with news") using data passed from `AgentLinksPage`. Items link directly to their rooms or inbox. Renders a quiet-state caption when all sections are empty.

**Modified frontend files**

- `packages/cluster/frontend/src/pages/ChatPage.tsx` — Added `useOverviewLanding(selectedRoom === null)` to implement ROOM-16; the `landing` value gates the welcome screen render.
- `packages/cluster/frontend/src/pages/AgentLinksPage.tsx` — Wired `useActiveTurns`, `useActivePeerEdges`, `useProjectInbox`, and `useRooms` into the page. Derives `activity` map (`agentActivity`), `busyCount`, `running`, `needsYou`, and `roomsWithNews`. Passes `activity`, `activeEdges`, `roomName`, and `onOpenRoom` to `AgentLinksCanvas`. Renders `AgentOverviewSummary` in the right panel when nothing is selected; refactored `DetailsPanel` to accept an optional `onClose` and a `label` prop so the overview panel has a heading instead of a close button.
- `packages/cluster/frontend/src/components/Sidebar.tsx` — Moved the agent-links nav item from an icon-only button inside the Agents header to a full-width labelled button at the top of the sidebar (above Inbox), satisfying ROOM-16's "Overview entry at the top" requirement.
- `packages/cluster/frontend/src/components/agent-links/AgentLinksCanvas.tsx` — Accepts `activity`, `activeEdges`, `roomName`, `onOpenRoom`; renders agent cards with highlight/dim overlays and flowing vs. still edge styles.
- `packages/cluster/frontend/src/lib/agentLinks.ts` — Added `agentActivity` utility and `fetchActivePeerEdges`; updated `ActivePeerEdge` type.
- `packages/cluster/frontend/src/i18n/catalogs/agentLinks.ts` — Added translation keys for `navLabel`, `overview`, `quiet`, `needsYou`, `openInbox`, `runningNow`, `asking`, `roomsWithNews`, `someRoom`, `details`, `summary` (with `busy` count).
- `packages/cluster/frontend/e2e/agent-links.spec.ts`, `*.test.ts`, `*.test.tsx` — Extended test coverage for new hooks, the page wiring, and E2E scenarios.
- `docs/agent-links.md` and `docs/requirements.md` updated.

## Decisions

**Why expose only `unavailable_code` and not failure details on the overview card?**
The requirement (COL-19) explicitly states "failure details stay admin-only". The code is a user-facing key (e.g. `no_machine`, `stopped_error`) that the UI words locally; the full failure reason stays in admin agent settings. This avoids leaking internal error messages to non-admins while still giving operators a readable signal.

**Why poll rather than subscribe for active edges?**
Active turns already use a poll (`useActiveTurns`, MSG-09). Reusing the same interval (`ACTIVE_TURNS_POLL_MS`) and visibility-aware refresh pattern keeps the two signals consistent and avoids adding a WebSocket channel for a feature whose data changes infrequently relative to the poll window. A WebSocket push could replace polling later without changing the hook's public API.

**Why suppress the welcome screen during the overview-landing check (`'checking'` state) rather than showing it optimistically and then redirecting?**
ROOM-16 says "확인하는 동안 환영 화면을 먼저 그리지 않는다" (do not render the welcome screen before the check). A flash-then-redirect would be disorienting. The check is a single lightweight GET that resolves quickly on a local network; the cost of holding back the welcome screen is negligible.

**Why move the sidebar entry to the top (labelled) rather than keeping the icon-only button near the Agents heading?**
The requirement says the sidebar gets an "Overview" entry at the top. Making it a full-width labelled item matches the Inbox entry pattern and makes the navigation target discoverable without hover-tooltip.

**Assumptions to revisit:** `accessible_room_ids` with `scope="rooms.collection"` and `include_shared=True` matches the room set used by `GET /api/v1/turns/active`; if that scope changes, `list_active_peer_edges` must be updated. The ≥ 2 agent threshold for the overview landing (`OVERVIEW_MIN_AGENTS`) is a product decision recorded in `useOverviewLanding.ts`; it may need adjustment as onboarding patterns evolve.

## Result

- `/` with no room now redirects to the overview for workspaces with ≥ 2 agents without flashing the welcome screen; workspaces with 0–1 agents continue to see the welcome screen.
- The overview highlights answering agents (with a link to their room), dims unavailable agents (with a short reason), and draws flowing edges for active peer questions only.
- With nothing selected, the right panel lists inbox items needing the user, rooms with active turns, and rooms with unread updates; empty sections are hidden.
- The sidebar's "Overview" entry appears at the top, labelled, replacing the icon-only button.
- `GET /api/v1/peer-chains/active` is live; agents get 403, persons get the filtered edge list.
- `GET /api/v1/agent-links` now carries `unavailable_code` per agent.
- Requirements COL-19, ROOM-16, and COL-14 wording are satisfied; 22 files changed, 821 insertions, 74 deletions.
- New test files: `useActivePeerEdges.test.ts`, `useOverviewLanding.test.tsx`; extended: `agentLinks.test.ts`, `AgentLinksPage.test.tsx`, `useActiveTurns.test.ts`, `test_agent_links.py`, `agent-links.spec.ts`.
