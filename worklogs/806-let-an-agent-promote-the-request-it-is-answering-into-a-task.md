# feat(tasks): let an agent promote the request it is answering into a task (#806)

- Commit: `5104d69` (5104d69b7339071144b2536f7595a957e0940952)
- Author: Changyong Um
- Date: 2026-10-08T23:59:50+09:00
- PR: #806 (issue)

## Situation

Outside project executions, an agent answered a room with no task. After #802 removed the orchestrator-only `create_task`, nobody tracked real work in an ordinary room unless a user made a task by hand. When an agent needed a fact from the user mid-work, it asked in chat and ended its turn. The answer then had no link back to the work, and the waiting work was not recorded anywhere.

## Task

- Let an agent turn the request it is answering into a task it owns, without passing any ids that the model could get wrong.
- Leave pure question-and-answer turns without a task.
- Let that task wait for a user answer and resume the same agent on the same task.
- Do not wake the agent twice, either when it promotes the request or when the reply also mentions it.
- Leave operating-room and project execution flows unchanged.

## Action

- `packages/cluster/anygarden/general_tasks.py` (new)
  - `promote_turn_request` authorizes the delivered turn lease (`authorize_turn`).
  - It refuses execution turns, turns bound to another task and operating-lead turns (`_is_operating_lead`). It accepts only a top-level human, non-system trigger message.
  - It creates an `in_progress` task assigned to the turn's participant and sets `turn.task_id`. It is idempotent and recovers from a unique-constraint race.
  - `request_input` blocks the task, records `TaskInputRequest`, and posts the question in the source thread. It enforces one pending question, `REQUEST_KEY_REUSED`, and `TASK_TURN_SUPERSEDED`.
  - `answer_from_thread` records the answer, appends it to `spec`, re-claims the task (`claim_task_cas`) and injects a `reassigned` notice that creates a task-bound turn. All of this runs in a savepoint.
  - `newer_turn_for_task` is shared with `mark_task_status`.
- `packages/cluster/anygarden/db/task_input_request_models.py` and migration `092_task_input_requests.py` add the new table.
- `packages/cluster/anygarden/mcp/tools.py`
  - Adds the schemas and handlers for `claim_current_request` and `request_task_input`.
  - `mark_task_status` now refuses a superseded general turn as well.
- `packages/cluster/anygarden/mcp/router.py`: `_call_general_task_tool` requires `turn_proof`, commits, then broadcasts and fans out task events.
- `packages/cluster/anygarden/ws/handler.py`
  - A user thread reply calls `answer_from_thread`. The requester is dropped from `agent_parts` so a mention does not start a second turn.
  - `_publish_thread_answer` shows the resume notice and delivers the outbox turn.
- `packages/cluster/anygarden/api/v1/tasks.py` reuses `is_system_source` from `general_tasks`, which removes a duplicate.
- Agent package
  - `codex_cli.py` adds `general_task_rule` and `turn_workflow`. Pi inherits them through `RoomExecutionAdapter`.
  - The Pi self tools (`pi_self_tools.py` and `.mjs`) list both tools.
- Tests
  - New: `test_general_tasks.py` (27), `test_general_task_thread_answer.py` (3), `test_general_task_rule.py`, and a 092 migration test.
  - Updated: the head pins in `test_migrations.py` and `test_federation_trust.py`, and the expected tool set.

## Decisions

- **Promotion source**
  - Options: a zero-argument tool that derives the trigger from the turn proof, or a `claim_task(message_id)` extension in the style of Raft's CLI.
  - Chosen: the zero-argument tool. Message ids are not reliably exposed in engine input, so an id argument adds a hallucination and cross-message attack surface without any benefit.
- **No wake on promotion**: the promoting turn already holds the lease. Injecting the usual assignment notice would start a duplicate turn and conflict with #719.
- **Storage**
  - Options: a new `task_input_requests` table, a nullable `execution_id` on `ExecutionRequest`, or markers on `Task`.
  - Rejected `ExecutionRequest`: its execution revision fence and operating-room scope are exactly what general tasks lack, and NULL branches would weaken the operating-room invariants.
  - Rejected `Task` markers: they cannot tell a question wait from other `blocked` reasons, and they cannot keep idempotent keys.
- **Answer channel**
  - Chosen: the first human reply in the source thread. An unmentioned user thread reply previously woke nobody, so hooking there does not collide with existing routing.
  - Rejected: a dedicated answer button (front-end scope, and general rooms have no inbox entry point) and the next mention (ambiguous, which is the original problem).
- **Prompt rule placement**
  - The rule is client-side and gated on the absence of `operating_lead` and of an execution assignment. The server refusals are the second line of defense.
  - Rejected: a server-stamped flag, because the information needs no forgery protection.
- **Revisit if**
  - Live runs show agents claiming plain Q&A turns, or skipping claims on real work. The rule wording or decision 1 would need another look.
  - DM tasks turn out to be noise.
  - Thread chatter is often mistaken for answers.

## Result

- Agents can promote, ask, and resume general-room tasks.
- `mark_task_status` rejects superseded general turns.
- Full suites pass: cluster 2741 passed (one xdist-only flake, `test_local_node.py::test_local_execution_disabled_is_logged_outside_integrated_mode`, passed 3/3 alone and green on rerun), agent 678 passed.
- Migration 092 upgrades and downgrades cleanly.
- Pending: live acceptance with a real model (promotion rate on work requests vs. Q&A, and the ask, answer and resume loop). No front-end change; the question renders like the operating-room questions.
