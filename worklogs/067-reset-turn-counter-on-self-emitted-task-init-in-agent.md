# fix(agent): reset turn counter on self-emitted [ROOM_QUERY]/[DELEGATED] in agent-only rooms (#67)

- Commit: `7cf4c75` (7cf4c754f309d538e95f769cec1ed2cb1d858eb9)
- Author: Changyong Um
- Date: 2026-04-16T14:59:27+09:00
- PR: #67

## Situation

`ChatClient._process_frame` tracked a per-room `_agent_turn_count` to bound agent-to-agent loops at `max_agent_turns = 6`. The hard filter (self-participant) and soft filter (nonce-echo) branches unconditionally `+1`-ed the counter and early-returned before the main-path reset logic could run. In human-less "agent-only" rooms, the representative agent's own `[ROOM_QUERY]` / `[DELEGATED]` emissions counted as turns even though they were task boundaries — so after 6 self-fanouts the counter was saturated and subsequent legitimate agent replies got dropped.

## Task

- Treat self-emitted `[ROOM_QUERY]` / `[DELEGATED]` as task boundaries in both filter paths, resetting the counter to 0 instead of incrementing.
- Keep the infinite-loop guard: regular self/nonce messages still count toward the limit.
- Avoid prefix-check duplication across the three branches.
- Preserve existing early-return semantics (self/nonce frames are NOT dispatched to handlers).

## Action

- `packages/agent/doorae_agent/client.py`
  - Added module-level helper `_is_task_init_content(content)` (client.py:24-43) with docstring explaining the `[ROOM_QUERY]` / `[DELEGATED]` task-boundary semantics and the #67 rationale.
  - Hard filter branch (client.py:265-281): branch on `_is_task_init_content(content)` — reset to 0 on task init, `+1` otherwise, then early return.
  - Soft filter branch (client.py:283-299): same task-init vs. increment logic, preserving nonce consumption via `_sent_nonces.discard`.
  - Main path (client.py:309): replaced the inline `content.startswith("[DELEGATED]") or content.startswith("[ROOM_QUERY]")` with the helper call (behaviour unchanged, single source of truth).
- `packages/agent/tests/test_client.py`
  - New `TestIsTaskInitContent` (5 cases): prefix matches, empty string, non-start occurrence.
  - New `TestAgentTurnCounter` (10 cases): self + regular → `+1`; self + `[ROOM_QUERY]` / `[DELEGATED]` → reset; nonce-echo + regular → `+1`; nonce-echo + `[ROOM_QUERY]` → reset; other-agent regular → `+1` and handler called; other-agent exceeds `max_agent_turns` → dropped; human message → reset; other-agent `[ROOM_QUERY]` → reset (main-path regression); 8-frame agent-only fanout regression asserting all four peer replies reach the handler at `max_agent_turns=3`.

## Result

- 26 / 26 tests pass in `packages/agent/tests/test_client.py` (15 new).
- Agent package: 131 pass, 1 pre-existing failure (`test_openai.py` requires `OPENAI_API_KEY` — unrelated).
- Cluster package: 366 pass, 0 regressions.
- Agent-only rooms with consecutive `[ROOM_QUERY]` rounds no longer hit the `ws.agent_turn_limit` drop path on legitimate task fanout; the `max_agent_turns` bound still protects against task-boundary-free agent-to-agent loops.
- No server, protocol, or frontend changes. Integrations (`claude_code`, `gemini_cli`, `codex`, `openai`) inherit the fix via `ChatClient`.
