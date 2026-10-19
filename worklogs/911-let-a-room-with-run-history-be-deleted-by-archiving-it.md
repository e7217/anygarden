# feat(rooms): let a room with run history be deleted by archiving it and cancelling its open work (#911)

- Commit: `f4f7ef3` (f4f7ef383d112a4f5103688506ecd5141e1d1366)
- Author: Changyong Um
- Date: 2026-10-19T20:07:16+09:00
- PR: #911

## Situation

Deleting a room that had ever run an agent failed with `409 NATIVE_INVOCATION_HISTORY_DELETE_FORBIDDEN` because invocation accounting rows reference the room with `ondelete=RESTRICT`. The delete menu was still shown to owners but always failed, and no archive UI existed as a workaround. Users had no way to remove a used room from their list.

## Task

- Allow room owners (and global admins) to "delete" a room that has invocation history.
- Preserve all messages, participants, and invocation accounting rows — hard delete is not an option.
- Hide the room from room lists, sub-room lists, and message search after the delete.
- Cancel any open general turns and project executions in the room atomically with the archive.
- Refuse the delete with `409 ROOM_HAS_ACTIVE_EXECUTION` if a project execution is running that the caller cannot cancel.
- Move child rooms to the top level (existing behaviour).
- Update confirmation copy to reflect the new semantics (history kept, not destroyed).

## Action

- **`packages/cluster/anygarden/rooms/retire.py`** (new): `retire_room()` function — finds active project executions (`_active_execution_ids`), cancels each via `cancel_execution`, cancels open general turns (skipping execution-bound ones), then sets `room.archived_at` and calls `archive_child_rooms`. Raises `RoomRetireError("ROOM_HAS_ACTIVE_EXECUTION", …)` on cancel permission failure.
- **`packages/cluster/anygarden/rooms/router.py`** (modified): `delete_room` endpoint replaced the hard-delete + invocation-history guard with a call to `retire_room`. On `RoomRetireError` it rolls back and returns 409. After commit it fans out execution updates, notifies cancelled turns, revokes workspace attachments, revokes room WS connections (`manager.revoke_room`), and restricts the participant poke to rooms other than the deleted one. Room listing and sub-room listing queries now filter `archived_at IS NULL`.
- **`packages/cluster/anygarden/api/v1/search.py`** (modified): message search filters `allowed_room_ids` to those with `archived_at IS NULL` so archived (deleted) rooms are excluded.
- **`packages/cluster/frontend/src/lib/roomDelete.ts`** (new): `deleteRoom(roomId)` wraps the DELETE call and returns a typed `DeleteRoomResult`. `deleteRoomFailure()` picks the right user-facing message (active-execution guidance > server detail > fallback).
- **`packages/cluster/frontend/src/lib/roomDelete.test.ts`** (new): Vitest tests covering 204 success, `ROOM_HAS_ACTIVE_EXECUTION` 409, generic error, and `deleteRoomFailure` message selection. Tagged `[ROOM-11]`.
- **`packages/cluster/frontend/src/components/Sidebar.tsx`** and **`src/pages/ChatPage.tsx`** (modified): three delete call-sites migrated from inline `apiFetch` + ad-hoc error parsing to `deleteRoom` / `deleteRoomFailure`.
- **`packages/cluster/frontend/src/i18n/catalogs/chat.ts`** (modified): confirmation and description copy updated in EN and KO — now says history is kept, agent work is cancelled; adds `chat.deleteRoomActiveExecution` key for the 409 case.
- **`packages/cluster/tests/test_room_delete_retire.py`** (new, 230 lines): pytest suite tagged `ROOM-11` covering: archive + history preserved, room hidden from list/search/sub-rooms, children promoted, open turn cancelled, execution cancelled, permission-denied execution blocks delete unchanged, 409 on active execution.
- **`packages/cluster/tests/test_rooms.py`** (modified): adjusted existing delete tests to expect the new archive-based behaviour.
- **`docs/requirements.md`** (modified): added `ROOM-11` requirement row.

## Decisions

- **Archive instead of hard-delete**: The invocation accounting foreign key uses `ondelete=RESTRICT`, so hard delete is impossible without migrating or dropping that constraint. Archiving leverages the pre-existing `archived_at` field and the archive-blocks-new-work invariant — zero new schema is needed. Alternative (nullable FK or cascade) would require a migration and weaken the audit trail.
- **Cancel in the same DB transaction as the archive**: Doing it atomically means no window where the room is archived but a new turn could still start (the archive gate blocks it), and the execution cancel still sees an operating room. Alternative (cancel first, commit, then archive) could leave the room un-archived if the second step fails.
- **Refuse rather than force-cancel**: If a project execution is running and the caller lacks cancel permission, the delete is refused rather than silently orphaning the run. This preserves the principle of least surprise for whoever owns the execution.
- **Typed `DeleteRoomResult` in frontend**: All three delete call-sites had their own copy of inline `apiFetch` + try/catch JSON parse. Extracting to `roomDelete.ts` avoids the duplication and makes the `ROOM_HAS_ACTIVE_EXECUTION` case testable without a running server.

## Result

- Room owners can delete any room, including those with run history; the room disappears from their list immediately.
- Messages, participants, and invocation accounting rows are retained.
- Open agent turns and project executions in the deleted room are cancelled before the archive commits.
- A project run the caller cannot stop blocks the delete with a clear `409` and changes nothing.
- Message search no longer returns results from archived (deleted) rooms.
- ROOM-11 requirement added and covered by 230-line pytest suite + Vitest unit tests.
