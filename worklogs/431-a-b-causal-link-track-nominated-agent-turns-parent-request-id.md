# feat(observability): A→B causal link — track nominated agent turns + parent_request_id (#431)

- Commit: `711d9fd` (711d9fd)
- Author: Changyong Um
- Date: 2026-06-10
- PR: #431 (issue)

## Situation

After the Phase 1–3 instrumentation work (#420/#422/#425/#427/#429), the `request_id` turn model only tracked **user-triggered** turns: a user send minted a `request_id` per agent, but when agent A's reply woke agent B (round-robin / orchestrator handoff / fallback), B's turn ran with no `request_id`. As a result B's turn was invisible to ActivityLog and to the trace, and the #429 RoomActivityDialog room-flow view could not draw the A→B causal edge. Langfuse grouped a room's turns into one session via `langfuse.session.id=room_id`, but carried no explicit causal relationship between turns.

## Task

- Track the agent→agent turn without flooding ActivityLog with phantom orphans (agent chatter is far more frequent than user sends, so a naive fan-out to every room agent per agent-send would multiply orphans).
- Stamp the triggering turn's id (`parent_request_id`) on the nominee's turn so the flow view / trace can draw A→B.
- Add an explicit causal edge in OTEL without changing the wire protocol or the agent runtime.
- Surface the link in the admin room-flow UI.

## Action

- `packages/cluster/anygarden/ws/handler.py`: captured the **server-authoritative** next speaker into a new `nominated_pid` local at each dispatcher site — round-robin (`next_info`), orchestrator handoff (now captures `_apply_orchestrator_handoff`'s return), and fallback nominate (`fallback_info`). In the agent-send `response_sent` branch, when `nominated_pid` is another agent (`!= participant.id`), mint exactly one tracked turn: resolve the nominee's `agent_id`, mint a `request_id`, register it in `request_id_by_participant` (so `_make_out` injects it into that nominee's tailored broadcast), and write a `message_received` ActivityLog with `parent_request_id` (A's echoed id), `trigger_message_id` (A's message), and `room_id`; then `tracing.start_request(parent_request_id=…)`.
- `packages/cluster/anygarden/observability/tracing.py`: `start_request` gained `parent_request_id`; new `_parent_links` helper attaches a **typed FOLLOWS_FROM** `Link` (attribute `opentracing.ref_type="follows_from"`, constants `_REF_TYPE_KEY`/`_REF_TYPE_FOLLOWS_FROM`) to the parent's still-open root span context. B stays its own trace (not a child — A's root closes first). `anygarden.parent_request_id` is stamped regardless of whether the parent span resolved. No cache (synchronous: B is minted on A's `response_sent`, before A's `handler_finished`).
- `packages/cluster/frontend/src/components/agent-settings/ActivityPanel.tsx`: `Turn.parentRequestId`, derived in `splitLogs` from the `message_received` row's `details.parent_request_id`.
- `packages/cluster/frontend/src/components/RoomActivityDialog.tsx`: build a `requestId→turn` map and render `↳ from <parent agent 6-char>` when the parent turn is in-window (refactored the `turns.map` callback to a block body).
- Tests: `test_observability_tracing.py` (typed FOLLOWS_FROM, survives parent close, unknown-parent degrade, no-parent); `test_ws_handler.py::TestAgentCausalLink` (round-robin targeting, no-nomination, self-nomination skip, forged-metadata regression); `ActivityPanel.test.ts` (parentRequestId); new `RoomActivityDialog.test.tsx` (↳ in-window / off-window / null-parent).
- `docs/plans/2026-06-09-agent-causal-link-design.md`: design doc (Approach A).

## Decisions

- **Approach A — nominate-targeted fan-out** over (B) fan-out-to-all-agents and (C) message_id→request_id cache. B was rejected because agent turns are frequent and N-1 nominees would immediately orphan, making `agent_turns_orphaned_total` meaningless. C was rejected because the cluster already knows the authoritative next speaker via the dispatcher, so a timing-heuristic cache discards that signal and adds expiry/miss complexity. A wins because `next_speaker_participant_id` is already computed (synchronously, before append) on every orchestration path — minting for that one agent gives phantom-0, cache-0, wire-unchanged causality.
- **FOLLOWS_FROM span Link, not parent-child**: A and B are independent request lifecycles and A's root closes before B's runs, so nesting would mismatch span lifetimes. A Link records the causal edge; `langfuse.session.id` still groups them. Per the #431 adversarial review, a bare `Link(ctx)` is **untyped** (indistinguishable from child-of), so the `opentracing.ref_type=follows_from` attribute was added to make the relationship explicit (hard-coded rather than imported from the unstable `opentelemetry.semconv._incubating`).
- **Drive minting from the server-set `nominated_pid`, not inbound `metadata`** (review finding #3, high): reading `metadata.next_speaker_participant_id` would let an agent forge the field and spuriously mint/trigger a peer's turn in any room. `Room.next_speaker_participant_id` was also rejected (it persists a stale value across sends). Capturing the dispatcher helper return values is the only source that reflects *this* send's authoritative nomination.
- **Assumptions / revisit triggers**: a nominated-but-silent B leaves a `message_received`-only row the orphan sweeper does NOT collect (it keys on `handler_started`) — consistent with the existing user-send fan-out, not a new defect; revisit if silent-nomination accounting is needed. Causal linking is reliable only at `otel_sampling_ratio == 1.0` (the default): B is an independent trace and OTEL's `ParentBased(TraceIdRatioBased)` sampler ignores links, so under sampling A and B keep/drop independently. Multi-nomination is out of scope (`next_speaker_participant_id` is single).

## Result

- Agent→agent turns are now tracked end-to-end: B's `message_received` carries `parent_request_id`/`trigger_message_id`, B's lifecycle threads under the minted `request_id`, and the trace carries a typed FOLLOWS_FROM edge B→A. RoomActivityDialog shows `↳ from <parent>`.
- An adversarial review workflow (4 lenses → refute-biased verify) surfaced 7 confirmed findings; all addressed (server-authoritative nomination + forged-metadata regression test, typed FOLLOWS_FROM + assertion, ↳ label clarity, RoomActivityDialog component test, sampling + orphan-sweep doc corrections).
- Verification: cluster `uv run pytest` 1063 passed; frontend `npm run build` (tsc) + `vitest` 432 passed; ruff clean on changed sources. PR / merge / cleanup pending.
