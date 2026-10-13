# fix(executions): let the lead restart a failed approval-continuation turn safely

- Commit: `f07d05e` (f07d05e89847a55eff5cdafb22edbd4c504510f8)
- Author: Changyong Um
- Date: 2026-10-13T15:25:58+09:00
- PR: —

## Situation

On the b9159e4 live run the DL-04 scenario's release-worker turn — the turn that ran after the user approved the submission — failed with "Selected model is at capacity" immediately after a tool call. Because the task was still in `review_approval` state the lead's `resume_project_task` was rejected with `TASK_WAITING_FOR_USER`, leaving the execution permanently stalled. The only exit was manual user intervention, which violated the expected lead-restarts-on-engine-failure guarantee (EXE-21).

## Task

- Make `resume_blocked_task` accept an approval-continuation turn that failed in the engine, provided every approval on that task has been decided by the user and every approved action's outcome is unambiguously known (either not yet attempted, or finished with a recorded receipt).
- Treat "model is at capacity" / "at capacity" as a transient engine signal so the failure is classified as `MODEL_EXECUTION_FAILED` (retryable), not something else.
- Thread the approval state into the restarted worker's spec so it knows whether to call `execute_approved_project_action` once or just read the receipt.
- Transfer the approval permit (`resume_message_id`) to the fresh continuation so the superseded failed turn can no longer use it.
- Guard against restarts when an action outcome is ambiguous (`executing` / `unknown`) — those could cause duplicate side effects.
- Record the b9159e4 core-suite benchmark run results.

## Action

- **`packages/agent/anygarden_agent/runtime/execution/failure_feedback.py` (line ~39)**: Added `"model is at capacity"` and `"at capacity"` to `_TRANSIENT_SIGNALS`, classifying the live "Selected model is at capacity" error as a transient, non-side-effect failure.
- **`packages/cluster/anygarden/project_executions/recovery.py`**:
  - Added `ACTION_OUTCOME_UNKNOWN_STATES = frozenset({"executing", "unknown"})`.
  - Added `task_approvals(db, *, task)` — fetches all `ExecutionApproval` rows for the task's current revision.
  - Added `approval_continuation_denial(approvals, *, turn)` — returns the blocking reason string or `None`; blocks on `APPROVAL_PENDING`, `ACTION_OUTCOME_UNKNOWN`, `APPROVAL_REJECTED`, and `APPROVAL_CONTEXT_SUPERSEDED` (approved permit owned by a different turn).
  - Extended `_retry_denial` with `decided_approvals_ok: bool` flag; when `True`, replaces the old blanket "any non-succeeded approval → block" check with `approval_continuation_denial`.
  - `engine_failure_resume_reason` now passes `decided_approvals_ok=True` to `_retry_denial` so approval continuations become eligible.
- **`packages/cluster/anygarden/project_executions/resumes.py`**:
  - Added `_approval_state_line(row)` — generates per-approval guidance text telling the resumed worker whether the action is unexecuted, succeeded, or failed.
  - `resume_blocked_task`: loads `approvals` early; rejects on `TASK_WAITING_FOR_USER` if any approval is pending; rejects on `ACTION_OUTCOME_UNKNOWN` if any is in flight or ambiguous; appends `_approval_state_line` output to the task spec for each approval; after injecting the new assignment message, transfers `resume_message_id` to the approved rows via a guarded `UPDATE … RETURNING`.
- **`packages/cluster/anygarden/project_executions/service.py` (`reconcile_execution`)**: When the lead is notified of an engine failure on an approval continuation, the notification text now lists each decided approval with its execution state and explains the lead can restart safely.
- **`packages/cluster/tests/test_project_execution_flow.py`**: Four new `@pytest.mark.req("EXE-21")` tests:
  1. Full happy path — lead restarts, resumed worker executes once, portal receives exactly one request.
  2. Already-executed case — resumed worker calls `execute_approved_project_action` again, reads cached receipt; no second send.
  3. Pending approval — restart rejected with `TASK_WAITING_FOR_USER`.
  4. Ambiguous outcome (`in_flight` / `unknown`) — restart rejected with `ACTION_OUTCOME_UNKNOWN`; `resume_project_task` is absent from the lead's notice text.
- **`packages/agent/tests/test_execution/test_failure_feedback.py`**: Tests for the new transient signal.
- **`docs/requirements.md` (EXE-21)**: Extended requirement text to cover approval-continuation restarts, listing the cases that remain user-only (pending approval, unknown action outcome, rejected approval, exhausted retries).
- **`benchmarks/results/20261013-0530-b9159e4.json`** and **`benchmarks/results/20261013-0611-b9159e4.json`**: Core-suite run results for commit b9159e4 (two separate runs).

## Decisions

- **`decided_approvals_ok` flag rather than a separate code path**: The existing `_retry_denial` already encodes all the other restart preconditions (execution active, turn matches, no question pending, retry count, etc.). Extending it with one flag avoids duplicating that logic and keeps the approval-continuation path as a narrow exception rather than an entirely separate function.
- **Transfer `resume_message_id` rather than creating a new approval**: The existing executor idempotency (`resume_message_id` gates which turn may call `execute_approved_project_action`) already prevents double sends; re-using that mechanism by pointing the permit at the new turn's message requires minimal new state and zero new API surface. The alternative — issuing a new approval row — was rejected because it would bypass the user's decision record.
- **Block on `executing`/`unknown` action outcome**: Restarting when a send might be in flight could duplicate a real-world side effect (e.g. a portal submission). The conservative choice — refuse and leave it to the user — was chosen because correctness trumps automation here. The assumption is that `executing` / `unknown` rows are rare (they arise only from process crashes mid-action); if they become common a separate recovery path should be considered.
- **"at capacity" added to transient signals**: The live error was a deterministic service error, not a network blip, but retrying it on a fresh turn (with the lead's explicit opt-in) is safe because no side effect had occurred at the point of failure. The guard `still never after a side effect` in the comment documents the invariant that must hold for any future additions to `_TRANSIENT_SIGNALS`.

## Result

- `resume_project_task` now succeeds for approval-continuation turns whose approvals are all decided and whose action outcomes are all known, unblocking the DL-04 stall scenario.
- The resumed worker's spec contains per-approval guidance so it does not re-request approval and calls `execute_approved_project_action` at most once (or zero times if already succeeded).
- Four new integration tests pass; the transient-signal test covers the new keyword.
- EXE-21 requirement text updated to reflect the extended contract.
- b9159e4 core-suite benchmark results recorded (two runs).
