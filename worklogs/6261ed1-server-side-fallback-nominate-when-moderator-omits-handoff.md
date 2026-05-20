# fix(orchestrator): server-side fallback nominate when moderator omits handoff

- Commit: `6261ed1` (6261ed11f8c054a0c7fb029df36824d07060bb9c)
- Author: Changyong Um
- Date: 2026-05-20T15:00:33+09:00
- PR: —

## Situation

The `orchestrator` speaker-selection strategy delegated turn-taking entirely to the moderator LLM via `[HANDOFF]` prefixes or addressable mentions in the broadcast. V1–V5 PoC reproduced a 5/5 failure mode: the moderator nailed the first handoff but dropped the mention token from the second turn onward, even with progressively stronger persona instructions (handoff_to emphasis → @-mention style → model pinning → hallucination guards → absolute prohibitions). Without a server-side safety net the room silently stalled — every participant saw the moderator message as `ingest_only` and no one was triggered to reply.

## Task

- Add a deterministic server-side fallback that nominates the next speaker when the moderator emits a non-terminal message without a valid handoff and without an addressable mention.
- Preserve every explicit signal: never override an upstream `next_speaker_participant_id`, respect `[종료]` termination, leave worker messages and rooms without an `orchestrator_agent_id` untouched.
- Pool rotation must exclude the orchestrator itself (the moderator never nominates itself) and use the same `(joined_at, id)` ordering as the standard round-robin so user mental models stay consistent.
- Ship with research evidence — the underlying LLM failure modes need to be documented so future medium-term work (constrained handoff via `tool_choice` / structured outputs) inherits the context.

## Action

- `packages/cluster/doorae/ws/handler.py:225-333` — new `_apply_orchestrator_fallback_nominate` helper. Encodes the five acceptance rules in early-return guards, runs a `Participant` query filtered to non-orchestrator agents ordered by `(joined_at, id)`, advances `(current_speaker_index + 1) % len(rows)`, and updates both `Room.current_speaker_index` / `Room.next_speaker_participant_id` plus the broadcast `metadata` in place so agent-side `decide_policy` rule 4a wakes the nominated participant.
- `packages/cluster/doorae/ws/handler.py:1136-1169` — wires the fallback into the orchestrator code path, gated on `speaker_strategy == "orchestrator"` and after the upstream `_apply_orchestrator_handoff` attempt. Emits a `orchestrator_fallback_nominate` warning log with the resulting participant + index for observability.
- `packages/cluster/tests/test_orchestrator_fallback.py` — 6 new tests covering: happy-path nominate, sender ≠ orchestrator skip, `[종료]` skip, upstream-nomination-set skip, addressable mention skip, empty pool skip.
- `docs/research/2026-05-12-multi-agent-turn-taking-mediator-failure.md` — 373-line deep-research note documenting the V1–V5 failure observations, the convergent LLM mechanisms (multi-turn instruction-following decay, format-task interference, lost-in-the-middle), the industry mitigations surveyed (AutoGen `SelectorGroupChat.selector_func`, LangGraph `Command(goto=...)`, MetaGPT type-subscribed routing, Magentic-One dual-loop), and the three-tier recommendation (short-term server fallback → mid-term constrained handoff → long-term type-subscribed routing).

## Decisions

- **Server-side rotation vs. retry-the-LLM**: Retrying the moderator with stronger prompts was rejected — V1–V5 already exhausted persona reinforcement. The failure is structural (instruction-following decay across turns), not stochastic, so adding latency to retry the same prompt would not converge. Server rotation guarantees forward progress at the cost of one "wrong" nominee per stall, which is recoverable in the next turn.
- **Round-robin over LLM re-ask via tool_choice**: Constrained handoff via `tool_choice: {"type": "any"}` (Anthropic) or structured outputs (OpenAI) is the medium-term fix called out in the research note; it requires per-adapter changes and was deliberately scoped out of this PR to keep the safety net deployable today. Round-robin is dumb but unambiguous and ships now.
- **Excluding the orchestrator from the pool**: Including the orchestrator would let the fallback nominate itself when only one worker is present, looping on the same failure. Better to return `None` (no-op, message flows as `ingest_only`) than to perpetuate the stall.
- **Mutating `metadata` in place vs. returning a stamp**: The broadcast envelope is already constructed by the caller; in-place mutation keeps the fallback transparent to downstream code that reads `metadata["next_speaker_participant_id"]`. Returning a stamp would have forced every caller to thread it through, increasing the chance of forgetting to apply it.
- **Honoring `[종료]` but not other prefixes**: `[종료]` is the orchestrator's explicit "we're done" signal — overriding it would force the room to keep speaking past the wrap-up. Other prefixes (`[HANDOFF]`, `[DELEGATED]`, `[ROOM_QUERY]`) are handled upstream; the fallback only fires when those upstream paths fail to stamp a nominee, so no special-casing is needed for them.

## Result

- The `orchestrator` strategy no longer stalls when the moderator LLM omits the mention token from a non-terminal message; the room rotates to the next worker via round-robin.
- 6 new tests in `test_orchestrator_fallback.py` cover the acceptance matrix; full cluster suite remains green (993 passed).
- `orchestrator_fallback_nominate` warning log fires on every fallback activation, giving operators visibility into how often the LLM falls back so the team can decide when to invest in the medium-term constrained-handoff work.
- Medium- and long-term mitigations (`tool_choice`-forced handoff, type-subscribed routing) remain pending — tracked in the research note's recommendations section.
