# feat(sidebar): hover '...' menu for rename + delete room (#46) (#48)

- Commit: `bc08afb` (bc08afb7cc17e9b985b98fdf87dd863a131a644c)
- Author: Changyong Um
- Date: 2026-04-15T21:32:54+09:00
- PR: #48 (closes #46)

## Situation

After #45 shipped delete-room UI, rename and delete were reachable only from the `RoomSettingsMenu` in `RoomHeader` — a user had to enter each room they wanted to tidy up. For admins cleaning up stale or experimental rooms, that meant repeated click-enter-delete-navigate cycles. The sidebar's room tree already renders every room the user can see, so it's the natural place to put a bulk-friendly entry point.

## Task

- Add a hover-revealed `···` button per sidebar room row exposing Rename / Delete.
- Reuse `RoomEditDialog` for rename; mirror `ChatPage.handleDeleteRoom` for delete (same confirm copy, 204 handling, selected-room navigate-home on delete).
- Gate visibility on `user.is_admin` only — room-scoped admin/owner gating would require per-room `participants` which the sidebar doesn't preload. Server remains the sole authority on actual delete/edit authorization.
- Work on touch devices where `group-hover` is a no-op.
- Project-space deletion is out of scope — there is no backend `DELETE /api/v1/projects/{id}` endpoint. Tracked separately.

## Action

- **New component** — `packages/cluster/frontend/src/components/SidebarRoomMenu.tsx` (+128): hover-revealed `···` using `opacity-0 md:group-hover:opacity-100` (the pattern also used in `TaskPanel` and `MessageBubble`). On `md-` (touch) the button stays visible so it remains reachable without hover. Outside-click closes via `pointerdown` (iOS Safari coverage) and Escape. All interactive elements `stopPropagation` so clicking inside the menu never triggers the row's own navigate handler. `role="group"` + `aria-haspopup="dialog"` follows the convention `RoomSettingsMenu` established (actual menu role avoided since arrow-key navigation is not implemented).
- **Sidebar wiring** — `packages/cluster/frontend/src/components/Sidebar.tsx` (+139 / −31):
  - `isAdmin` derived from `useAuth` and drilled through `RoomTreeBranch` → `RoomTreeNodeView`.
  - `editRoomId` state at Sidebar root (not inside the menu) so closing the menu doesn't unmount `RoomEditDialog` mid-edit.
  - `handleDeleteRoom(roomId, projectId, roomName)` copied from `ChatPage.tsx:217-250` — same Korean confirm copy, same 204/error handling, plus `fetchRooms(projectId)` for the acting user's snappy refresh and `navigate('/')` if the deleted room was the currently-selected one. WS `room_deleted` invalidation already covers cross-tab refresh through `RoomsProvider`.
  - `roomProjectLookup` Map derived from the existing `rooms` store so the edit dialog's `onSaved` callback can refetch the right project.
  - Row wrapper gets `group relative`; the menu sits absolute-positioned on the right so the hit target and label layout stay stable when the button fades in.
- **Build artifact** — `packages/cluster/frontend/tsconfig.tsbuildinfo` reflects the typecheck run.

## Result

Admins can rename and delete rooms directly from the sidebar without entering each one; non-admins see no change. Verified: `tsc -b` clean, `vite build` succeeds (2.21s). Backend unchanged, so existing `test_rooms.py::test_delete_room_*` and `test_update_room_*` remain authoritative. Manual verification list handed to the user since the frontend has no unit-test infrastructure. Project-space deletion (backend endpoint + cascade rules + WS broadcast) tracked as a follow-up issue.
