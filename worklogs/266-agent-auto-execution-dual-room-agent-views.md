# feat(tasks): agent auto-execution + dual room/agent views (#266)

- Commit: `62703cf` (62703cf663ef874bbce3aa83064fdc49d3f2f253)
- Author: Changyong Um
- Date: 2026-04-25T22:55:44+09:00
- PR: #266

## Situation

Rooms exposed a Tasks tab whose backend was nothing more than CRUD over
the `tasks` table. The DB had `assignee_participant_id` (FK to
`participants`) and `Task.created_by`, but the frontend never read or
wrote them, no API ever produced an event from a task change, and no
agent ever started running because of an assignment. There was also no
way to see what work a given agent had on its plate across the rooms it
participated in — the `ix_tasks_room_status` index made room queries
fast but the dual lookup by assignee was unindexed.

## Task

- Make assigning a task to an agent participant auto-trigger the agent
  through doorae's existing `decide_policy` paths, without forking a
  new SDK protocol.
- Give agents a way to report progress back into the task row.
- Surface assignee selection (agents primarily, humans optionally) in
  the room TaskPanel.
- Add a per-agent task aggregation view for admins.
- Keep both views live in real time without polling.
- Avoid widening the agent permission model — Phase 1 must stay
  admin-only on the new aggregation surface because `Agent` has no
  ownership column today.

## Action

- DB: migration `032_tasks_assignee_index_and_human_assignment.py`
  adds `ix_tasks_assignee_status (assignee_participant_id, status)` and
  `rooms.allow_human_assignment BOOLEAN NOT NULL DEFAULT 0`. Models in
  `db/models.py` mirror the additions on `Task` and `Room`.
- Repository: `db/repository.py:append_message` retypes
  `participant_id` to `str | None` so synthetic system messages can
  persist with a NULL sender (matches the long-standing nullability of
  the column itself).
- Helper: `messages/service.py` gains `inject_task_assignment_message`
  (mention-bearing synthetic message) and a `fanout_task_event` that
  broadcasts the new `task.updated` frame to the room channel and to
  every admin's WS sessions via the new user fanout.
- API: `api/v1/tasks.py` injects + broadcasts on POST/PUT,
  validates assignees against the room, picks the synthetic sender
  (orchestrator → caller → NULL system), and emits
  `created/updated/deleted` events. `api/v1/agents.py` adds
  `GET /api/v1/agents/{agent_id}/tasks` with room-name enrichment;
  Phase 1 admin-only.
- MCP: `mcp/tools.py` introduces the `mark_task_status` tool with
  enum-validated status and assignee-only authorization;
  `mcp/router.py` runs it under its own DB session and emits the
  `updated` fanout on success.
- WS: `ws/protocol.py` adds `TaskUpdateOut`; `ws/manager.py` carries
  a per-user reverse index and a new `push_to_users` for cross-room
  fanout; `ws/handler.py` passes `user_id` on `subscribe` so
  logged-in users land in the index.
- Frontend: `lib/taskAssignment.ts` parses the new metadata,
  `components/TaskAssignmentCard.tsx` renders the synthetic message
  as a compact in-stream card, `MessageBubble.tsx` branches on it
  before any other variant. `components/TaskPanel.tsx` gains an
  assignee dropdown gated by the room's `allow_human_assignment`
  flag, plus a window-event subscription for live updates.
  `agent-settings/TasksPanel.tsx` is the new 2차 view, integrated
  into `AgentSettingsDialog.tsx` as a `Tasks` section. `useWebSocket`
  forwards `task.updated` frames as a `doorae:task:updated` event.
- Tests: 4 new files (`test_tasks_injection.py`, `test_tasks_api.py`,
  `test_mark_task_status.py`, `test_agent_tasks_aggregation.py`,
  `test_ws_task_fanout.py`) totaling 30 cases covering the helper,
  the router contract, the MCP tool (unit + JSON-RPC round-trip),
  the aggregation API, and the user fanout. `test_migrations.py`
  bumps its head guard to 032 and `test_mcp_server_create_skill.py`
  adds the new tool to its expected list.

## Decisions

Trigger mechanism — three concrete options were on the table (plan
§3.2 결정 1). Synthetic mention message vs. `next_speaker` stamp vs.
new WS frame `task_assigned`:

- The stamp variant only fires under `round_robin`/`orchestrator`
  speaker strategies; doorae's prevalent `mentioned_only` rooms would
  silently ignore the signal. Rejected.
- A new WS frame would have meant changes in `doorae_agent` SDK +
  `ws/protocol.py` + cluster + frontend simultaneously, and a brand-new
  turn-trigger pathway in the agent. Rejected for first ship — high
  ROI only after a second consumer of the channel exists.
- Synthetic mention message reuses the entire mention path
  (`decide_policy` rule on `<@user:>`), the existing cycle guard, the
  existing typing/cooldown machinery, and the existing room broadcast.
  Picked.

Status sync — picked the explicit MCP tool over inferring from turn
boundaries. Inference is wrong whenever an agent multi-turns a task or
errors mid-run, and `mcp/tools.py` already established the
`isError`/`structuredContent` envelope so adding one tool was cheap.

2차 view permissions — narrowed to **admin-only** for Phase 1 (plan
correction, also commented on the issue). The plan originally hinted at
"admin OR agent owner" but `Agent` has no `created_by_user_id`. Adding
it touches every agent endpoint's auth model and is out of scope. The
permission code in `api/v1/agents.py` is shaped so a future
ownership column drops in as an OR clause.

Sender of synthetic messages — orchestrator's participant when the
room has one, the inviting user's participant otherwise, NULL+
`system_origin` marker as last resort. Originally we considered
seeding a dedicated "system" participant but the `Message.participant_id`
column was already nullable, so the system-origin path is just a
metadata flag. If `system_origin` ends up confusing renderers down the
road, revisit by introducing a system participant per room.

WS fanout — `ConnectionManager` already keyed by `participant_id`;
adding `_by_user` was one dict + two lines in subscribe/unsubscribe.
The alternative ("admin opens an explicit `subscribe_agent_tasks`
frame") would have been more efficient at scale but admin cohorts are
small in practice. Revisit when admin counts grow large enough that
per-event admin fanout becomes measurable.

## Result

- 761 cluster pytest cases pass (30 new). Frontend `npm run build`
  green; `vitest` 327/327 green. Other packages unaffected (the lone
  pre-existing `test_openai.py` failure reproduces on main and is
  environment-driven).
- A task assigned to an agent now produces a synthetic
  `<@user:{pid}> [TASK] {title}` message in the room. The agent's
  `decide_policy` matches the mention and runs. Calling
  `mark_task_status(task_id, status)` from the agent's MCP channel
  flips the row and pushes `task.updated` to both the room and every
  admin's WS sessions.
- Room TaskPanel exposes assignee selection (agents always, humans
  when `Room.allow_human_assignment=true`); the synthetic message
  renders as a compact card instead of a chat bubble.
- New `Tasks` section in the Agent settings dialog aggregates work by
  status with room chips that navigate to the originating room.
- Phase 2 (slash command `/task @agent title`) and Phase 3
  (orchestrator auto-decomposition with a `create_task` MCP tool) are
  deferred to follow-up issues.
