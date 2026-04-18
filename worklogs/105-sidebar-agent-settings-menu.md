# feat(sidebar): apply AgentSettingsMenu to admin agent DM items (#105)

- Commit: `49c7482` (49c7482368f845f77feb841d3054e03b1ac676f3)
- Author: Changyong Um
- Date: 2026-04-18T17:14:07+09:00
- PR: #105

## Situation

PR #104 landed `AgentSettingsMenu` — the collapsed ⋯ overflow menu (Edit avatar / Edit manifest / Manage rooms / Activity / Copy ID / Delete) — but wired it to `AdminMachines` only. The sidebar's Agents section still rendered each agent DM as a single click-to-navigate button, so an admin who wanted to, say, swap an agent's avatar or scan its activity log while already in a DM had to navigate over to `/admin/machines`, find the machine hosting that agent, and pick the row. Two surfaces, same admin concept, inconsistent reach.

## Task

- Attach `AgentSettingsMenu` to each admin row in the sidebar's Agents DM list, with the same six actions AdminMachines exposes.
- Keep the trigger hover-revealed so the sidebar stays visually calm with four+ agents; keep it pinned open while the popover is up.
- Mount the four dialogs (Edit / Manage rooms / Activity / Avatar) inside the admin-only `AgentDMListAdmin` subcomponent so the non-admin render path is untouched — no new `useAgents` mount, no new state, no new dialogs.
- Make delete refetch the sidebar DM list immediately so the deleted row doesn't linger until the WS invalidate lands.
- Do not refactor AdminMachines' handlers into a shared hook — the two call sites diverge in post-mutation refetch (AdminMachines refreshes machine detail, sidebar refreshes the DM list).

## Action

Frontend-only change, confined to `packages/cluster/frontend/src/components/Sidebar.tsx`:

- Imports: added `AgentSettingsMenu`, `AgentEditDialog`, `AgentRoomsDialog`, `AgentHistoryDialog`, `AvatarPickerDialog`.
- `AgentDMListAdmin` now destructures the full admin surface from `useAgents()` — `deleteAgent`, `updateAgent`, `fetchAgentFiles`, `upsertAgentFile`, `deleteAgentFile` — and pulls `fetchAgentDMs` from `useRooms()` for the post-delete refresh.
- Four local `useState` pairs for dialog visibility: `editDialogOpen`/`editingAgent`, `roomsDialogOpen`/`roomsAgentId`, `historyOpen`/`historyAgentId`/`historyAgentName`, `avatarDialogOpen`/`avatarAgent`. Shape mirrors `AdminMachines.tsx:193-206` exactly so a future diff between the two sites stays readable.
- Six handlers copied from `AdminMachines` and adapted: `handleEditManifest`, `handleEditAvatar` (both do `agents.find(...)` to resolve the full `Agent` record that child dialogs need), `handleManageRooms`, `handleShowHistory`, `handleCopyAgentId` (try/catch on `navigator.clipboard.writeText` — same graceful degrade as AdminMachines), `handleDeleteAgent` (`confirm` → `deleteAgent` → `fetchAgentDMs()` instead of AdminMachines' `fetchDetail(selectedId)`).
- Row layout swap: the previous single `<button>` per DM is now `<div class="group ...">` + inner navigate-`<button>` + `<span>` wrapper holding the `AgentSettingsMenu`. Hover and selected-row styles moved onto the outer `<div>` so the row still highlights as a whole; inner button keeps the nav click, menu button keeps the action click. Same split pattern as `PinnedRoomItem` (Sidebar.tsx:880).
- `<span>` wrapper gets `opacity-0 group-hover:opacity-100 has-[[aria-expanded=true]]:opacity-100 transition-opacity` — hover-reveal with a `has-*` selector that pins the trigger visible while the popover is open (AgentSettingsMenu's trigger carries `aria-expanded`). Tailwind 4's `has-*` support is required; the repo is on `@tailwindcss/vite` ^4.
- Menu only renders when `findAgentForDM(dm, agents)` resolved an agent record (fallback-name match path is unchanged). DMs whose representative agent can't be matched still navigate but don't show a broken menu.
- Four dialogs mounted at the tail of `AgentDMListAdmin` — `AgentEditDialog` / `AgentRoomsDialog` / `AgentHistoryDialog` / `AvatarPickerDialog`. `AgentRoomsDialog.onChange` wires to `fetchAgentDMs` (rather than `fetchDetail`) so room edits that remove the DM are reflected.
- Added `data-testid="sidebar-dm-actions-${dm.id}"` on the menu wrapper for future tests.

## Decisions

The plan in `.tmp/plan-105-sidebar-agent-settings-menu.md` pre-stated the four tradeoffs; decisive threads:

- **Dialog mount location: inside `AgentDMListAdmin` vs Sidebar root.** Chose the subcomponent because it already gates on `is_admin` and already consumes `useAgents()` — pulling the state up to Sidebar root would force the non-admin render branch to carry dialog state and would blur the permission boundary. Accepted tradeoff: when both `/admin/machines` and `/rooms/:id` happen to be mounted (they never are — they're sibling routes), the dialogs would double-mount. If that assumption breaks (e.g. a side panel implementation), revisit.
- **Handlers: shared hook vs copied.** Copied. The two call sites' `onChange` hooks diverge — AdminMachines refreshes the machine detail view after room changes, sidebar refreshes the DM list. Extracting a `useAgentRowActions()` hook would require the shared version to accept two `onChange` callbacks, which turns DRY into parameter gymnastics. The plan quotes "유사해 보이지만 변경 이유가 다른 코드는 중복이 아니다" — held to it. If a third call site shows up, revisit.
- **Menu visibility: hover-reveal vs always-on.** Hover-reveal, matching PinnedRoomItem's unpin handle. Sidebars with 6+ agents would get noisy with always-visible ⋯. The `has-[[aria-expanded=true]]` escape hatch keeps the trigger pinned while the popover is open so the user can move the pointer to a menu item without the trigger fading out from under them.
- **Active-agent identifier: full record vs id-plus-lookup.** Id-plus-lookup. AdminMachines does `agents.find(a => a.id === agentId)` inside each handler for the same reason — the `agents` list is reactive, so a looked-up record is always the latest one. Store-the-object would freeze a snapshot, and an admin who edits the manifest and then reopens from stale state would see stale fields.

## Result

- Admin flow: hover any agent DM row → ⋯ appears on the right; click to get the six-item menu (Edit avatar · Edit manifest · Manage rooms · Activity · Copy ID · separator · Delete). Each opens the corresponding dialog inline in the sidebar context. Delete confirms, calls the API, and the row drops without a page reload.
- Non-admin flow: unchanged. Plain DM list, no menu trigger, no `useAgents` mount.
- `AdminMachines` flow: unchanged — same handlers, same dialog mounts, no regression in its agent-row menu.
- Tests: frontend `npm run build` green (tsc + vite), `npm test` 173/173 across 18 files, no new tests (no existing Sidebar test suite to extend; the plan flagged this as optional). `AgentSettingsMenu` / `AgentEditDialog` / `AvatarPickerDialog` suites continue to pass.
- Out of scope, tracked for follow-up: a dedicated Sidebar test file covering the admin-row menu behavior (DOM presence, opacity-on-hover, dialog wiring) would be valuable once the sibling #106 (sidebar collapse/expand) lands and the layout stabilizes.
