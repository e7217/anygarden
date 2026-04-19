# feat(agent,cluster): strategy dispatcher + round_robin (#159 Phase B)

- Commit: `a9b2180`
- Author: Changyong Um
- Date: 2026-04-19
- PR: pending
- Issue: #159 (umbrella)

## Situation

Phase A (#164) laid the schema, the welcome propagation, and the per-room client cache for `speaker_strategy`. Nothing was actually wired to `decide_policy` yet — every room still behaved as `mentioned_only` regardless of DB value. Phase B has to turn that cache into a real routing decision, and implement the first non-default strategy (`round_robin`) so the user-side drill in Phase E has something to exercise. `orchestrator` is held back because the user chose option C (all-engine handoff_to support), which shifts Phase C into a multi-engine effort that deserves its own PR boundary.

## Task

- Add a strategy dispatcher to `decide_policy` without regressing any of the 15+ rules-based tests from #157 / earlier issues.
- Implement the `round_robin` branch end-to-end: server computes the next speaker, stamps it on the broadcast, and the agent honours it.
- Leave `orchestrator` as an explicit stub that falls through to `mentioned_only` semantics, so rooms that switch their strategy early don't silently stop responding.
- Keep direct mentions, task-init prefixes, cycle detection, and ingest_only all strategy-independent — they live above the dispatcher.

## Action

- `packages/agent/doorae_agent/integrations/base.py`
  - After rule 5 (explicit mention not us), read `client._speaker_strategy.get(room_id, "mentioned_only")`.
  - `round_robin` branch consults `metadata.next_speaker_participant_id`: match ⇒ RESPOND, mismatch or absent ⇒ SKIP. No "everyone replies" fallback — the server must stamp the pointer.
  - `orchestrator` branch falls through to the shared rule 6/7 tail (mentioned_only semantics). Phase C replaces this with real handoff handling.
- `packages/cluster/doorae/ws/handler.py`
  - `sa_update` imported; `Room` lookup widened to include `speaker_strategy` and `current_speaker_index` alongside the existing `context_window_enabled`.
  - New `_compute_round_robin_next(db, room_id, current_index, sender_is_human)` helper. Agent participants ordered by `joined_at, id` for stable rotation. Human sender ⇒ reset to index 0; agent sender ⇒ `(current + 1) % len`. Returns `None` when the room has no agents.
  - When strategy is `round_robin`, the helper's result is stamped on the message `metadata` and persisted to `Room.current_speaker_index` + `Room.next_speaker_participant_id`.
- `packages/agent/tests/test_integrations/test_should_respond.py`
  - `_make_client` gains a `speaker_strategy` kwarg defaulting to `{}` (legacy behaviour).
  - `TestRoundRobinStrategy` (5 tests): my-turn-responds, not-my-turn-skips, missing-metadata-skips, mention-wins-over-rotation, task-init-wins-over-rotation.
  - `TestOrchestratorStrategyStub` (2 tests): human-unaddressed-responds, agent-unaddressed-skips — documents the fallback behaviour so Phase C can see when the stub is replaced.

## Result

- `packages/agent/` — 201 passed (194 + 7 new) plus the unaffected `OPENAI_API_KEY` flake.
- `packages/cluster/` — 600 passed, no regressions. The #148 ingest_only path, room_query flow, and 40+ existing handler tests all continued to work after widening the Room lookup.
- `round_robin` rooms now rotate turn-by-turn without agent-side coordination. Direct mentions and task-init prefixes still pre-empt the rotation, so operators can break out of the strategy without toggling it off.
- Server-side round_robin integration drill is deferred to Phase E (manual drill). The helper is trivially unit-testable; the dispatcher is covered by the 7 new agent-side tests; the risk surface is small enough that a round-trip integration test would be disproportionate for this PR.
- Phase C can now implement handoff_to with confidence that the dispatcher is in place — the `orchestrator` branch already exists and just needs a body.
