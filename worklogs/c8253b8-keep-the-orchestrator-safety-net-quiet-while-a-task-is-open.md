# fix(cluster): keep the orchestrator safety net quiet while a task is open

- Commit: `c8253b8`
- Author: Changyong Um
- Date: 2026-09-21
- PR: —

## Situation

`_apply_orchestrator_fallback_nominate` is the server-side anti-stall net for `orchestrator` rooms: when the moderator LLM emits a message with no `[HANDOFF]` and no addressable mention, the server rotates to the next non-orchestrator participant so the room keeps moving (see `docs/research/2026-05-12-multi-agent-turn-taking-mediator-failure.md`). During the live collaboration test on 2026-09-19 the net misfired. `pm` delegated work through the MCP `create_task` tool — which injects a synthetic `[TASK]` mention and wakes the assignee — and then posted a plain status line ("assigned it to dev, will create the qa task when dev reports"). That status message has no handoff and no mention, so the fallback stamped `next_speaker_participant_id` on `qa`, and `qa` replied "I'll verify once the task is assigned" before it had been assigned anything.

## Task

- Stop the nomination when the orchestrator has already put someone to work.
- Keep the net armed for the failure it was built for (a handoff that lost its mention token).
- Do not let the new condition switch the net off permanently.

## Action

- `packages/cluster/anygarden/ws/handler.py`: added acceptance rule 6 to `_apply_orchestrator_fallback_nominate`. Before rotating, it looks for a task in this room whose status is not in `TERMINAL_STATUSES`, whose `assignee_participant_id` is set, and whose assignee is not one of the orchestrator's own participants; if one exists, the helper returns `None`. The docstring's rule list documents it alongside rules 1-5.
- `packages/cluster/anygarden/ws/handler.py`: imports `TERMINAL_STATUSES` from `anygarden.tasks_status`.
- `packages/cluster/tests/test_orchestrator_fallback.py`: `test_fallback_noop_while_a_worker_task_is_open` (todo task assigned to a worker suppresses the nomination) and `test_fallback_nominates_when_every_task_is_terminal` (a done task leaves the net armed), plus a small `_add_task` helper.

## Decisions

- Options weighed: (a) suppress while an open task assignment exists — chosen; (b) correlate the status message with the turn that created the task via `request_id`; (c) suppress whenever the message is `ingest_only`.
- (b) was rejected because the orchestrator's own sends do not reliably carry `request_id` — the live capture showed the `[TASK]` message stamped with one while the orchestrator's follow-up status message had none, so the correlation would silently fail.
- (c) was rejected as too broad: `ingest_only` is the shape of ordinary ambient chatter, and suppressing on it would disarm the net in exactly the stall scenario it exists for.
- Staleness is bounded by machinery that already exists: an unclaimed task fails on the pickup timeout, so rule 6 cannot hold the net off forever. If that timeout is ever removed, this rule needs a time bound of its own.
- Tasks assigned to the orchestrator itself do not suppress, since the room would then be waiting on the very agent that just spoke.

## Result

- `cd packages/cluster && uv run pytest`: 1772 passed, 1 deselected (2 new tests; the open-task test failed before the fix, the terminal-task test passed already).
- Live re-verification of the pm → dev → qa flow has not been run against the merged build yet.
