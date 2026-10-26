# feat(campaigns): request evaluations, judge cycles and close them (#930)

- Commit: `e70aaf6` (e70aaf6)
- Author: Claude Opus 5.5
- Date: 2026-10-26
- PR: #930

## Situation

The campaign scheduler could open cycle executions and track their progress, but had no path to close them. After a cycle's execution completed there was no mechanism to request an evaluation from the agent's machine, classify the outcome, update best-attempt records and counters, or advance the campaign's state machine (target_reached, budget_exhausted, plateau, block). Campaign attempts accumulated without ever reaching a terminal state.

## Task

Implement the full evaluation → judge → close pipeline for campaign cycles (issue #930, §3.2–§3.3 phases 3–4):

- Request an evaluation from the machine bus after a completed execution.
- Classify the result via a pure judge function using metric direction and min_delta.
- Drive the campaign state table on each verdict (best attempt, failure/plateau counters, block / target_reached / budget_exhausted / plateau, optimize-mode strategy change).
- Handle evaluation deadline expiry server-side.
- Close cycles triggered from outside (stopped campaign, room archived, stuck-task sweeper).
- Ensure the close invariant: every close sets `next_cycle_at`; a still-active execution is cancelled through the execution stop protocol; the cycle Task ends failed.
- Add `extend_campaign` to the service (reopens deadline_reached / budget_exhausted campaigns).

## Action

**New files:**

- `packages/cluster/anygarden/campaigns/lifecycle.py` — all hooks that run inside the caller's transaction:
  - `on_execution_terminal`: syncs tokens, requests eval on `execution_completed` or closes with `unknown`/`skipped` on a cancelled/stopped run; handles the race where the attempt was already closed.
  - `on_eval_result`: validates the result frame, calls `judge.classify`, updates `dev_score`/`metrics`/`eval_excerpt`, closes the attempt, then calls `_after_close`.
  - `_after_close`: CAS loop over the campaign — updates `tokens_used`, `best_attempt_id`, `consecutive_eval_failures`, `cycles_since_improvement`, `evaluator_pins`, `wake_reason`, and emits the appropriate state event; records `cycle_closed` and `strategy_change` history events.
  - `expire_eval_deadlines`: closes attempts past `eval_deadline_at` as `eval_failed/timed_out`.
  - `on_cycle_task_closed`: closes a cycle whose Task ended before evaluation as `unknown`.
  - `stop_campaign` / `stop_room_campaigns`: stop an open campaign (and any open cycle) in one transaction.
  - `reconcile_open_cycles`: scheduler tick safety net — stops campaigns in archived rooms, replays missed terminal events, closes orphaned attempts.
  - `cancel_cycle_execution`: issues a system `ExecutionMutation` cancel (same protocol as user cancel and usage limits) via `fence_execution_turns` + `settle_execution_mutation`.

- `packages/cluster/anygarden/campaigns/evaluation.py` — send/receive layer for machine evaluations:
  - `queue_dispatch_after_commit` / `install_dispatcher`: SQLAlchemy `after_commit` listener fires a background task per queued attempt.
  - `dispatch_eval`: opens a session, checks the attempt is still pending, builds the frame, calls `machine_bus.request_campaign_eval`, and on acceptance records `eval_status=running` + `eval_deadline_at` in a separate write transaction.
  - `request_pending_evals`: re-sends all pending attempts to online machines (called from the tick and on reconnect).
  - `request_pending_evals_soon`: fires `request_pending_evals` in the background on machine register.
  - `handle_eval_result_frame`: validates a `CampaignEvalResultFrame`, opens a write transaction and calls `on_eval_result`.

- `packages/cluster/anygarden/campaigns/judge.py` — pure classification (no DB):
  - `classify(metric, score_key, status, parsed, best_score)` → `Verdict` named-tuple with `outcome`, `eval_status`, `dev_score`, `valid`, `improved`, `eval_failed`, `pin_mismatch`.
  - `reaches_target(score, metric)` — compares against `metric.target` respecting direction.
  - Handles `maximize`/`minimize` directions, `min_delta` threshold, `invalid`/`error` statuses.

**Modified files:**

- `packages/cluster/anygarden/campaigns/scheduler.py`: integrates `expire_eval_deadlines`, `reconcile_open_cycles`, `request_pending_evals` into each tick; calls `install_dispatcher` / `uninstall_dispatcher` at lifespan.
- `packages/cluster/anygarden/campaigns/service.py`: adds `extend_campaign` (reopens stopped/deadline_reached/budget_exhausted campaigns by extending deadline/budget and applying a `resume` transition).
- `packages/cluster/anygarden/scheduler/machine_bus.py`: adds `request_campaign_eval` and `is_connected` to the `MachineBus`; registers `handle_eval_result_frame` for `campaign_eval_result` frames.
- `packages/cluster/anygarden/ws/machine_handler.py`: routes incoming `campaign_eval_result` frames to `evaluation.handle_eval_result_frame`.
- `packages/cluster/anygarden/app.py`: wires `install_dispatcher` into app lifespan.

**Tests:**

- `test_campaigns_judge.py` — unit tests for `classify` across all verdict paths.
- `test_campaigns_state.py` — updated to cover `_after_close` state transitions, best-attempt tracking, counter resets, strategy-change events.
- `test_campaigns_scheduler.py` — updated to cover tick integration of `expire_eval_deadlines` and `reconcile_open_cycles`.

## Decisions

- **After-commit dispatch over in-transaction send**: evaluation requests are queued inside the transaction and dispatched after commit via a SQLAlchemy `after_commit` listener. This avoids sending a machine request that may never commit, and keeps the lifecycle hooks synchronous and transaction-safe. The tick's `request_pending_evals` handles the re-send case (machine offline, timeout, reconnect) without coupling lifecycle hooks to the async machine bus.

- **Ack-only protocol, result matched by job_id**: the machine bus exchange waits only for the `accepted` acknowledgement. The result frame carries `job_id` (= attempt id), so it is matched after server restarts and during tick-driven re-sends without any additional state. Alternative (streaming result in the same request) was rejected as it would couple the evaluation timeout to the HTTP connection lifetime.

- **Pure judge function with no DB access**: `judge.classify` takes only scalars (metric spec, status, parsed dict, best score) and returns a `Verdict`. This makes the classification trivially testable and keeps the DB update logic in `lifecycle._after_close` where the CAS retry loop lives.

- **Close invariant via execution stop protocol**: when a cycle must close while its execution is still active, the code issues a system `ExecutionMutation` cancel — the same path a user cancel and a usage-limit stop use — rather than writing `execution.status` directly. This keeps execution state transitions in one place and ensures the execution's terminal event eventually fires `on_execution_terminal`, which then fails the cycle Task. Direct status writes were rejected as they bypass the mutation fence and stop-receipt logic.

- **`extend_campaign` reopens terminal campaigns**: extending a campaign past what ended it (deadline or budget) applies a `resume` event and resets `next_cycle_at`. The alternative (creating a new campaign) was rejected because it would lose cycle history and best-attempt tracking.

- **`cycles_since_improvement` reset strategy for optimize mode**: in optimize mode, a plateau does not stop the campaign; instead `cycles_since_improvement` is reset to 0, a `strategy_change` wake_reason is set, and an event is recorded. This allows the brief renderer to ask for a fresh approach. How that brief changes behavior is left to the brief module (unclear from available context exactly which brief fields react to `STRATEGY_CHANGE_REASON`).

## Result

Campaign cycles can now complete the full loop: execution finishes → evaluation dispatched to machine → result classified → campaign state advanced. Cycles that fail to evaluate within the server-side deadline are expired automatically. Stopped campaigns, archived rooms and stuck tasks all close open cycles with appropriate outcomes. The `extend_campaign` service entry point allows paused/exhausted campaigns to be reopened.

Tests cover the judge unit logic, the state-update paths in `_after_close`, and the scheduler tick integration. The `test_campaigns_cycles.py` file (end-to-end cycle tests) and the worklog for the preceding run-in-workspace phase remain uncommitted.
