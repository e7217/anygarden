# feat(workspace): let agents look over and change the workspace for the person who asked (#938)

- Commit: `5e4f62d` (5e4f62d0c6886c1bef33127e4df4a5983fb14fda)
- Author: Changyong Um
- Date: 2026-10-27T12:32:32+09:00
- PR: #938

## Situation

Agents in Anygarden were previously limited to working only inside rooms and tasks that already existed. Any broader workspace management — creating sub-rooms, adding members, creating routines or metric goals, or hiring new agents — required a human to use the web UI directly. This meant agents could not drive end-to-end workflows without manual intervention, and admins had no way to delegate workspace governance to a trusted agent.

## Task

Expose two new MCP tools (`workspace_query`, `workspace_action`) to agents flagged with `workspace_tools=True` by an admin. The tools must:

- Always act for the person whose message started the turn, using that person's rights.
- Refuse turns that have no identifiable human requester (agent-to-agent, server, guest, shared-room turns).
- Split actions into "allowed" (run at once) and "ask" (post an approval card, run once approved).
- Prevent agents from granting themselves or new hires more privilege than they already have.
- Add DB flags (`workspace_tools`, `can_create_agents`) to the Agent model and a `WorkspaceActionRequest` table (migration 100).

## Action

- **`packages/cluster/anygarden/workspace/policy.py`** (new, 169 lines): defines `ACTIONS` policy table with `ActionSpec(tier, params, summary, admin_only)` entries for `create_room`, `add_room_member`, `create_task`, `reassign_task`, `pause_routine`, `resume_routine`, `create_routine`, `create_goal`, `archive_room`, `hire_agent`; also defines `PERMISSION_LEVELS` and `permission_rank()` helper.
- **`packages/cluster/anygarden/workspace/queries.py`** (new, 157 lines): `run_query()` dispatches `QUERY_KINDS` (projects, rooms, agents, machines, room, tasks, routines, goals) against the DB with the requester's `identity`, returning filtered JSON-serialisable dicts.
- **`packages/cluster/anygarden/workspace/requester.py`** (new, 119 lines): `requester_for_turn()` resolves the `Requester` from a turn proof — rejects non-human starters, missing room, shared rooms, and guest identities via `RequesterRefused`.
- **`packages/cluster/anygarden/workspace/actions.py`** (new): `run_action()` dispatches each allowed action to the appropriate route function; `parse_params()` validates action params via pydantic.
- **`packages/cluster/anygarden/workspace/approvals.py`** (new, 281 lines): `open_request()` creates a `WorkspaceActionRequest` row and posts an approval card message; `approve()` / `reject()` claim the row atomically (`pending → executing`) to prevent double-runs; `_report_and_load()` sends a server notice to wake the agent.
- **`packages/cluster/anygarden/workspace/router.py`** (new, 60 lines): FastAPI router at `/workspace/action-requests/{id}/approve|reject`; mounts into `app.py`.
- **`packages/cluster/anygarden/mcp/workspace_tools.py`** (new, 185 lines): `WORKSPACE_TOOL_SCHEMAS` builds tool definitions from the policy table; `call_workspace_tool()` orchestrates query/action dispatch; `_check_proposal()` pre-validates proposals (goal spec, routine trigger config, hire permission rank) before posting the approval card.
- **`packages/cluster/anygarden/db/workspace_action_models.py`** (new): `WorkspaceActionRequest` SQLAlchemy model with status enum (`pending`, `executing`, `executed`, `failed`, `expired`), optimistic-lock via `status` filter in UPDATE.
- **`packages/cluster/anygarden/db/migrations/versions/100_workspace_agent_tools.py`** (new): adds `workspace_tools` (bool, default false) and `can_create_agents` (bool, default false) columns to `agent`; creates `workspace_action_request` table.
- **`packages/cluster/anygarden/db/models.py`**: adds `workspace_tools` and `can_create_agents` fields to `Agent`.
- **`packages/cluster/anygarden/api/v1/agents.py`**: exposes the new flags in the agent update/read API; audit-logs changes.
- **`packages/cluster/anygarden/mcp/router.py`**: routes workspace tool names to `call_workspace_tool`.
- **`packages/cluster/frontend/src/components/WorkspaceActionCard.tsx`** (new, 134 lines): renders the approval card in chat with approve/reject buttons.
- **`packages/cluster/frontend/src/components/WorkspaceActionCard.test.tsx`** (new): unit tests for the card.
- **`packages/cluster/frontend/src/components/MessageBubble.tsx`**: detects `metadata.workspace_action_request` and renders `WorkspaceActionCard`.
- **`packages/cluster/frontend/src/components/agent-settings/OverviewPanel.tsx`**: adds toggles for `workspace_tools` and `can_create_agents`.
- **`packages/cluster/frontend/src/lib/workspaceActions.ts`** (new, 77 lines): `approveWorkspaceAction()` / `rejectWorkspaceAction()` REST calls.
- **`packages/cluster/frontend/src/i18n/catalogs/admin.ts`, `chat.ts`**: i18n strings for the new UI.
- **`packages/cluster/tests/workspace/test_workspace_tools.py`** (new, 465 lines): integration tests covering query kinds, allowed actions, ask-tier approval flow, requester refusals, and hire permission checks.
- **`packages/cluster/tests/workspace/test_policy.py`** (new, 44 lines): unit tests for the policy table and `permission_rank`.
- **`packages/cluster/tests/test_migrations.py`**: added migration-100 smoke test.

