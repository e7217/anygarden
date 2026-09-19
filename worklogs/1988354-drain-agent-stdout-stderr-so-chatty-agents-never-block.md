# fix(machine): drain agent stdout/stderr so chatty agents never block

- Commit: `1988354` (19883544b6f0615482e04c26a7252cf195fdfbf8)
- Author: Changyong Um
- Date: 2026-09-19T22:21:07+09:00
- PR: —

## Situation

`Spawner` launches every agent with `stdout=PIPE, stderr=PIPE` (`packages/machine/anygarden_machine/spawner.py:1213`), and nothing read those pipes while the agent ran. The only reader was `watch_process`, which read the first 2KB of stderr after exit. During a live agent-collaboration test on 2026-09-19 (integrated node), agent logs turned out to be unobservable. An isolated reproduction then showed that a child writing to an undrained pipe blocks after ~100–200KB. On top of that, CPython 3.12 resolves `Process.wait()` only in `_call_connection_lost`, i.e. after every pipe reaches EOF. A paused, unread pipe therefore also hangs the watcher, even after the child has been killed.

## Task

- Keep agents from ever blocking on log output, however long they run.
- Make sure `watch_process` always observes the exit (normal, crash, or kill) so stopped/crashed state reaches the cluster.
- Preserve the `on_crashed(agent_id, exit_code, stderr_tail)` contract. `stderr_tail` also feeds quota classification (`packages/cluster/anygarden/agent_availability.py`, e.g. "429").

## Action

- `packages/machine/anygarden_machine/supervisor.py`:
  - New `_drain()`, which reads a pipe in 64KiB chunks until EOF and optionally keeps a rolling buffer of the last `STDERR_TAIL_MAX` bytes.
  - `watch_process` starts one drain task for stdout (discarded) and one for stderr (tail kept) before awaiting `proc.wait()`.
  - After exit it waits up to 5s for the drains to consume what is still buffered, and cancels them in `finally`.
  - The crash tail is now the decoded rolling buffer instead of a post-exit `read(2048)`.
- `packages/machine/tests/test_supervisor.py` (new), using real child processes with piped stdio like the spawner:
  - a child writing 2MB to stdout exits and is reported as stopped;
  - a crash after 2MB of stderr noise reports a tail that ends with the final error line and stays ≤ `STDERR_TAIL_MAX` bytes;
  - a short stderr is reported whole;
  - a child that floods stdout and is then killed is still reported as crashed with exit code -9.

## Decisions

- Options weighed:
  - Drain inside `watch_process` (chosen).
  - Spawn with `stdout=DEVNULL`.
  - Redirect agent output to a per-agent log file.
- `watch_process` already owns the child's lifetime and the stderr tail, so draining there fixes both the write block and the `wait()` hang in one place and covers every spawn path that uses it. Adopted processes (`_poll_watch`, `proc=None`) are unaffected.
- `DEVNULL` for stdout was rejected to keep the change inside the watcher and leave the spawn call untouched. A per-agent log file (useful for debugging, since agent logs are currently invisible) was left as a follow-up to keep this fix minimal.
- The retained stderr is now the *last* 2KB rather than the first. For a crash this is where the traceback or quota error lands, which is what `classify_quota_error` and the admin-facing detail need.
- Assumption: agent grandchildren do not keep the agent's stdio open after the agent exits. If they do, `wait()` still waits for them. That is asyncio's pre-existing semantics, and `terminate_tree` kills the process group.

## Result

- New tests: before the fix, 3 of 4 failed with timeouts (the short-stderr case passed); after the fix, 4 passed in 0.6s.
- Regression: machine 519 passed / 2 skipped; cluster 1770 passed / 1 deselected.
- Pending: not yet verified against a long-running live agent. Agent stdout is still discarded; persisting it to a per-agent log file is a possible follow-up.
