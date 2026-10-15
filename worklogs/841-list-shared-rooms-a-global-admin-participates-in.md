# fix(shared-channels): list shared rooms a global admin participates in (#841)

- Commit: `9b81769` (9b81769a3a650ede9321d1993538e206a71ebeb7)
- Author: Changyong Um
- Date: 2026-10-15T14:02:00+09:00
- PR: #841

## Situation

`accessible_room_ids()` has two branches: a regular user/agent branch that
honours `include_shared`, and a global-admin bypass branch. The bypass branch
called `_without_shared()` unconditionally and returned early, stripping every
shared room from the result regardless of the `include_shared` flag and whether
the admin was actually on the authority roster or among the current grant
actors. This meant a global admin who legitimately participated in a shared
channel could open its detail endpoint (which already applied the shared
visibility predicate) but would never see it in the rooms list or sub-rooms
listing — causing the web UI to fail to render it.

## Task

- Extend the global-admin code path to re-admit shared rooms when
  `include_shared=True` and a `channel_service` is provided.
- Apply the same `visible_shared_room_ids()` predicate the user/agent branch
  uses — active roster membership on the authority side, or current unexpired
  grant actors on the mirror side.
- Ensure the bypass alone never re-includes a shared room: only rooms the
  admin actually participates in (predicate passes) should be listed.
- Keep parity with what `GET /api/v1/rooms/{id}` (room detail) already allows.
- Update the federation-wiring integration test suite and the architecture doc.

## Action

**`packages/cluster/anygarden/rooms/authorization.py` (lines ~470–493)**

- Changed the admin bypass return from an unconditional `return await _without_shared(db, room_ids)` to a two-step:
  1. Compute `non_shared` as before.
  2. When `include_shared` is `True` and `channel_service` is not `None`, intersect the dropped set with `visible_shared_room_ids()` and union the result back.
- Added inline comment referencing #841 to explain the predicate's intent.
- Updated the module-level docstring block to clarify that the bypass restriction covers only the bypass path itself, not participating admins.

**`packages/cluster/tests/test_app_federation_wiring.py` (lines ~349–421)**

- Added `test_global_admin_lists_shared_rooms_they_participate_in`: places the admin on the authority roster and in the mirror grant actors, confirms all three rooms appear in the listing, verifies detail still succeeds, then tombstones the roster entry and confirms the room disappears — ensuring the bypass cannot re-include it.

**`docs/implementation/app-federation-593.md`**

- Appended a clarifying sentence to the shared-channel admin-visibility note: the bypass restriction covers only the bypass itself; an admin on the roster or grant actors is listed via the same predicate room detail uses.

## Decisions

- **Apply the existing predicate rather than special-casing admin**: the
  `visible_shared_room_ids()` helper is the single source of truth for
  shared-room participant visibility; duplicating its logic in the admin branch
  would create drift. Reusing it keeps the two endpoints consistent.
- **Bypass alone never re-includes**: an alternative would have been to let
  global admins see all shared rooms (full bypass). This was explicitly
  rejected — architecture criterion #593 restricts admin visibility of
  shared-channel content to the bindings/snapshot APIs. The fix is narrowly
  scoped to the participation case.
- **`include_shared=True` + `channel_service` gate**: the predicate call is
  gated on both flags, matching the existing user/agent branch contract. This
  means callers that do not opt in (e.g. content-surface queries) are
  unaffected; the invariant from B-3 (message content stays in the snapshot
  API) is preserved.
- **Assumption**: if `visible_shared_room_ids()` semantics change (e.g. its
  definition of "active roster") the admin branch will silently inherit the
  new semantics. Revisit if those semantics diverge intentionally.

## Result

- Global admins on the authority roster or mirror grant actors now appear in
  rooms/sub-rooms listings for shared channels they participate in, matching
  what room detail already allowed.
- The admin bypass alone cannot re-include a shared room; the predicate must
  pass independently.
- New integration test `test_global_admin_lists_shared_rooms_they_participate_in`
  covers roster membership, grant actor membership, listing/detail parity, and
  tombstone-driven removal.
- Architecture doc updated to document the narrowed bypass intent.
