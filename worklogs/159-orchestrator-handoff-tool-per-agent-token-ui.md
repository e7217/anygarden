# feat(cluster,agent): orchestrator + handoff tool + per-agent token UI (#159 Phase C+D)

- Commit: `63f4c8b` (63f4c8bca6b2875c72b14342e293af817262cef1)
- Author: Changyong Um
- Date: 2026-04-19
- PR: pending
- Issue: #159 (umbrella)

## Situation

Phase A (#164) and Phase B (#168) landed the schema, welcome propagation, client cache, strategy dispatcher, and `round_robin`. `orchestrator` was left as a documented stub that falls back to `mentioned_only` semantics, and the per-agent token panel — which makes orchestrator rooms observable in practice — was never built. The admin UI also had no way to flip `speaker_strategy` or pick an orchestrator agent; a freshly-added column sat unreachable. With the dispatcher plumbing in place, Phase C+D closes the loop so orchestrator rooms are end-to-end operable by a human admin.

## Task

- Expose `speaker_strategy` / `orchestrator_agent_id` on the `PATCH /api/v1/rooms/{id}` surface, admin-gated without losing the member-open rename path.
- Replace the Phase B orchestrator stub with real O1/O2/O3 rules, keeping the existing 200+ tests green.
- Teach the WS handler to parse `[HANDOFF]` from the orchestrator and flip `Room.next_speaker_participant_id` inside the same transaction that persists the message, so the broadcast carries the stamp and the target agent wakes up under O2.
- Register an in-process `handoff_to` MCP tool in the Claude Code adapter, but only when the current client is the room's orchestrator — otherwise the LLM is tempted to forge turn-order decisions it shouldn't make.
- Give admins a strategy dropdown + orchestrator picker in the existing room-edit dialog, plus a per-agent token panel that reuses the #157 endpoint so no new backend surface is required for Phase D.

## Action

- `packages/cluster/doorae/rooms/router.py`
  - `RoomOut` / `RoomDetailOut` gain `speaker_strategy` (default `"mentioned_only"`) and `orchestrator_agent_id`. `list_rooms` + `get_room` populate both.
  - `RoomUpdate` accepts the two new fields; `update_room` validates strategy against a `_VALID_SPEAKER_STRATEGIES` frozenset (`mentioned_only`, `round_robin`, `orchestrator`) and verifies `orchestrator_agent_id` is actually a participant before committing.
  - Admin check is inline (not via `get_admin_identity`) so non-admin members keep the rename/description/context-window surface open. Touching either new field as a non-admin returns 403 with no partial write.
- `packages/cluster/doorae/ws/handler.py`
  - `_apply_orchestrator_handoff` helper mirrors `_compute_round_robin_next`: sender-is-orchestrator gate, first-`type=user`-mention as target, participant-exists check, then `Room.next_speaker_participant_id` update + `metadata["next_speaker_participant_id"]` stamp. Workers emitting `[HANDOFF]` are silently ignored (the prefix itself still carries the mention).
  - Main SendFrame path reads `orchestrator_agent_id` alongside `context_window_enabled` / `speaker_strategy` / `current_speaker_index` in the same row fetch and invokes `_apply_orchestrator_handoff` regardless of strategy — handles a room that was just flipped back to `mentioned_only` with in-flight handoffs.
  - `_is_ambient_candidate` now short-circuits on `[HANDOFF]` so ambient-context stamping doesn't demote a handoff to `ingest_only`.
- `packages/agent/doorae_agent/integrations/base.py`
  - Orchestrator branch of `decide_policy` replaces the stub with O1 (I am `_orchestrator_agent_id[room_id]` → RESPOND), O2 (`next_speaker_participant_id` matches one of my participant IDs → RESPOND), O3 (else SKIP). When orchestrator is unset, the branch falls through to rule 6/7 so a misconfigured room stays usable.
  - Base rules (self-echo, `[DELEGATED]`, `[ROOM_QUERY]`, cycle, explicit mention, `ingest_only`, mention-not-us) still run upstream of the dispatcher — direct mentions and task-init prefixes pre-empt the orchestrator gate by design.
- `packages/agent/doorae_agent/client.py`
  - `_is_task_init_content` recognises `[HANDOFF]` alongside `[ROOM_QUERY]` / `[DELEGATED]` so the per-room agent-turn counter resets when the orchestrator hands off, mirroring the #67 semantics for the new prefix.
- `packages/agent/doorae_agent/integrations/claude_code.py`
  - `ClaudeCodeAdapter.__init__` accepts an optional `client: ChatClient` so the handoff tool closure can call `client.send`. Legacy constructors (`ClaudeCodeAdapter()` in tests) keep working — `_is_orchestrator_of` returns False when client is missing.
  - `_build_options` conditionally stamps `mcp_servers={"doorae": config}` + `allowed_tools=["mcp__doorae__handoff_to"]` when `_is_orchestrator_of(room_id)` is True. Otherwise the LLM never sees the tool, closing off forged handoffs.
  - `_ensure_handoff_server_config` builds the in-process MCP server once, using the SDK's `tool` decorator + `create_sdk_mcp_server`. The tool handler reads `self._current_room_id` (set per turn in `on_message`, cleared in `finally`) to route `[HANDOFF] <@user:{participant_id}> {reason}` back to the right room with a `metadata.handoff` envelope. Returns `is_error: True` when called without participant id or room context.
- `packages/cluster/frontend/src/components/RoomEditDialog.tsx`
  - Loads `speaker_strategy`, `orchestrator_agent_id`, and the agent-kind participants from the existing room GET; admin-only strategy dropdown + orchestrator picker render above the context-window toggle. Picker is hidden when strategy isn't `orchestrator` to avoid suggesting it's meaningful elsewhere.
  - New `PerAgentTokenPanel` merges 1h / 24h `per_agent` rows from `GET /api/v1/rooms/:id/token-stats` (admin-only, 403 handled silently). DESIGN.md warm-neutral palette: `#f6f5f4` header, `var(--color-border)` table lines, tabular-nums for the counts, no accent colour on raw numbers.
- Tests:
  - `packages/cluster/tests/test_rooms.py::TestRoomSpeakerStrategy` (8 tests) — default, admin set/clear, non-participant 400, unknown-strategy 400, non-admin 403 for both admin fields, non-admin can still rename.
  - `packages/cluster/tests/test_handoff_wiring.py` (5 tests) — helper in isolation: orchestrator path flips the pointer, worker path is a no-op, missing mention / non-participant target / non-handoff content all bail cleanly.
  - `packages/agent/tests/test_integrations/test_should_respond.py::TestOrchestratorStrategy` (8 tests) — O1/O2/O3 + graceful fallback + mention/DELEGATED pre-emption.
  - `packages/agent/tests/test_integrations/test_claude_code.py::TestHandoffTool` (5 tests) — tool exposure toggles on orchestrator check, handler sends the `[HANDOFF]` marker with the right metadata, missing participant returns `is_error`.

## Decisions

Four non-trivial calls came out of this PR:

1. **Admin gate lives inline, not via `get_admin_identity`.** Using the dep at the router level would force every member-initiated rename through the admin path too, breaking the pre-#159 contract that non-admin members can still rename / describe / toggle context-window. The inline check (`identity.kind == "user" and claims.is_admin`) is exactly `get_admin_identity`'s body minus the hard fail, and only fires when an admin-only field is actually present in the payload. Rejected alternative: split into `PATCH` (member) + `PATCH /admin` (admin) — would have cost an extra route plus a frontend conditional for no real win.
2. **`handoff_to` is admin-gated via orchestrator identity, not via `allowed_tools` alone.** The SDK's `allowed_tools` is an allow-list, not a permission boundary — a rogue custom prompt could still request the tool if it's in the server's tool list. Gating at `_build_options` (only register `mcp_servers` when I'm the orchestrator) means workers never receive the tool in their SDK config, which is a stronger guarantee than trusting allow-list filtering.
3. **Handoff goes through `client.send` with a `[HANDOFF]` content prefix, not a dedicated WS frame.** Reuses `parse_mentions`, the message-persist transaction, the broadcast fan-out, and the reconnect replay. A new frame type would have doubled the WS protocol surface for no user-visible gain. The prefix is already a recognised task-init marker (matches `[DELEGATED]` / `[ROOM_QUERY]` pattern), so the agent-side `_is_task_init_content` change is the only cross-cutting update.
4. **Server trusts the orchestrator's handoff target after membership validation only — no capability-bound tokens.** LLMs hallucinate participant IDs; `_apply_orchestrator_handoff` confirms the target is a participant of the same room before flipping the pointer, which is the same trust level as `set_representative`. Rejected alternative: per-turn signed handoff tokens minted by the client — overkill for a closed-room trust model where every participant is already authenticated.

