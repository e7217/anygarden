# feat(projects): delete-project UI + API with cascade confirmation (#77)

- Commit: `243fd0b` (243fd0b55266abaadf8e76611526bc2f36192643)
- Author: Changyong Um
- Date: 2026-04-16T23:59:21+09:00
- PR: #77

## Situation

`/api/v1/projects` exposed only `POST` and `GET` — no way to delete a project once created. The Sidebar likewise had a "New Project" button but no removal control. Empty or experimental projects accumulated in the tree and were only disposable by direct DB edit. The gap mirrored the state of room deletion before #45 but was more severe: even the hidden curl path was missing, so there was no escape valve at all.

## Task

- Add the backend `DELETE /api/v1/projects/{project_id}` endpoint with the same auth gate as `POST`/`GET` (`forbid_guest`), since projects don't carry an owner/admin role model today.
- Make the DB cascade actually fire from ORM `session.delete(project)` — without the model-relationship fix, SA tries to `UPDATE rooms.project_id=NULL` before the FK cascade, which violates NOT NULL.
- Broadcast `RoomDeletedOut` per removed child room to both the deleted-room subscribers and every affected user's *other* active `participant_id` sockets, so sidebars in sibling rooms update without polling — reuse the pattern #45 landed for room deletion.
- Wire a Sidebar entry point with a confirmation dialog whose wording branches on whether the project has any rooms: a plain confirm when empty, a cascade warning with the exact child-room count when not.

## Action

- **Backend endpoint** — `packages/cluster/doorae/api/v1/projects.py` (+89/-1): `delete_project` snapshots `room_ids` and the affected `user_ids` BEFORE the commit, then `db.delete(project)` + cascade removes rooms + participants + messages atomically. A post-commit pass broadcasts `RoomDeletedOut` per child room and, for each affected user, pushes the same frame to every still-live participant socket they hold.
- **ORM relationship fix** — `packages/cluster/doorae/db/models.py` (+9/-1): `Project.rooms` gains `passive_deletes=True` and `cascade="all, delete-orphan"`, matching the inline comment on `Room.participants` that documents the exact failure mode ("UPDATE rooms.project_id=NULL violates NOT NULL"). The FK already declares `ON DELETE CASCADE`; the relationship change is what lets the ORM actually defer to it.
- **Frontend hook** — `packages/cluster/frontend/src/hooks/useRooms.ts` (+25/-2): new `deleteProject(projectId)` in `RoomsContextValue`. On 204 it drops the project from `projects` and the rooms bucket from `rooms` in one render, keeping the acting session's sidebar snappy; the per-room WS broadcasts reconcile the rest through the existing `doorae:rooms:invalidate` path.
- **Kebab menu component** — `packages/cluster/frontend/src/components/SidebarProjectMenu.tsx` (+104, new): hover-revealed `MoreHorizontal` trigger with a single "Delete project" destructive item. Deliberately a separate file from `SidebarRoomMenu` for the same reason that file calls out in its own header comment — narrow context, single action, keeps the row-level menu's layout assumptions out of the sidebar. Mobile opacity stays at 100 because the row's outer click toggles expand/collapse.
- **Sidebar integration** — `packages/cluster/frontend/src/components/Sidebar.tsx` (+113/-9): the project header row is now a `group` flex container holding the existing toggle `<button>` and the new `SidebarProjectMenu` side by side (nested buttons would be invalid HTML). A new `deleteProjectTarget` state drives a confirm `Dialog` whose body branches on `(rooms[id] ?? []).length`: a plain confirm for empty projects, a cascade warning with the exact room count otherwise. `handleDeleteProject` snapshots the affected room ids before the mutation so the post-delete `navigate('/')` check still fires even after local state clears, and prunes the project id from `expandedProjects` so a reused id can't inherit a stale flag.
- **Tests** — `packages/cluster/tests/test_projects.py` (+80): four new cases — empty-project delete returns 204 and disappears from the list; cascade delete returns 204 and `GET /rooms/{id}` is 404 for every child; unknown id is 404; no-auth is 401. All 7 project tests pass alongside the 29 `test_rooms.py` cases untouched by the ORM relationship change.

## Result

Projects can now be deleted from the Sidebar with a context-aware confirmation that makes the cascade impossible to miss. Cluster suite at 385 passing, `npm run build` clean (tsc + vite), machine suite at 213 passing. The agent suite's pre-existing `test_openai.py` failure (missing `OPENAI_API_KEY`) is on `main` too and is orthogonal to this change. The hook exposed by `useRooms` is a drop-in for any future delete entry point (e.g. a project-header action on the main area) without further state plumbing.
