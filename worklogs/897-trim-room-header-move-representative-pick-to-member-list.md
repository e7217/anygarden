# refactor(ui): trim the room header to what needs attention and move the representative pick into the member list (#897)

- Commit: `47464f2` (47464f2580c65e7fb4b56c5db5f1430780ea53e8)
- Author: Changyong Um
- Date: 2026-10-19T14:12:27+09:00
- PR: #897

## Situation

The room header had grown to two rows on narrow columns: a status row that always showed a connection badge, an "agents N/N" liveness count, and a representative-agent `<select>`. These were always-visible even when no attention was needed — the connection badge was green nearly all the time, the agent count conveyed no urgency, and the representative selector was a low-frequency admin action sitting in the highest-traffic zone. Every room switch briefly flashed "Disconnected" because the WebSocket starts down on navigation and the reconnect waits 1 second.

## Task

- Remove ambient-status noise from the header; show signals only when something actually needs attention.
- Replace the always-on connection badge with one that appears only after the socket has been down for >2 s (to avoid the ordinary reconnect flash on room switch).
- Replace the "agents N/N" inline count with a warning dot on the participants button, visible only when at least one agent is offline; add an accessible label so screen-readers know the count without the dot.
- Move the representative-agent selector out of the header entirely; re-home it inside the participant-list popover where it belongs semantically (it is a role of one participant, not a global room control).
- Collapse the two-row layout to a single row.
- Add ROOM-10 to the requirement catalog to cover the new behaviour.

## Action

- `docs/requirements.md`: added ROOM-10 row specifying the header-alert conditions and the representative-badge/picker placement, verified by vitest.
- `packages/cluster/frontend/src/components/RoomHeader.tsx`:
  - Removed `AgentParticipant` interface, `representativeAgentId`, `agentParticipants`, and `onSetRepresentative` props.
  - Added `DISCONNECT_GRACE_MS = 2000` constant (line ~328) and a `useEffect`/`useState` pair that starts a 2 s timer on disconnect and clears it on reconnect (lines 402–410); the badge renders only when `showDisconnected` is true.
  - Replaced the "agents N/N" badge with `agentsOffline` calculation (lines 394–400) and an absolute-positioned warning dot on the participants button (lines 456–464); the button's `aria-label` appends the offline count when non-zero.
  - Removed the inline representative `<Select>`.
  - Collapsed the grid from `grid-cols-[…]_auto_auto` with a second status row to a plain two-column single row (line 425).
- `packages/cluster/frontend/src/components/ParticipantListPopover.tsx`:
  - Added `representativeAgentId?: string | null` and `onSetRepresentative?` props.
  - Derived `agents` memo (agents-only subset of sorted list).
  - Added a labelled `<Select>` at the top of the list when `onSetRepresentative` is provided and agents exist (lines 147–164).
  - Added a "Representative" badge on each agent row that matches `representativeAgentId` (lines 172–179).
  - Widened popover from `w-64` to `w-72 max-w-[calc(100vw-2rem)]` to accommodate the picker.
- `packages/cluster/frontend/src/pages/ChatPage.tsx`:
  - Removed the `agentParticipants` memo (was computing a mapped list for the header select).
  - `agentsOnline`/`agentsTotal` memo retained, feeds the participants button warning.
- `packages/cluster/frontend/src/i18n/catalogs/chat.ts`: replaced `chat.agentsOnline` / `chat.agentsOnlineTitle` keys with `chat.agentsOfflineOne` / `chat.agentsOfflineMany` (en + ko).
- `packages/cluster/frontend/src/i18n/catalogs/guest.ts`: added `participants.representative` and `participants.representativeLabel` (en + ko).
- `packages/cluster/frontend/src/components/RoomHeader.test.tsx`: rewrote the "undefined-prop" suite; added new `only-signals-that-need-attention` suite with ROOM-10-tagged tests for timer-gated disconnect badge, offline-agent warning dot, and absence of the count badge and select.
- `packages/cluster/frontend/src/components/ParticipantListPopover.test.tsx`: added `representative agent (#897)` suite covering badge visibility for viewers, admin picker interaction, and no picker when no agents.

## Decisions

- **Grace period of 2 s (not 1 s or 0 s)**: the WebSocket client waits 1 s before its first reconnect attempt after a room switch, so a 1 s grace would still flash the badge on every navigation. 2 s clears that without silencing real outages.
- **Warning dot on the participants button vs. a separate badge**: consolidating into the participants button avoids adding a new permanent slot in the header. The shape (a ring dot) and the accessible label carry the signal beyond colour alone, satisfying WCAG non-text contrast. A standalone `⚠ agents n/N` badge was considered but would use header space for a state that is usually absent.
- **Representative picker moves to the participant list, not the overflow menu**: the representative is a property of a specific participant row. Placing it at the top of the participant list keeps the spatial relationship between picker and affected row, and aligns with the existing pattern of other participant-row actions (admin, remove) living in the same popover. The overflow menu was considered but would bury an already low-frequency action behind two clicks.
- **Removing the `agentParticipants` prop entirely from `RoomHeader`**: once the select moved out, the prop served no purpose. Removing it reduces the surface and the `ChatPage` memo that built the list. Assumption: if any future feature needs per-agent data in the header, it should be introduced on a new, purpose-named prop rather than re-adding the generic list.

## Result

- Header is a single row; no second-row wrap on narrow columns.
- Connection badge is absent during normal room-switch reconnects; appears after 2 s of continuous disconnection.
- "agents N/N" badge is gone; a warning dot on the participants button appears only while an agent is offline, with an accessible count in the button label.
- Admins see the representative picker at the top of the participant list; all viewers see a "Representative" badge on the relevant agent row.
- ROOM-10 added to the requirement catalog; 7 new vitest cases cover the new behaviour.
- `ChatPage` loses the `agentParticipants` memo (minor bundle/complexity reduction).
