# fix(rooms): decouple agent DM rooms from project lifetime (#179)

- Commit: `3b0e6af` (3b0e6af8d53459122ed9ea5bddaf3f188e916dd5)
- Author: Changyong Um
- Date: 2026-04-19T15:33:51+09:00
- PR: #179

## Situation

Agent DM rooms were auto-attached to the "oldest project" on creation (`agents.py:208-223`) because `rooms.project_id` was `NOT NULL + ON DELETE CASCADE`. Deleting that arbitrary host project cascade-wiped every agent's DM in the system, even for agents tied to totally unrelated work. Observed in the `admin@doorae.dev` environment: 4 running agents (`agent01-claude`, `agent01-codex`, `agent01-gemini`, `test-agent`) with `GET /api/v1/rooms?is_dm=true` returning `[]`, so the Sidebar's `agentDMs.length > 0` gate (`Sidebar.tsx:639`) hid the Agents section entirely. Domain-wise DMs are 1:1 user↔agent channels that have nothing to do with project scope — the `Agent` table doesn't carry a `project_id` either — yet `Room.project_id` forced them into one.

## Task

- Make DM rooms independent of any project's lifecycle so project deletion can never remove unrelated DMs.
- Keep the "a regular room belongs to exactly one project" invariant intact at the API surface.
- Backfill existing DM rows in the DB so the column transition doesn't leave stale project links behind.
- Thread the schema change through graph/search/frontend type sites without breaking tsc, pytest, or existing callers.

## Action

- `db/models.py:65-76` — `Room.project_id` flipped to `Optional[str]` with `nullable=True`, keeping the existing `ON DELETE CASCADE` (cascade only fires when the FK value matches a deleted row, so NULL DMs are naturally excluded).
- `db/migrations/versions/025_dm_room_project_nullable.py` — new Alembic revision: `batch_alter_table` to relax the column, plus a backfill `UPDATE rooms SET project_id=NULL WHERE is_dm=1 AND project_id IS NOT NULL`. Downgrade deletes orphan DMs since the old schema cannot represent them.
- `api/v1/agents.py:208-223` — removed the `first_project` lookup; DM is now created with `project_id=None`. Unused `Project` import dropped.
- `rooms/router.py:59-66` — `RoomOut.project_id` typed `Optional[str]`; `RoomCreate` keeps `project_id: str` required so the public POST surface still refuses unowned rooms.
- `api/v1/graph.py:363-370` — `project_ids = {r.project_id for r in rooms_list if r.project_id is not None}` to keep `Project.id.in_({…, None})` from degrading into a false predicate.
- `frontend/src/hooks/useRooms.ts:15-20` — `Room.project_id: string | null`.
- `frontend/src/pages/ChatPage.tsx` — guarded every project-scoped call site: `currentProjectId = currentRoom?.project_id ?? undefined` (SearchDialog), `if (currentRoom.project_id) for (...) byId.set(...)` (parent breadcrumb), and three refresh sites (`handleSetRepresentative`, `CreateSubRoomDialog.onCreated`, `RoomEditDialog.onSaved`) only call `fetchRooms(project_id)` when it's non-null, falling through to `fetchAgentDMs()` for DMs.
- Tests: new regression tests `test_agent_dm_has_null_project_id` and `test_project_delete_preserves_dm` in `test_agents_api.py::TestAgentAutoDM`; `test_create_room_requires_project_id` in `test_rooms.py::TestRoomCRUD` guards that the public POST still enforces project_id. `test_migrations.py` head version bumped from `"024"` to `"025"`.

## Decisions

Three options were weighed in `.tmp/plan-179-dm-room-project-nullable.md §3.2`:

- **A. `Room.project_id` nullable** — DM is NULL on the same table. One-column migration, five small call-site touches, FK cascade stays NULL-aware. ✅ chosen.
- **B. Separate `AgentDM` table** — cleaner domain boundary but blows up Message/Participant joins and forces large frontend-type work for one bug.
- **C. Hidden "system project"** — keeps the schema untouched but entrenches the wrong mental model: DMs still appear to "belong" to a project, surfacing in admin/search/graph paths by accident.

The decisive observation was that `Agent` already has no `project_id`; only `Room` did, purely because the "at least one row" DB constraint forced a host project. Once DMs carry NULL, every downstream consumer either already handles NULL (the `==` SQL filter in `delete_project` and `list_rooms`) or needed a one-line guard. B's churn wasn't justified by the benefit, and C would leave the next maintainer to rediscover the same bug under a different guise.

Assumptions worth revisiting if they change:
- No production path reads `r.project_id` assuming non-null *and* acts on it (grep verified: only serialization and `==`-filter queries).
- The downgrade path's "delete orphan DMs" semantics are acceptable — documented in the migration. If future work persists per-DM metadata worth preserving, the downgrade needs to move rows to a project instead of deleting them.

## Result

- Agent DMs now survive project deletion. `test_project_delete_preserves_dm` verifies end-to-end: creating an agent, deleting the seeded project, confirming the DM row still exists with `project_id IS NULL`.
- Cluster pytest suite 616 passed. Frontend `npm run build` (tsc) passes. Frontend vitest suite 245/245 passed. Migration upgrade/downgrade both run cleanly on a fresh SQLite DB.
- Existing orphaned agents in the `admin@doorae.dev` environment (whose DMs were already cascade-deleted before this fix) still need DM backfill — tracked as a follow-up per plan §6; not in this PR.
