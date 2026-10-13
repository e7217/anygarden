# feat(executions): let the lead restart a child whose engine call failed (#816)

- Commit: `b55bc6c` (b55bc6c7ae28b79f6f0e09412d86d369f7dfe2e0)
- Author: Changyong Um
- Date: 2026-10-12T21:53:13+09:00
- PR: #816

## Situation

A QA benchmark run (DL-04 team run 2) against main hit a transient `ENGINE_ERROR`
on a worker child task. `MODEL_EXECUTION_FAILED` is not retried automatically by
the harness, and `resume_project_task` only accepted `blocked` tasks — so the
lead could not restart the child and the execution stalled waiting for a human
operator to intervene manually.

## Task

Extend the lead-resume path (`resume_project_task` / EXE-21) so that:

- A child whose engine call failed with a **confirmed-finished** receipt
  (`MODEL_EXECUTION_FAILED` or non-automatic `MODEL_TRANSIENT_FAILURE`) can be
  restarted by the lead, sharing the existing 2-per-task resume cap.
- `PROCESS_OUTCOME_UNKNOWN` remains **user-only** (engine may still be running;
  a replay could duplicate side effects).
- The reconciliation notice emitted to the lead turn explicitly offers
  `resume_project_task` when the new path is eligible.
- Record the final re-measurement benchmark results for `7ad7214`.

## Action

- **`recovery.py`**: added `LEAD_RESUMABLE_ENGINE_FAILURES` frozenset and
  `engine_failure_resume_reason()` (lines 231–266). The helper checks task
  status (`blocked`/`failed`), locates the latest `AgentTurn` + `AgentTurnAttempt`,
  verifies the same-task retry gate is open (delegates to existing `_retry_denial`),
  confirms a terminal native receipt, and cross-checks the public recovery payload
  state is `action_required`.

- **`resumes.py`**: updated `resume_blocked_task()` to accept `task.status ==
  "failed"` in addition to `"blocked"` (line 108); calls
  `engine_failure_resume_reason()` when the standard blocked-path guard fails;
  fences delivery rows of earlier requests via `AgentTurnOutbox` update to
  `cancelled` so nothing queued for the failed request races the new turn
  (lines 161–170); clears `task.finished_at`; records `resumed_from` in event
  details.

- **`service.py`**: in `reconcile_execution()`, when phase is `action_required`,
  calls `engine_failure_resume_reason()` and injects a lead-facing text block
  naming the failure, the remaining resume budget, and the exact tool call to
  use (lines 1740–1752).

- **`mcp/project_tools.py`**: minor wording update to the `resume_project_task`
  tool description to reflect the new engine-failure path.

- **`agent/integrations/codex_cli.py`**: one-line adjustment (likely related
  error-code surfacing).

- **`docs/requirements.md`**: EXE-21 requirement entry updated to reflect the
  extended scope.

- **`tests/test_project_execution_flow.py`**: 209 lines of new tests covering:
  - Happy path: lead resumes after engine failure, worker delivers, execution
    completes (`test_lead_resumes_task_after_engine_failure_and_finishes`).
  - Stale-result fence: old turn's `mark_task_status` is rejected; user retry
    of the superseded request returns 409.
  - Limit enforcement: cap shared with blocked-resume path.
  - `PROCESS_OUTCOME_UNKNOWN` rejected with `TASK_OUTCOME_UNKNOWN`.

- **`benchmarks/results/`**: two new benchmark JSON files for the final
  re-measurement on `7ad7214` (runs at 11:48 and 12:30 on 2026-10-12).

## Decisions

- **Shared cap vs. separate cap**: the 2-per-task limit already applied to
  blocked-task resumes. Sharing it for engine-failure restarts avoids a second
  counter and keeps the total restarts the lead can perform bounded. A separate
  cap was considered but rejected as unnecessary complexity when the root cause
  of both paths is the same underlying task being re-delegated.

- **`PROCESS_OUTCOME_UNKNOWN` excluded**: if the native process outcome is
  unknown the engine may still be running, and issuing a new assignment turn
  could cause concurrent side effects. The conservative choice is to require
  a human to confirm the process is stopped before retrying. This is the same
  rationale that keeps unknown outcomes user-only throughout the retry system.

- **New turn vs. new attempt**: a new `AgentTurn` (rather than an additional
  attempt on the failed request) was chosen so the guidance message is carried
  cleanly, the old request's retry budget is preserved, and `source_message_id`
  moves — making the old turn legally unable to write to the task. Using another
  attempt would have reused the failed request's context and potentially confused
  audit trails.

- **Delivery-row fencing**: pending/delivering `AgentTurnOutbox` rows of earlier
  turns are cancelled before the new assignment is injected. Without this, a
  queued delivery from the failed request could race with the new worker turn and
  corrupt the task state (same pattern already used by `retry_task`).

## Result

- The lead can now restart a child whose engine call failed with a confirmed
  terminal receipt, without waiting for human intervention.
- The reconciliation notice for eligible failures explicitly surfaces
  `resume_project_task` with the remaining budget count, so the lead can act
  immediately.
- Stale results from the superseded failed turn are fenced; user retry of the
  old request is correctly refused (409).
- 209 new test lines cover the happy path, the stale-result fence, the cap, and
  the `PROCESS_OUTCOME_UNKNOWN` guard.
- Benchmark re-measurement for `7ad7214` recorded (two runs).