## Decisions

- **"Allowed vs ask" split rather than always asking**: low-risk structural changes (sub-room creation, task creation, member add) run at once like any web UI action; higher-stakes changes (routines, goals, hiring, archiving) require a human in the loop. This matches the principle that agents should be able to drive everyday work autonomously but must not unilaterally change durable configuration or hire new agents.
- **Act as the original human requester, not the approver**: when an approval fires, the action runs with the approver's HTTP identity (for route authz) but the server notice wakes the agent with a turn that acts for the original asker. This preserves auditability (the person who triggered the change is on record) while still letting admins approve admin-only actions.
- **Call route functions directly, not HTTP**: the executor calls the same Python functions the REST routes call, bypassing FastAPI `Depends` guards. This avoids an internal HTTP round-trip and means the policy table's `admin_only` flag is the sole enforcement point — a deliberate trade-off that requires the flag to be kept correct.
- **Optimistic-lock via `status` filter in UPDATE**: `pending → executing` uses `WHERE status='pending'` with rowcount check rather than a SELECT-FOR-UPDATE. This is lighter, avoids deadlocks across separate sessions, and is safe because the transition is one-way.
- **`can_create_agents` as a separate flag**: hiring is further gated by `can_create_agents` on the agent itself (on top of `workspace_tools`), so an admin can let an agent manage rooms/tasks without also letting it spawn more agents.
- **No permission escalation**: a hired agent's `permission_level` is capped at the hiring agent's level, refused in `_check_proposal` when explicitly above the creator, and re-capped at approval (`capped_permission`) in case the creator was demoted meanwhile; the hired agent never gets `workspace_tools` or `can_create_agents`.
- **Review-driven change (before commit)**: the first design recorded the approval's outcome message in the approver's name, which would have let an admin's approval lend the agent admin rights for its next turn. It is now a server notice and `requester_for_turn` resolves such turns to the original asker. The same review led to showing the full stored params on the card (a truncated summary could hide `permission_level` or an evaluator command), committing the outcome before reporting, closing rows stuck `executing` after 15 minutes, deduplicating repeated proposals in one turn, and validating goal/routine specs at proposal time.
- **Revisit if**: a workspace action needs a REST route whose guard is a `Depends` other than `get_current_identity`/`forbid_guest`/`get_admin_identity` — the direct-call executor would silently skip it.

## Result

Agents with `workspace_tools` enabled can now autonomously query the workspace and drive structural changes (room creation, task management) or request approval for higher-stakes operations (routine/goal creation, hiring). The approval card renders inline in chat; approve/reject runs the action once and wakes the agent. Migration 100 is additive (two nullable bool columns + one new table) and backward-compatible. Test coverage: 465-line integration suite + 44-line policy unit tests.
