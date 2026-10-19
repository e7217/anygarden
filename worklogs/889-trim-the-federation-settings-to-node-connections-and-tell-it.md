# refactor(ui): trim the federation settings to node connections and tell it apart from Machines (#889)

- Commit: `bb2ff20`
- Author: Changyong Um
- Date: 2026-10-19
- PR: — (issue #889)

## Situation

The admin "Federation" page (`/admin/federation`) had three tabs: 1. Connection, 2. Shared channel, 3. Task handoff. The task handoff tab asked admins to type executor node/agent, task and delegation UUIDs by hand, while the shared room already delegates a message (`DelegateMessageDialog`), shows delegation cards with stop (`DelegationCard`), and lists unconfirmed submissions with retry and refresh (`SharedRoomConversation`, `useSharedRoom.retry`). The name "Federation" did not say what the page is for, and nothing told it apart from "Machines", which also connects another computer with a code but in a very different relationship.

## Task

- Remove the task handoff tab without losing any capability.
- Keep the shared channel tab, because binding a local room to a channel (`federation.bindChannel`) has no other UI, but stop asking for the local room's UUID.
- Rename the menu so the peer relationship is clear, and add cross-links: Machines for "a computer that runs my agents", Partner nodes for "a server someone else runs".
- No backend or API change.

## Action

- `packages/cluster/frontend/src/components/FederationLiveWorkspace.tsx`: dropped the `task` tab, `TaskComposer`, `DelegationList` and the submissions card. The tab list is now two columns without step numbers. The "Open shared conversation" card moved into the shared channel tab. The header carries a Machines link. `ChannelSelector` picks the local room from `useRooms()` with the shared `Select`, leaving out DMs and rooms that already have a `shared_channel`.
- `AdminMachines.tsx`: the purpose text links to Partner nodes. The "This server" entry gets `self-start` so it no longer stretches beside the empty-state block on mobile (a #888 follow-up).
- i18n: `navigation.federation` became "Partner nodes / 협업 노드". The eyebrow, live notice, description and tab labels were updated. Hint, `selectLocalRoom` and `partnerHint*` keys were added. The roster command-field labels now say they are for roster changes. 28 keys that only the removed tab used were deleted. Numbers were dropped from the mock preview's tab labels, which share the keys.
- Tests: four task-tab tests in `FederationLiveWorkspace.test.tsx` were removed. New tests cover the two tabs, the Machines link, the room picker binding (DM and already-shared rooms excluded) and the bound-room link. `AdminMachines.server.test.tsx` checks the Partner nodes link, and the AdminMachines tests now render inside `MemoryRouter`. `Sidebar.test.tsx` and `FederationWorkspace.test.tsx` labels were updated.

## Decisions

- **How far to cut** (`.tmp/plan-889-federation-settings-trim.md`):
  - Keeping only the connection tab was rejected: `rg bindChannel` showed `FederationLiveWorkspace` is the only caller, so new shared channels could not be set up at all.
  - A room-first "share with another node" flow was deferred: it needs protocol decisions (who mints the channel id, how the invite scope ties to grants).
  - Removing only the task tab was chosen because the shared room already covers delegation, retry, refresh and stop. This was checked in `useSharedRoom.ts:119-121` and `SharedRoomConversation.tsx:65-98` before deleting.
- **Name**:
  - "Node connection" was rejected because Machines already says "connection guide".
  - "Partner server" was rejected because "server" blurs the server/machine split again.
  - "Partner nodes / 협업 노드" says the relationship is between peers.
- **Hook kept intact**: `useFederation.requestTask/cancelTask/submissions/delegations` are now unused by this page. They were left in place to keep this change UI-only, and a room-first flow may reuse them.
- **Assumption**: a room is bindable when it is not a DM and has no `shared_channel`. If the backend allows more than one channel per room, the filter should be revisited.

## Result

- The Partner nodes page shows two tabs (Connection, Shared channel). Delegating, following and stopping work happen in the shared room.
- A local room is bound by choosing it from a list, not by pasting a UUID.
- Machines and Partner nodes each link to the other with one line explaining the difference.
- Vitest 132 files / 976 tests pass and `npm run build` passes. E2E (`sidebar-admin-menu`, `shared-room`, `admin-server-entry`, `machine-detail`) passes; the first-in-worker cold-start flakes passed on retry. Screens were checked at 1280px and 375px.
- Follow-ups: a room-first sharing flow (then the shared channel tab can go), and cleanup of the unused `useFederation` actions.
