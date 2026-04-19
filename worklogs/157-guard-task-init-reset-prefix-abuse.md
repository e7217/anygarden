# feat(agent): guard against task-init reset-prefix abuse (#157 Phase A)

- Commit: `eafab15`
- Author: Changyong Um
- Date: 2026-04-19
- PR: #160
- Issue: #157 (umbrella)

## Situation

Issue #67 made `[ROOM_QUERY]` / `[DELEGATED]` prefixes zero the agent-only turn counter so consecutive task rounds don't get dropped by `max_agent_turns=6`. The side effect: any agent that keeps emitting task-init prefixes bypasses the turn limit indefinitely. The 2026-04-19 deep-research report on agent-to-agent conversation cites the $47K production loop case (agent A ↔ B ping-pong for 11 days undetected) and Cemri et al. (arXiv:2503.13657) classifies it under FC2 "Inter-Agent Misalignment" (FM-2.1 Conversation Reset). Before this change, the only hard cap was `max_agent_turns`, which a prefix loop silently neutralised.

## Task

- Add a per-room consecutive task-init counter that tracks how many task-init resets have fired without a human message in between.
- After `max_task_init_resets` (default 5), stop honouring the reset so `max_agent_turns` takes over.
- Mirror the guard across all three `_process_frame` paths that honour task-init (hard self-filter, nonce echo, normal agent path).
- Reset the streak when a non-self, non-nonce message (i.e. a human) breaks the chain so legitimate multi-task conversations aren't penalised.
- Land the change as Phase A of #157 (smallest, most independent) so the follow-up R1 cycle detection and R2 telemetry PRs can ship separately.

## Action

- `packages/agent/doorae_agent/client.py`
  - `__init__`: added `_consecutive_task_init: dict[str, int] = {}` and `max_task_init_resets: int = 5` next to the existing `_agent_turn_count` / `max_agent_turns` block.
  - Added `_consume_task_init_reset(room_id) -> bool` helper that bumps the counter, logs `task_init.reset_guard_fired` at WARN once over the limit, and returns False so callers skip the reset.
  - Rewrote the three task-init reset sites inside `_process_frame` (hard self-filter, nonce-echo soft filter, normal path) so each now calls the helper before zeroing `_agent_turn_count`.
  - Human path (`else` branch after the `elif sender_has_nonce` increment) now also zeros `_consecutive_task_init[room_id]` so the streak is broken whenever a real user speaks.
- `packages/agent/tests/test_client.py`
  - New `TestTaskInitResetGuard` class with 7 tests: default values, 5-reset normal flow, 6th-reset guard, human reset, per-room isolation, nonce-echo path, foreign-agent path.
  - Tests drive `_process_frame` directly (matching the existing `TestAgentTurnCounter` style) and assert both `_agent_turn_count` and `_consecutive_task_init` state transitions.

## Result

- 7 new tests pass; pre-existing 155 agent tests pass (`uv run pytest packages/agent/` reports **162 passed**). The only failure is `test_openai::test_integrate_registers_handler`, a pre-existing flake from `OPENAI_API_KEY` not being set in the local sandbox; unaffected by this change.
- `max_agent_turns=6` is now the effective ceiling: once 5 consecutive task-init resets have been consumed, the 6th reset no-ops and the next agent-only message increments the turn counter normally, triggering the existing `ws.agent_turn_limit` skip.
- The guard fires with a structured `task_init.reset_guard_fired` log (room_id, consecutive, limit) so operators can correlate it with R2 telemetry (Phase C) once that lands.
- Phase A shipped as a standalone PR; R1 (cycle detection in `decide_policy`) and R2 (per-agent token telemetry) are independent follow-ups on the same umbrella issue #157.
