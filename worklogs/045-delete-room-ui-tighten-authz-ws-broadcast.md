# feat(rooms): delete-room UI + tighten authz + WS broadcast (#45)

- Commit: `47b5567` (47b5567570d6a9e864211b513b2836c30c0d42d7)
- Author: Changyong Um
- Date: 2026-04-15T18:23:57+09:00
- PR: #45

## Situation

`DELETE /api/v1/rooms/{room_id}` had been wired since the initial release but was gated only by `forbid_guest` — meaning any authenticated non-guest could delete any room they knew the ID of, with no membership or role requirement. The frontend had no delete control at all, so the security gap was invisible to users but reachable via curl. There was also no WS signal, so clients with the deleted room open had no way to react in real time.

## Task

- Tighten server-side authorization to match the existing admin/owner rule used by invites (#25) and participant removal (#40), and keep the 403-before-404 anti-enumeration policy.
- Emit a new WS frame distinct from per-user membership changes so future consumers can tell "I lost access" from "the room ceased to exist".
- Broadcast to both (a) subscribers of the deleted room and (b) the acting members' *other* active participant sockets — so sidebars in sibling rooms update without polling.
- Add a frontend entry point for the delete action with a confirm dialog spelling out the cascade.

## Action

- **Backend authz** — `packages/cluster/doorae/rooms/router.py` (+67): `delete_room` now runs `_require_room_admin_or_owner` BEFORE the `select(Room)` lookup so outsiders receive 403 regardless of whether the room exists.
- **WS protocol** — `packages/cluster/doorae/ws/protocol.py` (+16): new `RoomDeletedOut` frame carrying `room_id`, distinct from `RoomMembershipChangedOut`.
- **Broadcast flow** — the handler captures the room's participant + user_id audience BEFORE wiping rows, then `manager.broadcast`s to subscribers of the deleted room AND `manager.send_to`s each member's other active `participant_id`s. No duplication because the deleted room's Participant rows are gone by the time the per-user query runs.
- **Frontend WS** — `packages/cluster/frontend/src/hooks/useWebSocket.ts` (+12): re-emits `room_deleted` as both `doorae:rooms:invalidate` (triggers `RoomsProvider.refetch`) and a new `doorae:room:deleted` with the `room_id` so a page currently viewing the room can navigate away.
- **Frontend UI** — `packages/cluster/frontend/src/components/RoomSettingsMenu.tsx` (73 changed): new destructive "Delete room" entry in the same red group as "Stop all agents", separator-divided from safe actions. `packages/cluster/frontend/src/components/RoomHeader.tsx` (+3) passes the prop through. `packages/cluster/frontend/src/pages/ChatPage.tsx` (+51): `canRemoveParticipants` gating (mirrors the server rule), native `window.confirm` spelling out the cascade ("messages disappear, child rooms detach to project root, irreversible"), `/` navigate on 204 with the WS listener as a safety net for other tabs / host-kick scenarios.
- **Tests** — `packages/cluster/tests/test_rooms.py` (+242): 5 cases covering member caller → 403, outsider on bogus `room_id` → 403 (not 404), global admin deleting an unrelated room, `RoomDeletedOut` landing on both the deleted-room subscriber and the sibling-room WS, and child rooms detaching (`parent_room_id → NULL`) to pin `archive_child_rooms` semantics against a future cascade-delete regression. Second commit in the PR tightened `>=1` broadcast assertions to exact-count `== 1` per reviewer feedback, with inline documentation of the two-path broadcast reasoning.

## Result

Room deletion is now admin/owner-gated end-to-end, visible in the UI, and propagates to connected clients via WS. Cluster suite at 333 passing, frontend `npm run build` clean. The delete entry point is the foundation the subsequent sidebar hover menu (#46/#48) extends.
