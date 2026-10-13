# fix(agent): resend instructions when a turn starts a fresh engine session (#805)

- Commit: `289af1a` (289af1a201fe6f1dc7ca1131c3810fb1f52c93d3)
- Author: Changyong Um
- Date: 2026-10-07T17:25:11+09:00
- PR: #805

## Situation

On 2026-10-07 a PM agent (Codex) failed its first turn in a room (Codex 0.158.0 did not support the new default model). The next turn ran in a fresh Codex session whose input was just the user line: no system prompt, memory or roster. The agent did not know it was in a chat room or that `ask_peer` existed, so it answered "via agents" with Codex's built-in sub-agents instead of the room's peers. `ShaTrackedInjector` (#293) recorded the blocks as sent when the prompt was assembled, while the execution store saves an engine session handle only when `outcome == "succeeded"`. A failed first turn therefore left "sent" records and no session to resume.

## Task

- After a failed, cancelled or timed-out first turn, the next turn in the same scope must carry system prompt, memory and roster.
- Where a resumable session handle exists, keep sending only changed blocks.
- Cover both Codex and Pi (both go through `RoomExecutionAdapter`), checking the actual engine input.

## Action

- `integrations/base.py`: `ShaTrackedInjector.forget(key)` drops the system/memory/roster sha records for a scope.
- `integrations/codex_cli.py`: `on_message` calls `forget` before `apply` when `_has_resumable_session(msg)` is false; the base implementation checks `room_session_key` in `_room_thread_ids`.
- `integrations/room_execution.py`: the `SessionScope` construction moved out of `_call_codex` into `_session_scope(msg, room_id, workspace)`; `_has_resumable_session` binds that scope and asks the execution manager.
- `runtime/execution/launch.py`: `ExecutionLaunch.bind_scope(scope)` computes the bound policy epoch; `bind` uses it, so the lookup key equals the key the invocation runs under.
- `runtime/execution/manager.py`: `LocalExecutionManager.has_session(scope)`.
- Tests: `tests/test_room_execution.py` gets a `fail-once` mode in the fake CLI (Codex emits `thread.started` + `turn.failed` and exits 1, as the real failure did) and a parametrized Codex/Pi test asserting turns fail→fresh→resume carry the system prompt as [yes, yes, no]. `tests/test_integrations/test_codex_system_prompt.py`: the stubbed `_call_codex` now leaves a resume handle like the real one, plus a failed-then-fresh case for the plain adapter.

## Decisions

- Options from the issue: (a) record shas only after success (two-phase); (b) resend every block every turn; (c) decide by whether a resumable session handle actually exists.
- Chose (c). (a) still diverges when a succeeded turn returns no handle or a handle later disappears (store loss, scope change), because it keys on the run outcome, not on session state. (b) would re-paste the same blocks into accumulating sessions every turn, which #293 exists to prevent.
- The lookup goes through the same `bind_scope` the invocation uses rather than re-deriving the key, so a future change to epoch binding cannot make the check and the run disagree.
- A Codex failure without `turn.failed` is classified `unknown` and blocks the scope (no new turn runs), so that path cannot hit this bug; the test models the definite-failure path that occurred live.
- Assumption to revisit: the store's `sessions` row is the only resume source for room execution. If engines start resuming from another source, `has_session` must consult it.

## Result

- A fresh engine session always receives system prompt, memory and roster; resumed sessions still get deltas only.
- New tests fail without the fix for both Codex and Pi; agent suite 672 passed (729 after rebase on b9159e4, where #816 reclassified codex failures; the fail-once fixture still yields a definite `failed`).
- Out of scope (noted in the issue): disabling Codex built-in sub-agents for room agents, and surfacing the real first-failure cause in the room.
