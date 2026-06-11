# feat(observability): gateway-free LLM turn I/O capture on engine_call span (#433)

- Commit: `ad4d01a` (ad4d01a)
- Author: Changyong Um
- Date: 2026-06-12
- PR: #433 (issue)

## Situation

After the instrumentation roadmap (#420–#431), the OTEL trace tree (chat.request→agent.handler→agent.engine_call→llm.generation) had an empty `llm.generation` span: it is filled only by the LLM reverse-proxy ("gateway"), which is OFF and only partially wired (codex via config.toml, claude-code via ANTHROPIC_BASE_URL, gemini-cli has no base-URL mechanism at all). So Langfuse showed turn structure/timing/causality but never "what the agent sent the model and what it got back". A workflow analysis (wamx3lr3a) established that the agent's `run_engine` boundary is a single in-process common layer where every engine's turn input + output are both visible — gateway-independent and uniform across all 4 engines.

## Task

- Capture per-turn input (the augmented prompt the adapter hands the engine) + output (engine reply) at `run_engine`, with no proxy.
- Stamp them onto the existing `agent.engine_call` span as `gen_ai.prompt`/`gen_ai.completion`, gated by the existing capture toggle + truncation.
- Do NOT change `on_message`'s `str|None` contract (96 test call sites assert it).
- Keep it trace-only — message text must not leak into ActivityLog/DB.

## Action

- `packages/agent/anygarden_agent/integrations/base.py`: `EngineAdapter` gained `_record_turn_input(room_id,text)` / `_take_turn_input(room_id)` — a lazy per-room single-slot stash (`_turn_input_buf`, pop on take).
- The 4 adapters (`codex.py`, `claude_code.py`, `gemini_cli.py`, `openhands_engine.py`): each records the augmented input inside `on_message` right after building it (codex `turn_content`, claude/openhands `prompt`, gemini `_build_prompt`), keeps `on_message` returning `str|None`, and its `run_engine` closure now returns `EngineTurn(response, _take_turn_input(room_id))`. Each `run_engine` `finally` drains the stash unconditionally.
- `packages/agent/anygarden_agent/runtime/handler_wrapper.py`: new `EngineTurn` dataclass + `_normalize_engine_result` (accepts `str` OR `EngineTurn`); `_run` sets `io_capture = isinstance(raw, EngineTurn)` and emits `prompt`/`completion` on the `engine_call_finished` frame only when `io_capture`.
- `packages/agent/anygarden_agent/protocol/frames.py` + `packages/cluster/anygarden/ws/protocol.py`: both `LifecycleFrame` definitions gained optional `prompt`/`completion`. `client.sendLifecycle` already forwards via `**details` (no change).
- `packages/cluster/anygarden/ws/handler.py`: `_apply_lifecycle_to_trace` passes `frame.prompt`/`frame.completion` to `tracing.finish_engine_call`.
- `packages/cluster/anygarden/observability/tracing.py`: `finish_engine_call` gained `prompt`/`completion`; stamps `gen_ai.prompt`/`gen_ai.completion` on the open engine span before `_end`, gated by `self._capture_content`, truncated by new `_clip_text` (str sibling of `_clip`).
- Tests: protocol round-trip (incl. cross-package wire), supervisor EngineTurn/backward-compat/empty-response, base stash helper, codex on_message stash + two end-to-end integration tests (success + failed-drain), cluster tracing stamp/toggle/truncate + ActivityLog non-leak regression.

## Decisions

- **Contract change on `run_engine` (str→EngineTurn), NOT `on_message`.** Considered (A) changing `on_message`'s return — rejected: 96 test call sites assert it (the exact regression risk #429 deferred). (C) capturing raw `msg['content']` in `run_engine` — rejected: ≈ `Message.content` we already have, misses augmentation. (B, chosen) adapter stashes the augmented input, `run_engine` reads it — `run_engine` is an untested inner closure, so the blast radius is the 4 closures + supervisor only.
- **Turn I/O is symmetric opt-in via `EngineTurn`.** A bare `str` return emits neither field (predictable single toggle) rather than half-capturing output for un-migrated adapters.
- **Stash drained in `run_engine` `finally`** (added after adversarial review). The review found the stash was recorded before the engine call but taken in `run_engine`'s `try`, so a raising turn (timeout/fail/cancel) never drained it → unbounded per-room memory leak + a later early-return turn could pop a stale prompt onto the wrong span. The unconditional `finally` drain (idempotent on the ok path) fixes both. Assumption: the supervisor serializes turns per room, so the record→take pair never races a concurrent same-room turn — revisit if per-room serialization changes.
- **Fidelity is T2, not T3.** This layer sees the turn's user-facing input + the engine's output, not the full model-API context (system prompt + context window + tool schemas), which the CLI/SDK assembles internally — that stays gateway/CLI-log only. Privacy: prompt/completion travel the same internal agent↔cluster WS the reply already uses; `otel_llm_capture_content` is a cluster-side span gate (documented on the frame).

## Result

- With the gateway off, the `agent.engine_call` span now carries `gen_ai.prompt`/`gen_ai.completion` for every engine including gemini-cli; ActivityLog stays metadata-only (verified by regression test).
- Adversarial review (4 lenses → refute-biased verify) surfaced 7 confirmed findings; all addressed (orphan-drain leak/staleness, empty-response interplay, run_engine integration coverage, cross-package wire round-trip, capture-toggle privacy docs).
- Verification: agent `uv run pytest` 400 passed; cluster 1069 passed; ruff clean on all changed files. PR / merge / cleanup pending.
