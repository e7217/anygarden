# feat(tasks): orchestrator create_task MCP tool (#270)

- Commit: `e50be1a` (e50be1abfa1948eb9ba77e3153ca77483eb09532)
- Author: Changyong Um
- Date: 2026-04-25T23:51:06+09:00
- PR: #270

## Situation

Phase 1 (#266) wired the synthetic mention injection + WS fanout that
auto-executes a task when an admin manually assigns it from the
TaskPanel. Phase 2 (#269) puts that same injection behind a slash
command. But neither path lets the room's *orchestrator agent* react
to a natural-language request — "이거 봐주고 끝나면 PM에 보고해줘" —
by silently breaking the request into N tasks and delegating each one.
Without an MCP tool, the orchestrator has no programmatic way to land a
row in `tasks` from inside its own turn.

## Task

- Expose a new `create_task` MCP tool that the orchestrator can call
  one or more times in a single turn.
- Authorize so only the room-pinned orchestrator can use it, and only
  when the room runs `speaker_strategy='orchestrator'`.
- Reuse Phase 1's mention-injection pipeline so the assignee agent
  wakes via the existing `decide_policy` mention rule — no new
  wake-up protocol.
- Defend against a self-loop: orchestrator assigning to its own
  participant would feed its turn back into itself indefinitely.
- Emit the same WS fanout pair (room channel `MessageOut` + room/
  admin `task.updated`) so 1차/2차 views render the new task without
  polling.

## Action

- `mcp/tools.py` declares the new tool schema in `TOOL_SCHEMAS`
  (room_id + title required; optional `assignee_pid` and `status`)
  and adds an async `create_task` handler that validates inputs,
  loads the room, asserts orchestrator authority + strategy, validates
  the assignee participant (membership + self-loop guard), persists
  the row, and lazy-imports `inject_task_assignment_message` to drop
  the synthetic mention frame. Returns `task_id` in
  `structuredContent` so the router can broadcast.
- `mcp/router.py` adds a `create_task` dispatch branch parallel to
  `mark_task_status`: own-session lifecycle, post-success commit,
  then fanout — pulls the just-injected mention message back from
  `messages` (last 5 rows, filtered by `task_assignment.task_id`)
  and broadcasts the matching `MessageOut`, then calls
  `fanout_task_event` with `event="created"`.
- `tests/test_create_task_tool.py` covers the handler (8 unit
  scenarios) and the JSON-RPC round-trip (2 scenarios).
- `tests/test_mcp_server_create_skill.py` updates the `tools/list`
  guard to expect `create_task`.

## Decisions

Authorization model — three options on the table (plan §3.2 결정 1):

- Open `create_task` to **any agent**: simpler, but lets workers
  spawn tasks for each other and turns the room into an N×N
  delegation mesh. Hard to reason about responsibility.
- **Orchestrator only**: matches doorae's `speaker_strategy =
  orchestrator` mental model exactly — the conductor distributes
  work, workers execute. Phase 1's `mark_task_status` already
  established the "only the relevant agent may mutate" pattern, so
  this is a consistent extension.
- **Admin-only HTTP**: rules out the natural-language flow entirely
  and defeats the purpose of Phase 3.

Picked orchestrator only. The added strategy assertion (`room.
speaker_strategy != "orchestrator"` returns forbidden) is
deliberately strict: an orchestrator that's pinned but not the
active strategy means the admin disabled orchestration without
unpinning, and we treat that as "orchestration is off."

System-prompt injection point — debated three options in plan
§3.1 (decision around the prompt that tells the orchestrator to
decompose):

- **`Agent.agents_md`**: zero infrastructure work; admin writes the
  guidance into the orchestrator's manifest. Picked.
- Per-turn code injection: would have meant a new prompt-assembly
  hook in the spawn pipeline. Out of scope.
- Dedicated schema column: schema change for what is essentially a
  documentation problem.

Self-loop guard — plan §6 R2 flagged this as a real risk: the
synthetic mention message wakes whoever owns the assignee
participant, and that path goes through `decide_policy`. If the
orchestrator targets its own participant, its own turn fires again,
and we have the same shape as a context-window infinite loop. The
handler rejects assignee == orchestrator's participant outright with
a tool-level error so the LLM sees a recoverable failure rather than
a runaway turn. Cycle guards exist in `decide_policy` too, but
defending at the create site keeps the failure mode localized.

Verification UX — Phase 3 ships **auto-apply, no preview**. Preview
+ approval was considered but adds an interrupt pattern and a
multi-turn dance for what is supposed to be a single fluent
delegation. Misfires are recoverable: the existing `DELETE
/api/v1/tasks/:id` route lets a user undo a bad decomposition, and
Phase 1's `task.updated` fanout will sweep the deletion across both
views. If pilot data shows the orchestrator's accuracy is too low,
we can revisit with a Preview step in a follow-up issue.

`structuredContent` shape — settled on `{task_id, room_id,
assignee_pid, status}` rather than echoing the full task. The LLM's
follow-up calls (e.g. another `create_task` for the next subtask, or
a `mark_task_status` later) only need `task_id`; everything else is
already in the LLM's prompt context.

Assumptions worth flagging:
- Orchestrator's LLM is capable enough to call MCP tools reliably
  (Codex / Claude Code / Gemini CLI confirmed; smaller open models
  may need different prompting).
- Orchestrator manifest carries the decomposition guidance — this
  is admin-managed, easy to forget. Docs should call it out.
- Pilot accuracy is acceptable; if not, switch to Preview.

## Result

- 771/771 cluster pytest cases pass (10 new: 8 unit + 2 round-trip
  + 1 tools/list guard update).
- An orchestrator agent in an `orchestrator`-strategy room can call
  `create_task` from inside its turn and produce a fully-wired task:
  DB row, synthetic mention message, WS frames on both the room
  channel (chat stream task card) and the admin user fanout (agent
  profile Tasks tab).
- Self-loop assignment is rejected at the boundary; non-orchestrator
  agents and orchestrator-strategy-off rooms get tool-level errors
  the LLM can retry from.
- Phase 4 candidates (preview UX, task dependencies, decomposition
  audit log) are explicitly deferred and listed in plan §7.
