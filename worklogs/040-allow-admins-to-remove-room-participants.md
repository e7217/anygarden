# feat(rooms): allow admins to remove room participants (#40)

- Commit: `dc95c67` (dc95c67f88c05a5512dca8e5b436197e4446c01e)
- Author: Changyong Um
- Date: 2026-04-15T17:10:01+09:00
- PR: #40

## Situation

Rooms had a `POST /api/v1/rooms/{room_id}/participants` endpoint (#17, #19) but no symmetric DELETE, so once a user was in a room there was no admin path to remove them short of deleting the room or revoking the invite offline. The UI likewise had no control. With guest participation now live (#23–#31), this gap also meant guests who had been let into a room could not be ejected without pulling down the room itself.

## Task

- Add `DELETE /api/v1/rooms/{room_id}/participants/{participant_id}` with the same authz surface as `POST`/invites.
- Preserve chat history (messages should keep rendering) and room invariants (last admin/owner must not be removable; representative agent FK must not dangle).
- Broadcast the removal over WS to remaining subscribers so sidebars/participant lists update in real time — but NOT to the departing participant's socket.
- Wire the UI gate with the same privilege check so non-admins never see a button that would always 403.

## Action

- **Backend** — `packages/cluster/doorae/rooms/router.py` (+131): new `delete_participant` using the existing `_require_room_admin_or_owner` helper from `api/v1/invites.py`. Authz runs before the participant lookup (403-before-404, same anti-enumeration policy). Adds 400 for self-removal ("Use leave-room instead"), 409 for last-admin/owner removal computed from `remaining = sum(1 for a in admins if a.id != target.id)`, and clears `Room.representative_agent_id` in the same transaction when the target was the representative. `Message.participant_id` is `ON DELETE SET NULL` (migration 004) so history survives. Invite revocation for removed guests is intentionally skipped — `require_room_member` on the WS side already neutralises them.
- **WS broadcast** — emits `RoomMembershipChangedOut(action="removed")` to subscribers whose `Participant.id != target.id`. Agent removals pass `user_id=""` (schema allows empty string).
- **Frontend** — `packages/cluster/frontend/src/components/ParticipantListPopover.tsx` (+41): optional `onRemove` prop renders a red X on each row that is not the caller and not a room owner; click goes through `window.confirm` first. `packages/cluster/frontend/src/pages/ChatPage.tsx` (+41): `canRemoveParticipants = user.is_admin || myRole === 'admin' || 'owner'`, passed through to the popover, with a DELETE handler that refetches participants on 204 and surfaces server detail via `window.alert`. `GuestRoomPage` deliberately not wired (guests always 403).
- **Tests** — `packages/cluster/tests/test_rooms.py` (+399): new `TestRemoveParticipant` class covers owner removes user (204 + row gone + broadcast), owner removes representative agent (representative field cleared), rank-and-file caller (403), outsider (403 not 404), guest (403 via `forbid_guest`), self-removal (400), last-admin removal (409), unknown participant with admin caller (404), global-admin without room membership (204). Plus `test_removed_participant_does_not_receive_broadcast` added after review flagged that the existing `>=1` assertion would tolerate a future refactor accidentally sending the frame back to the removed socket.

## Result

Admins (global or room-level) can eject users, agents, and guests from a room via the popover X without losing chat history. Full cluster suite at 325 passed on the first commit, then the added broadcast-audience test pins exact-count semantics. Follow-ups noted in the PR body: owner removal + ownership transfer flow, self-removal / leave-room endpoint, toast-based error surface for chat-page mutations.
