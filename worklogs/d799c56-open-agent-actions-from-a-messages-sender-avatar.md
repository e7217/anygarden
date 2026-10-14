# feat(chat): open agent actions from a message's sender avatar

- Commit: `d799c56` (d799c5648c5d8cf13eb6d31f632d1563b289004f)
- Author: Changyong Um
- Date: 2026-10-14T14:55:59+09:00
- PR: —

## Situation

The sidebar provides per-agent actions (start a DM, open settings), but once a
user is inside a chat room there was no path to those actions without navigating
away. An agent's avatar and display name appeared as inert text; nothing
indicated the agent was actionable. This created friction for admins who needed
to quickly jump to an agent's DM or settings while mid-conversation.

## Task

- Add a clickable sender chrome (avatar + name) on agent messages that opens a
  small popover with "Direct message" and "Settings…" actions.
- Restrict the menu to admins only, matching the existing DM / settings API
  authz.
- Avoid duplicating the DM list or the settings dialog; the sidebar owns both.
- Preserve the plain, non-interactive sender rendering for guests and non-admin
  users.

## Action

- **`packages/cluster/frontend/src/lib/agentActions.ts`** (new): defines
  `AgentAction`, `AgentActionDetail`, the window-event constant
  `AGENT_ACTION_EVENT = 'anygarden:agent:action'`, `requestAgentAction()` to
  dispatch the event, `AgentActionsContext` (React context, default `false`),
  and the `useAgentActionsEnabled()` hook.
- **`packages/cluster/frontend/src/components/AgentSenderMenu.tsx`** (new):
  wraps the avatar + name children in a `<button>` that toggles a
  `SidebarMenuPopover`. Two menu items dispatch `direct-message` and `settings`
  actions via `requestAgentAction`. Hidden behind `aria-haspopup="dialog"` /
  `aria-expanded`; test IDs `message-sender-menu-trigger`,
  `message-sender-menu-dm`, `message-sender-menu-settings`.
- **`packages/cluster/frontend/src/components/MessageBubble.tsx`** (modified,
  lines ~387–411): introduces `agentActionsEnabled = useAgentActionsEnabled()`
  and the `sender` variable. When the message is from an agent with a known
  `agent_id` and actions are enabled, the avatar + name render inside
  `<AgentSenderMenu>`; otherwise they render unchanged. The `sender` node
  replaces the three previous duplicated avatar/name blocks (task-assignment,
  short-reply, and default variants).
- **`packages/cluster/frontend/src/components/Sidebar.tsx`** (modified): in
  `AgentDMListAdmin`, subscribes to `AGENT_ACTION_EVENT` via a
  `useEffect`/`agentActionRef` pattern (stable ref prevents stale closure
  issues). On `settings` calls `handleOpenSettings`; on `direct-message` looks
  up the agent's existing DM in `allDMs` (full list, not collapsed `dms`) and
  navigates to it, or calls `handleCreateDM` to create one. Also fixes the
  sidebar action-group background: the `+/⋯` button cluster now uses
  `bg-[var(--color-surface)]` when the row is selected and
  `bg-[var(--color-surface-hover)]` otherwise, so only the hovered button is
  highlighted rather than the whole group.
- **`packages/cluster/frontend/src/pages/ChatPage.tsx`** (modified): wraps
  `LocalChatPage`'s return in `<AgentActionsContext.Provider value={!!user?.is_admin}>`.
- **`packages/cluster/frontend/src/components/AgentSettingsMenu.tsx`** (minor
  update): unchanged in substance per diff.
- **`packages/cluster/frontend/src/components/SidebarMenuPopover.tsx`** (minor
  update): `align="start"` prop support confirmed.
- **i18n** (`admin.ts`, `chat.ts`): added keys
  `admin.agentSettings.avatarMenu`, `admin.agentSettings.settingsAction`,
  `chat.directMessage`.
- Tests: `MessageBubble.test.tsx` (+40 lines) covers menu trigger visibility
  (admin vs. guest) and item dispatch; `Sidebar.test.tsx` (+54 lines) covers
  the `AGENT_ACTION_EVENT` listener for both DM and settings actions.

## Decisions

- **Window event over prop drilling / context callback**: the chat page and the
  sidebar are siblings under the app shell; threading a callback from sidebar
  down into individual message bubbles would require passing it through 4–5
  component layers. A named custom event (`anygarden:agent:action`) follows the
  same pattern already used by `anygarden:rooms:*` events in the WS layer,
  keeping the coupling unidirectional and consistent with the codebase's
  existing convention.
- **Stable ref pattern for the event listener** (`agentActionRef.current = …`
  outside `useEffect`): avoids repeated `removeEventListener / addEventListener`
  on every render while still seeing fresh state (closure freshness problem).
  Alternative — adding every state variable to the effect's deps array — would
  cause the listener to be re-registered on every render and was rejected as
  noisy and fragile.
- **Full DM list (`allDMs`) instead of the visible `dms` slice**: when the
  agents section is collapsed the `dms` slice is empty. Using `allDMs` (from
  `useRooms`) ensures an existing DM is found even when the section is
  collapsed, preventing spurious duplicate DM creation.
- **Admin-only gate via `AgentActionsContext`**: consistent with the DM and
  settings API endpoints, which are restricted to admins. Non-admins see no
  menu; the sender chrome is visually identical to the pre-change rendering.

## Result

- Admin users can click any agent's avatar or name in a chat message to open a
  two-item popover: "Direct message" navigates to or creates the agent's DM;
  "Settings…" opens the agent's settings dialog — both without leaving the
  current chat.
- Non-admins and guests see no interactive affordance; sender chrome is
  unchanged.
- The sidebar action-group button cluster no longer bleeds its background
  highlight over the entire `+/⋯` group when the row is hovered; only the
  pointer-targeted button is highlighted.
- 11 files changed, 270 insertions, 21 deletions; new test coverage in
  `MessageBubble.test.tsx` and `Sidebar.test.tsx`.
