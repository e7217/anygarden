# refactor(turns): route terminal turn transitions through mark_turn_terminal (#762)

- Commit: `8079ab9` (8079ab9ca432431cc698b75651b9ed10f73a9152)
- Author: Changyong Um
- Date: 2026-09-30T16:56:20+09:00
- PR: #762

## Situation

#762 changes `ask_peer` to an asynchronous fan-in: the server wakes the caller once every peer's turn has ended. That needs one reliable signal for "this turn is over". Today the terminal transition of an `AgentTurn` (completed / cancelled / failed) is written by hand in 7 places spread over different sessions and transactions (`turns/service.py` ×6 functions, `workspaces/service.py` ×1). A new path could easily skip a hook added at only some of them.

## Task

- PR1 of the plan (`.tmp/plan-762-async-peer-fanin.md` §4): a behavior-preserving refactor only.
- Add `mark_turn_terminal(...)` and route every terminal transition through it. Leave a clearly named no-op extension point (`_on_turn_terminal`) for PR2.
- Keep every field write the same: timestamps, reasons, attempt state/outcome/reason, outbox rows. The helper must stay inside the caller's transaction and never commit.
- TDD: a spy test must show the hook fires exactly once per terminal transition and never for open transitions (lease, retrying, lease expiry, completing).

## Action

- `packages/cluster/anygarden/turns/service.py`
  - Added `TERMINAL_TURN_STATES`, `TerminalTurnState`, `async _on_turn_terminal(db, turn)` (no-op) and `async mark_turn_terminal(db, turn, *, state, reason, at=None, attempt=None, attempt_state=None, attempt_outcome=None, attempt_reason=None)`. It rejects non-terminal states. `at` stamps `turn.completed_at` and `attempt.ended_at` only when given. Attempt fields change only when an attempt is passed and the values are non-`None`.
  - `create_turn`: the row is now always built `pending`. If it is born cancelled (`agent_not_running` or a workspace denial), it is closed through the helper right after `db.add(turn)`. `completed_at` stays unset, as before.
  - `deliver_pending_outbox`: 3 cancels (workspace gate, `authorization_revoked`, `trigger_message_deleted`). No `at`, so `completed_at` and `ended_at` stay unset, as before.
  - `finish_completion`: completed/ok. `accepted_message_id` and `updated_at` stay at the call site, and the existing `terminal_reason` is passed through unchanged.
  - `record_lifecycle`: `agent_cancelled` and `agent_skipped`. The "already completed" branch and the "failed without completion → expire lease" branch are not transitions and are untouched.
  - `recover_stalled_turns`: cancelled, `legacy_interrupted`, `retry_exhausted` (turn only, because the attempt was already fenced by the bulk UPDATE).
  - `cancel_invalid_turns`: the attempt is now fetched first. An attempt that is already completed or cancelled is not passed, so it keeps its fields as before.
- `packages/cluster/anygarden/workspaces/service.py` `cancel_attachment_turns`: same pattern. Lazy import of the helper, matching the existing lazy `turns ↔ workspaces` imports.
- `packages/cluster/tests/test_turn_terminal_hook.py` (new, 17 tests): monkeypatches `_on_turn_terminal` with a spy and drives each real path. Fixtures are reused from `test_durable_turns.py` and `test_workspace_attachments.py`. Negative cases: normal create/deliver, `begin_completion` (completing), `handler_started`, failed-without-completion, first lease expiry → retrying, a trailing `handler_finished` on a completed turn, and idempotent `create_turn` replay.

## Decisions

- Options (plan §3.2 decision 4): (A) add the fan-in call at each of the 7 sites; (B) have the recovery loop poll open groups every second; (C) route through one `mark_turn_terminal()` that does DB work only, with waking/broadcasting left to the worker. **C was chosen.** It lets this pure refactor ship as its own PR, and new terminal paths inherit the hook automatically.
- The hook is `async` and takes `(db, turn)`, because PR2's `on_turn_terminal` must query and update group rows in the same transaction.
- Bulk UPDATEs: none of them move an `AgentTurn` to a terminal state. The only `update(AgentTurn)` is the completion CAS to `completing`, which is still open. The bulk `update(AgentTurnAttempt)` (fence to `interrupted`) and `update(AgentTurnOutbox)` statements act on other tables, so no hook-friendly bulk variant was needed.
- Not every write moved into the helper: `accepted_message_id` and `updated_at` (finish_completion), outbox rows, activity logs and audits stay at the call sites. The helper owns only the terminal transition itself.
- Revisit if: a future path closes turns with a bulk `UPDATE agent_turns SET state=...`, which would bypass the hook, or if the hook must run after commit (it currently runs inside the caller's transaction).

## Result

- No behavior change. The existing durable-turn, lifecycle, recovery and workspace tests pass unchanged: `test_durable_turns.py test_ws_handler_lifecycle.py test_execution_worker_recovery.py test_workspace_attachments.py` together with the new file gave 58 passed. The full cluster suite (`pytest -n logical`) gave 2665 passed, 2 skipped.
- The new test file passes ruff. The touched source files add no new ruff findings: the remaining UP017/UP035 findings were already there.
- Pending: PR2 (async fan-in) fills `_on_turn_terminal`.
