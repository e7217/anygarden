# feat(sidebar): reorder projects in the sidebar per user (#995)

- Commit: `d5d4a4b` (see `git log --grep "#995"`)
- Author: Changyong Um
- Date: 2026-11-09
- PR: #995

## Situation

Sidebar projects were listed in creation order (`list_projects` → `order_by(Project.created_at)`) with no way to change it, so a frequently used project could not be brought to the top. Pinned rooms (#47) could already be reordered per user, with the order synced across the user's sessions.

## Task

- Let a user reorder sidebar projects by dragging, from the keyboard, and from the project menu.
- Store the order per user so it never reshuffles anyone else's sidebar.
- Projects without a saved position (new or newly shared) follow the ordered ones by creation time.
- The order survives reloads and reaches the user's other sessions.
- Out of scope: moving rooms between projects; a shared order for everyone.

## Action

- `anygarden/db/models.py` — `UserProjectOrder` (PK `user_id, project_id`, both FK CASCADE, `sort_order`).
- `anygarden/db/migrations/versions/104_user_project_order.py` — creates `user_project_orders`; test head references updated in `tests/test_migrations.py` and `tests/test_federation_trust.py`.
- `anygarden/api/v1/projects.py` — `_visible_projects` LEFT JOINs the caller's order (ordered first, then creation time) and backs both `GET /projects` and the new `PUT /projects/order`. The PUT drops ids the caller cannot see, keeps positions missing from a partial snapshot, renumbers with `PIN_ORDER_STEP` (1024), and pushes `ProjectOrderChangedOut` to the user's participant sessions.
- `anygarden/ws/protocol.py` — `ProjectOrderChangedOut` (`project_order_changed`).
- `frontend/src/hooks/useRooms.ts` — `reorderProjects` (optimistic, rolls back on failure), listener for `anygarden:projects:order`, and the per-project room fetch now keys on the set of project ids so a reorder does not refetch every project's rooms.
- `frontend/src/hooks/useWebSocket.ts` — forwards `project_order_changed` as a window event.
- `frontend/src/components/Sidebar.tsx` — project list wrapped in its own `DndContext`/`SortableContext`; new `SortableProject` render-prop wrapper; drag handle on each header row when there is more than one project; failure shows a notice.
- `frontend/src/components/SidebarProjectMenu.tsx` — "Move up/Move down" items (disabled at the ends); delete item only when `onDelete` is passed. The menu now shows whenever there is something to reorder or delete.
- i18n strings in `frontend/src/i18n/catalogs/chat.ts`; requirement `ROOM-14` in `docs/requirements.md`.
- Tests: `tests/test_project_order.py`, `frontend/src/hooks/useRooms.test.tsx`, `frontend/src/components/Sidebar.test.tsx`, `frontend/e2e/sidebar-project-order.spec.ts`.

## Decisions

- Options weighed for storage: a shared `Project.sort_order` column, a per-user `user_project_orders` table, or browser `localStorage`.
  - A shared column would let one member reshuffle everyone's sidebar and would need new rules about who may reorder (projects are visible to the creator and all room members, #783).
  - `localStorage` would not follow the user across devices or sessions, unlike pinned-room order (ROOM-09).
  - Chosen: the per-user table. It matches how pinned-room order already behaves, and the user picked the per-user option.
- API shape: a full-snapshot `PUT`, the same as `PUT /rooms/pin-order`. Single-item moves were rejected: with unordered projects mixed in, the server would have to recompute positions, and the WS sync needs the full order anyway.
- Unordered projects go last, not first, so newly shared projects do not push down what the user arranged.
- Keyboard: dnd-kit's `KeyboardSensor` plus menu move items. The menu path is explicit for screen-reader and touch users.
- Unknown or invisible ids are ignored rather than 404'd so the call does not reveal whether a project exists.
- Revisit if project counts grow to hundreds, since every save renumbers the whole snapshot.

## Result

- Users can reorder sidebar projects. The order is per user, persists across reloads, and syncs to the user's other sessions.
- pytest: 6 new tests. The full cluster suite gave 4677 passed and 1 error in a WS scenario test that passes on its own (parallel flake). vitest: 1211 passed. The Playwright drag and keyboard tests passed 20 of 20 runs.
- Known limitation, shared with pinned rooms: dropping with the keyboard while the neighbouring item is still sliding can drop the item back in place. The e2e waits for the animation to finish. Switching measuring to `BeforeDragging` did not fix it.
- Not verified live against a running node.
- CI: the first Linux runs failed in `tests/test_campaigns_cycles.py::test_completed_run_requests_an_evaluation_with_a_persisted_job`, an existing test whose import-time `T0` drifts on a slow suite. #999 fixed it on main, so this branch was rebased onto that fix.
