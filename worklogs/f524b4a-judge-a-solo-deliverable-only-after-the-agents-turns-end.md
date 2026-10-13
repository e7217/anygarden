# fix(bench): judge a solo deliverable only after the agent's turns end

- Commit: `f524b4a` (f524b4aedaee0e2b5dcd4a386e3e811dc034dea2)
- Author: Changyong Um
- Date: 2026-10-13T18:48:18+09:00
- PR: —

## Situation

The benchmark runner's `_deliverable_text` function for solo-mode scenarios checked whether a task's `status == "done"` to decide it was ready to judge. However, the task status transitions to `done` a moment before the agent posts its final answer, creating a race: the judger could fire while the agent's reply was still being written. In DL-03 solo run 1 this caused every expected fact to appear missing, producing a false failure.

## Task

- Fix the solo-deliverable readiness check so it waits until the agent's turns are in a terminal state before collecting the reply text.
- Add a regression test case that covers the "task done but agent still writing" case.
- Re-run the core suite (DL-01, DL-02, DL-03, ×3 each, solo mode) and record the results to confirm all 9 runs pass.

## Action

- `benchmarks/runner/scenarios.py` (`_deliverable_text`): after confirming `task["status"] == "done"`, fetch the agent's turns via `agent_turns()` and return `(False, "")` if any turn is not in `TERMINAL_TURN_STATES`. Only then collect `replies` from `agent_replies()` and return the text. Added a brief comment explaining the race.
- `packages/cluster/tests/test_benchmark_runner.py`: added a parametrized case `(tasks=[done task], turn_state="leased", expected=False)` to `test_solo_deliverable_accepts_a_direct_answer_or_a_done_task`, covering the new guard.
- `benchmarks/results/20261013-*.json` (five files): recorded core-suite evidence runs, including the passing run on b82873c that preceded this fix and the final passing run on f524b4a.

## Decisions

- **Wait on turn-terminal-state vs. retry with delay**: A fixed `asyncio.sleep` would work but introduces arbitrary latency and can still miss a slow write. Checking the actual turn state is a precise, zero-waste condition.
- **Where to insert the check**: the check belongs in `_deliverable_text` right after the `status == "done"` guard, keeping the guard logic co-located and not changing the caller's contract.
- **Rejected — changing task-status semantics**: Redefining `done` to mean "turns complete" would require changes across the cluster and agent; the local fix is narrower and safer for now.
- **Assumption**: `TERMINAL_TURN_STATES` correctly enumerates all states where the agent will not write any more content. If a new non-terminal state is ever added, this guard will need updating.

## Result

All 9 core-suite solo scenarios (DL-01 ×3, DL-02 ×3, DL-03 ×3) pass with exit 0. The race that caused DL-03 solo run 1 to fail is closed. A regression test guards the new behaviour.
