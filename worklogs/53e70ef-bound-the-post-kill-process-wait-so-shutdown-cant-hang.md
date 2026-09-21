# fix(machine): bound the post-kill process wait so shutdown can't hang

- Commit: `53e70ef`
- Author: Changyong Um
- Date: 2026-09-21
- PR: —

## Situation

`Spawner.kill` terminates an agent's process group through `terminate_tree` and then awaits `proc.wait()` with no timeout. The comment there asserted the wait "returns immediately" because the process is already dead. That is not true for an asyncio subprocess: `Process.wait()` resolves only in `_call_connection_lost`, i.e. once every pipe has reached EOF. Anything still holding the agent's inherited stdio leaves the wait pending indefinitely.

On 2026-09-21, while restarting the local node to pick up #642, `anygarden stop` stalled. The log shows `drain` terminating agents one at a time: pm terminated, 테스트에이전트02 terminated, `agent_terminate_tree` logged for qa_agent — and then nothing. The stop command timed out, three agents were left running, the node ignored SIGTERM, and it had to be SIGKILLed. Recovery then required moving the leftover `agents/<id>/runtime.json` aside and repairing `node-owner.json` by hand before the node would start again.

## Task

- Make a stuck `wait()` cost one agent's cleanup, not the entire node shutdown.
- Keep the cleanup guarantee: a stop must still refuse to report success if any agent process survived.

## Action

- `packages/machine/anygarden_machine/spawner.py`: new `PROC_WAIT_TIMEOUT = 10`; `kill` wraps `proc.wait()` in `asyncio.wait_for` and, on timeout, logs `agent_wait_timeout` (agent_id, pid, timeout) and proceeds to `_cleanup`. The stale comment is replaced with the actual EOF semantics and the reason the bound is safe.
- `packages/machine/tests/test_spawner.py`: `TestKillWithLingeringPipes::test_kill_returns_when_the_process_wait_never_resolves` — a proc whose `wait()` awaits an `Event` that is never set; `kill` must still return success within 20s and drop the agent from `_agents`.

## Decisions

- Bounding the wait, rather than removing it, keeps the normal path unchanged: when EOF does arrive the state machine is drained exactly as before.
- 10s matches the existing `KILL_TIMEOUT` order of magnitude. `terminate_tree` has already returned by then, so the group is reaped whether or not the pipes closed.
- Cleanup verification is deliberately left where it already lives: `close_local_execution` checks `is_group_alive` for every agent pid and raises if anything survived, so the timeout cannot turn a dirty stop into a clean-looking one.
- The test drives a never-resolving `wait()` directly instead of reproducing the live pipe holder. A real-process attempt (child sleeping, grandchild in a new session holding stdout) did not reproduce: `terminate_tree` reaps descendants via psutil regardless of session, so the pipe closed and the wait returned. The mechanism under test is our timeout, and the test fails without it.
- What actually held the pipe in the live incident is still unidentified. Worth revisiting if `agent_wait_timeout` shows up in the node log.

## Result

- `cd packages/machine && uv run pytest`: 524 passed, 2 skipped. The new test times out (RED) without the fix.
- `cd packages/cluster && uv run pytest`: 1773 passed, 1 deselected.
- Not exercised against a live shutdown yet; the next `anygarden stop` on the node will.
