# fix(pi): resume the saved native session with --session (#714)

- Commit: `7ba40bc` (7ba40bc301db827c0516a4524ca13bb66f0cb720)
- Author: Changyong Um
- Date: 2026-09-28T10:51:48+09:00
- PR: #714

## Situation

A `pi-cli` agent answered the first message in a conversation but not the second. `PiRuntime.command()` passed the stored native session id as `--resume <id>`. In the pinned Pi 0.85.1, `--resume` is a bare flag that opens the interactive session selector. In `--print --mode json` the process exited without `agent_settled`, the manager recorded `unknown / missing_terminal_event`, and the unknown receipt fenced the scope. Later turns then failed with `POLICY_DENIED` while the agent still showed Online.

## Task

- Resume with Pi's `--session <path|id>` option.
- Add a regression assertion for the resumed argv and verify two consecutive turns on the pinned Pi version.
- Keep the unknown-receipt safety fence and document how an operator recovers a scope that is already fenced.

## Action

- `packages/agent/anygarden_agent/runtime/execution/pi.py` (`PiRuntime.command`): `cmd += ["--session", session]`. The docstring now says why `--resume` is wrong.
- `packages/agent/tests/test_execution/test_pi_runtime.py` (`test_second_execution_resumes_native_session`): asserts that `--session` carries the handle and that `--resume` is absent.
- `docs/runbook/room-execution-upgrade.md`: new note on the flag, plus a "Recovering a scope blocked by an `unknown` receipt" section. To recover, stop and then start the agent. This advances the launch generation, which `ExecutionLaunch.bind` folds into the scope's policy epoch, so the next turn opens a new scope and a new native session. The history of the blocked session is not carried over, and the uncertain turn is not replayed.

## Decisions

- **`--session <id>` vs `--session-id <id>`**: `--session-id` creates the session if it is missing. That would silently start a fresh conversation when the handle is stale. `--session` resolves an existing file or partial UUID, which matches what "resume" means.
- **The fence is not relaxed**: we could have auto-cleared `unknown` receipts that carry `missing_terminal_event`. This was rejected because `unknown` means the runtime cannot prove whether side effects happened. The existing contract never retries an uncertain turn automatically, so recovery stays an explicit operator step (stop/start = new generation).
- **Receipt DB deletion is discouraged**: deleting it would destroy the evidence and the handles of the other scopes.
- Revisit if the Pi pin moves past 0.85.x and the CLI flag semantics change.

## Result

- Agent package tests: 653 passed.
- Live check with the real `@earendil-works/pi-coding-agent@0.85.1` against a loopback chat-completions stub, run through `PiRuntime.run`: two consecutive turns both `succeeded`. The second request carried 4 messages, so the prior history was resumed. The same harness with `--resume` reproduced `unknown / missing_terminal_event` on turn 2.
- A real-provider live DM check is not part of this commit: no provider credentials were available in this environment.
