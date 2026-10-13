# fix(turns): ride out provider capacity outages without repeating side effects

- Commit: `30bd83e` (30bd83ea0e7c237cdebd684e0aecf4ea2fc5c1fd)
- Author: Changyong Um
- Date: 2026-10-13T16:46:59+09:00
- PR: —

## Situation

During benchmark core runs (b9159e4 / f07d05e), the cluster encountered extended "Selected model is at capacity" periods lasting several minutes. The single 5-second retry configured for transient failures was insufficient to bridge these outages. Additionally, general-room turns (those outside a project execution) were never retried at all: the agent posted a visible failure notice immediately, which was accepted as the turn's answer and blocked any server-side retry from taking effect.

## Task

- Give transient failures their own bounded retry budget (3 retries at 30/60/120 s backoff) that is distinct from the lease-expiry recovery path (which keeps its single retry per ENG-03).
- Apply this budget uniformly to both execution turns (receipt-based) and general-room turns (frame-based classification).
- Suppress the agent's failure notice for general-room turns that qualify for a server-side transient retry, so the retry can actually run.
- Classify `TIMEOUT_AFTER_SIDE_EFFECT` as a new, non-transient failure code for timeouts that occur after a side-effecting tool or item has already started, so partial-effect turns are never silently retried.
- Ensure non-transient general-room failures (e.g. timeout after a tool call) are closed immediately rather than being held for lease recovery.

## Action

**`packages/cluster/anygarden/turns/service.py`**
- Added `TRANSIENT_FAILURE_REASON`, `MAX_TRANSIENT_RETRIES = 3`, `TRANSIENT_RETRY_BACKOFF_SEC = (30, 60, 120)` constants.
- Added helpers `transient_retry_delay()`, `is_transient_retry_attempt()`, `transient_retries_used()` to count and schedule transient retries independently of the lease budget.
- Added `_general_failure_receipt()` to classify a general-room terminal frame as `"transient"`, `"permanent"`, or `None` based on `handler_finished` frame fields.
- In `record_lifecycle` (execution receipt path): replaced the `turn.retry_count < turn.max_retries` guard with a `transient_used < MAX_TRANSIENT_RETRIES` check, using `transient_retry_delay()` for the next retry timestamp.
- In `record_lifecycle` (general-room `handler_finished` path): probes `_general_failure_receipt()` and either closes the turn immediately (permanent proof) or stamps `attempt.reason = TRANSIENT_FAILURE_REASON` for the stall-recovery scan.
- In `recover_stalled_turns`: added `transient_retry` flag, separates transient vs. lease budget in `retry_allowed` / `retry_delay`, and replaces `turn.retry_count >= turn.max_retries` with `not retry_allowed`.

**`packages/agent/anygarden_agent/runtime/handler_wrapper.py`**
- Added `server_retries` flag in `RoomHandlerSupervisor`: when a leased non-execution, non-delegation turn fails transiently before any side effect, skip posting a failure notice so the server's retry path can proceed.

**`packages/agent/anygarden_agent/runtime/execution/contracts.py`**
- Added `TIMEOUT_AFTER_SIDE_EFFECT` to `FAILURE_CODES`.

**`packages/agent/anygarden_agent/runtime/execution/failure_feedback.py`**
- `terminal_failure_category()`: `TIMEOUT_STOPPED` maps to transient; `TIMEOUT_AFTER_SIDE_EFFECT` falls through to `MODEL_EXECUTION_FAILED` (non-transient).

**`packages/agent/anygarden_agent/runtime/execution/codex.py`**
- Introduced `_FAILURES: ContextVar[CodexFailureTracker]` so the timeout handler can read whether a side-effecting item has started.
- `TimeoutError` handler now emits `TIMEOUT_AFTER_SIDE_EFFECT` if `failures.side_effect_started`, otherwise `TIMEOUT_STOPPED`.

**`packages/agent/anygarden_agent/runtime/execution/pi.py`**
- Introduced `_SIDE_EFFECTS: ContextVar[dict]` (`{"started": bool}`) updated on `_starts_tool()` events.
- `TimeoutError` handler checks `effects["started"]` to pick between the two timeout codes.

**Tests**
- `packages/cluster/tests/test_durable_turns.py`: 136-line new file covering transient retry budget, backoff delays, and general-room classification.
- `packages/cluster/tests/test_project_execution_flow.py`: extended with 222-line additions for the new retry paths.
- `packages/agent/tests/test_handler_supervisor.py`: 59 new lines covering the suppressed failure-notice path.
- `packages/agent/tests/test_execution/test_pi_runtime.py`: 34 new lines for `TIMEOUT_AFTER_SIDE_EFFECT` classification.
- `packages/agent/tests/test_execution/test_local_execution.py`: 33-line additions.

**`docs/requirements.md`**: Minor update (2-line change, likely reflecting the new retry behaviour in requirements).

## Decisions

- **Separate transient budget from lease budget**: The lease recovery (ENG-03) was designed for unknown-outcome scenarios (process lost, generation interrupted) and must stay bounded at 1 retry. Mixing transient provider errors into that path would either exhaust the lease budget on capacity spikes or widen the lease risk. A dedicated 3-retry budget with exponential backoff cleanly separates the two concerns.
- **`TIMEOUT_AFTER_SIDE_EFFECT` vs a single `TIMEOUT_STOPPED`**: Retrying a timeout after a tool call started could cause duplicate writes, payments, or API calls. The safest cut: any turn that saw a tool-start event gets a non-retryable code; the pre-tool timeout remains transient. This required threading a context-var (`_FAILURES` / `_SIDE_EFFECTS`) from the collector into the timeout handler.
- **Suppressing the agent failure notice**: The alternative (let the agent post the notice and have the server delete it on retry) was rejected because the notice can be delivered to the room before the server processes the receipt — a race that's hard to close. Silence is the correct signal when the server owns the retry; the server posts its own exhaustion notice if retries are spent.
- **Frame-based classification for general-room turns**: General-room turns have no stored receipt event. The `handler_finished` frame carries enough proof (`native_outcome`, `native_reason_code`, `native_transient`, `native_process_state`) to make the transient/permanent decision locally without an extra round-trip. The `None` (inconclusive) case falls back to the existing lease recovery path, preserving backward compatibility.
- **Assumption**: `native_transient` being `True` in a `handler_finished` frame is a trustworthy signal emitted by the agent under the same conditions that set `transient: true` in the receipt. If the agent side ever changes this flag's semantics, the general-room transient classification should be revisited.

## Result

- Transient "model at capacity" failures in both execution and general-room turns are now retried up to 3 times with 30/60/120 s backoff before being declared exhausted.
- General-room turns no longer surface a premature failure notice during a retryable outage.
- Timeouts that occur after a side-effecting tool or item started are classified as `TIMEOUT_AFTER_SIDE_EFFECT` and never retried automatically.
- Non-transient general-room failures (e.g. proven timeout after tool call) are closed immediately rather than occupying the lease recovery slot.
- 634 insertions / 83 deletions across 16 files; all covered by new tests in `test_durable_turns.py`, `test_project_execution_flow.py`, `test_handler_supervisor.py`, and `test_pi_runtime.py`.
