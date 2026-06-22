# fix(rooms): return 404 for non-existent project_id on room create (#472)

- Commit: `123ed5d` (123ed5d724194489f90f6b9a9372dfdce99c8ac6)
- Author: Changyong Um
- Date: 2026-06-22T12:32:00+09:00
- PR: #472

## Situation

`POST /api/v1/rooms` with a present-but-nonexistent `project_id` (e.g. `"not-a-real-project"`) did not fail cleanly. `create_room` constructed `Room(project_id=...)` and called `db.flush()` with no prior existence check, so SQLite's enforced foreign key (`PRAGMA foreign_keys=ON`, `Room.project_id → projects.id`) raised `sqlalchemy.exc.IntegrityError: FOREIGN KEY constraint failed`. With no handler for that error it leaked to the client as a **500**, even though the real cause is bad client input that should surface as a clear 4xx.

## Task

- Convert the unhandled FK violation into a deterministic **404** "Project not found" when `project_id` references a project that does not exist.
- Keep the change in lockstep with the sibling validation already in this router (`add_participant` → 404 for a missing room, `update_room` → 400), rather than introducing a new error-handling style.
- Preserve the DM-room contract: a future/None `project_id` (`project_id=NULL`, rooms living outside any project) must skip the check, not 404.
- Do not alter any unrelated behavior; the existing `project_id`-omitted → 422 contract (#179) stays untouched.

## Action

- `packages/cluster/anygarden/rooms/router.py` — added `Project` to the `anygarden.db.models` import block, and in `create_room` inserted a pre-insert guard *before* constructing `Room(...)`: `if body.project_id is not None: project = await db.get(Project, body.project_id); if project is None: raise HTTPException(status_code=404, detail="Project not found")`. `db.get` is a light PK lookup that reuses the session identity map and keeps the transaction clean (no failed flush to roll back).
- `packages/cluster/tests/test_rooms.py` — added `TestRoomCRUD.test_create_room_unknown_project_id`: posts `{"project_id": "not-a-real-project", "name": "ghost-room"}` and asserts `404` with `detail == "Project not found"`. The existing `test_create_room` (valid project_id → 201) and `test_create_room_requires_project_id` (omitted → 422) stand as regression coverage.

## Decisions

- **Pre-check (`db.get`) over `try/except IntegrityError`**: catching the FK error would leave the async session in a dirty state after the failed flush, requiring a rollback that then entangles with the participant insert/commit downstream; the message would also be opaque. A cheap pre-SELECT keeps the transaction pristine and the error precise. Room creation is low-frequency, so the extra query is negligible.
- **Match the sibling pattern**: `add_participant` and `update_room` already do "lookup → 4xx", so the same shape here keeps the router consistent and predictable.
- **None guard retained**: although `RoomCreate.project_id` is currently a required `str`, the `is not None` guard preserves DM-room (`project_id=NULL`) compatibility. An empty string `""` naturally yields `db.get(Project, "") → None → 404`, so no separate `min_length` is needed for this scope.

## Result

The original 500 was reproduced in a test (RED): the FK `IntegrityError` fired exactly as described before the fix. After adding the pre-check (GREEN), the same request returns a clean `404 {"detail": "Project not found"}`, and the valid-`project_id` → 201 path is unchanged. `tests/test_rooms.py`: 49 passing. Full cluster suite: **1176 passed, 1 deselected** (one pre-existing FastAPI `HTTP_413` deprecation warning, unrelated). `ruff check anygarden/rooms/router.py`: clean.
