# feat(tasks): embed mark_task_status self-instruction in synthetic mention (#275)

- Commit: `8b62baa` (8b62baa7907e8a96bf04a2cf7732d9b54207bf92)
- Author: Changyong Um
- Date: 2026-04-26T00:52:42+09:00
- PR: #275

## Situation

Phase 1 (#266) wired the auto-execution path: assigning a task to an
agent injects a synthetic mention that wakes the agent through
`decide_policy`. E2E verification with `agent01-claude` confirmed the
wake-up works (the agent generated a long BBOM design-review reply for
"UI 검증 — 디자인 검토"), **but** the task's `status` stayed `todo`.
The agent answered as if it were a normal mention and never called the
`mark_task_status` MCP tool, so neither the room TaskPanel nor the agent
profile Tasks tab ever saw `in_progress` or `done`. The plan's §6 R3
flagged this as a gamble: "1차엔 content 텍스트와 mention만으로 충분히
컨텍스트 전달됨을 가정." That gamble lost.

## Task

- Make the assignee LLM actually call `mark_task_status` on this same
  turn it wakes up — without modifying the agent SDK, the spawn
  pipeline, or every agent's manifest.
- Don't change the wake-up trigger (mention path stays the only
  responsibility of the synthetic message).
- Don't leak the new instruction into the chat UI; keep the
  `TaskAssignmentCard` title clean.
- Stay backward-compatible with the existing first-line invariant
  (`<@user:pid> [TASK] {title}`) so anything that already reads the
  message keeps working.

## Action

- `packages/cluster/doorae/messages/service.py` —
  `inject_task_assignment_message` now builds a multi-line `content`.
  Line 1 is the canonical `<@user:pid> [TASK] {title}` (unchanged so
  decide_policy mention-matching, frontend title extraction, and any
  message-log reader keep working). A blank-line separator is followed
  by an italicised self-instruction telling the assignee LLM to call
  `mark_task_status(task_id="…", status="in_progress")` on start and
  `status="done"` on completion (`blocked` for blockers). The
  `task.id` is *interpolated into the prose* so the LLM doesn't have to
  read `metadata.task_assignment` to find it — extra safety.
- `packages/cluster/frontend/src/lib/taskAssignment.ts` —
  `stripTaskMentionPrefix` now slices to the first line before
  stripping the mention/marker prefixes. The trailing instruction
  block never reaches the card title.
- `packages/cluster/tests/test_tasks_injection.py` — added two new
  cases: (a) the canonical first line still carries
  `<@user:pid> [TASK] title`, and (b) the content explicitly includes
  `mark_task_status`, the concrete `task.id`, and the canonical
  status enum values (`in_progress`, `done`). Existing 3 cases pass
  unchanged because they only assert *containment*, not equality.
- `packages/cluster/frontend/src/lib/taskAssignment.test.ts` (new) —
  6 vitest cases: single-line strip, multi-line strip (the scenario
  this PR creates), no-marker fallback, and the
  `parseTaskAssignment` happy/sad paths. The library had no unit tests
  before; this PR seeds them.

## Decisions

Where to put the instruction — four options were on the table
(plan §3.2 결정 1):

- **(A) Embed in `content`** (chosen): one helper, one line of
  prose, applies to every existing agent immediately, and lets us
  interpolate the concrete `task_id` per task. Total infra change
  is one function. The trailing prose is invisible to humans
  because `MessageBubble` short-circuits on
  `metadata.task_assignment` and renders only the card.
- **(B) Auto-augment `Agent.agents_md`**: would require touching
  every existing manifest and would not include `task_id`, so the
  LLM would still have to dig for it.
- **(C) Server-side system-prompt prepend at spawn time**: the
  spawn pipeline lives in the `machine` package and varies per
  engine adapter (claude-code, codex, gemini). A change there
  would explode in scope without buying any per-task
  parameterization.
- **(D) Make the agent SDK read `metadata.task_assignment`**: the
  cleanest design, but a SDK change ships on a different cadence
  than the cluster, so production agents would lag the new
  behavior.

The deciding observation was that `content` is the one channel
guaranteed to land in the LLM's prompt for this exact turn —
metadata visibility depends on the engine adapter — and it's the
only channel where we can interpolate `task_id` cheaply. Plan §3.2
also flagged that an italic-marked self-instruction is a familiar
LLM idiom that doesn't disturb non-instruction-following readers
(humans see the card, not the prose).

Self-instruction format — Korean, since the rest of the doorae
chat surface is Korean. Tool name and parameter names stay English
so the LLM can copy them verbatim into a tool call. Backticks
around `mark_task_status(...)` and the status values to make the
tokens stand out in whatever rendering the LLM might internally
do. Three states (`in_progress`, `done`, `blocked`) listed
explicitly; `todo` left out because that's the initial state the
server already sets.

Idempotency / multi-turn behaviour — the instruction asks for
"start" and "end" calls, leaving multi-turn middle-state silent.
`mark_task_status` accepts unlimited calls (the enum guard is the
only validation), so an agent that pings `in_progress` mid-turn
won't break anything. We didn't try to gate that here; if it
becomes noisy in practice, the dedup is easy to add later.

Assumptions worth flagging:
- The assignee LLM follows italicised self-instructions reliably.
  Codex, Claude Code, Gemini CLI are the three concrete targets;
  smaller models added later may need a stronger imperative form.
- `MessageBubble`'s `task_assignment` short-circuit stays in
  place. If a future change starts rendering the body, the
  instruction prose leaks. The card-render integration test from
  #266 acts as a guard.
- ~10–30 tokens of extra content per assigned task. Negligible
  for both LLM cost and message-log volume.

## Result

- Backend pytest 778/778 green (was 776 before #275 — 2 new injection
  assertions).
- Frontend `npm run build` green; `vitest run` 347/347 (was 341 — 6
  new taskAssignment cases, the file's first unit tests).
- The Phase 3 `create_task` MCP tool path benefits automatically since
  it reuses `inject_task_assignment_message`. No additional change to
  `mcp/tools.py` required.
- Manual e2e (deferred to the user's verification on dev): the
  assignee agent should now call `mark_task_status(...)` and the
  TaskPanel + AgentTasksTab should reflect `in_progress` → `done`
  in real time (Phase 1's WS fanout is unchanged).
- Out of scope, captured in plan §7: manifest auto-augmentation,
  SDK-side metadata-driven system_prompt synthesis, and a
  turn-based fallback for LLMs that ignore the instruction.