Assumption to revisit if violated: orchestrator rooms are assumed to have a human in the loop (or at least a `#157` cycle-guard budget) preventing runaway A→B→A chains. If production data shows orchestrator rooms burning token budgets well past the expected pattern, `R7` (token auto-cutoff) from plan-159 §7 moves from "conditional follow-up" to "required".

## Result

- Agent `packages/agent/tests/` — 210 passed (198 + 12 new). `test_integrations/test_openai.py` still requires `OPENAI_API_KEY` and is unrelated.
- Cluster `packages/cluster/tests/` — 610 passed (602 + 8 new in `test_rooms.py` + 5 new in `test_handoff_wiring.py` — 3 existing cluster tests subsumed or re-organised during the strategy wiring).
- Frontend `packages/cluster/frontend/` — `npm run build` (tsc + vite) passes cleanly; RoomEditDialog vitest suite continues to pass (2/2).
- Admin UI now lets an admin flip a room into `orchestrator` mode, pick the orchestrator agent from the room's agent participants, and watch per-agent token usage in the same dialog. Non-admin members see the original surface unchanged.
- Phase E manual drill (3-agent orchestrator room, 5+ handoff chain) is still pending — this PR covers the automated coverage; the plan's "수동 drill" line item carries over. Flagged for before merge.
- Remaining scope on #159: Phase E manual drill + `docs/decisions/NNN-speaker-strategy.md` write-up are the last open items before the umbrella issue closes.
