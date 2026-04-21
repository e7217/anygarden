# feat(observability): turn-level agent activity timeline (#222)

- Commit: `58c2449` (58c2449ba57a0fff900b0ce0ec64a44371cfb784)
- Author: Changyong Um
- Date: 2026-04-21T20:59:53+09:00
- PR: #222

## Situation

Admins needed to answer "how did this agent handle this message?" but
`ActivityPanel` rendered raw `ActivityLog` rows in a flat reverse-chrono
list of 50. A single agent turn is actually four rows —
`message_received`, `handler_started`, `response_sent`,
`handler_finished` — separated by the fixed ordering with no visual
bracket, so the panel answered at most "what happened, approximately"
and never "how did this specific turn unfold". The broader direction
(LLM-level tracing, tool-call events) would have been much bigger but
was blocked on engine limitations: only 2 of 5 engines can propagate
custom HTTP headers for `request_id`, so header-based
`LLMGatewayUsage` correlation would cover less than half the fleet.

## Task

- Keep scope to what gives admins clear turn-unit visibility in v1,
  stop short of cross-layer LLM correlation that needs its own design
  pass.
- Confirm the assumption that `ActivityLog.request_id` is already
  populated end-to-end (handler, lifecycle frames, response) so the
  work could be a grouping exercise rather than an event-emission
  one.
- Close the single remaining data-quality gap so the UI could tie a
  turn back to its trigger message without a second lookup.

## Action

- **Exploration first**: ran a structured audit of `ActivityLog`
  writers, `LLMGatewayUsage` shape, agent-side LLM call paths, and
  engine SDK header propagation. That surfaced two realities that
  shaped the plan: (a) request_id is already on every relevant row,
  and (b) header-based LLM correlation is engine-gated, so the
  natural v1 cut is server aggregation + UI grouping with LLM usage
  deferred to a follow-up issue.
- **Server — trigger_message_id**: modified
  `packages/cluster/doorae/ws/handler.py:796-804` so the
  per-agent `message_received` ActivityLog stamps
  `details["trigger_message_id"] = msg.id`. This is the one piece
  the UI couldn't derive otherwise.
- **Server — API surface**: extended `ActivityLogOut` in
  `packages/cluster/doorae/api/v1/agents.py:766` with
  `request_id: str | None = None` and wired it in the endpoint
  handler. The column was already on the model (migration 027); this
  just exposes it.
- **Frontend — ActivityPanel refactor**: rewrote
  `packages/cluster/frontend/src/components/agent-settings/ActivityPanel.tsx`
  around a pure `splitLogs` helper that groups rows by `request_id`
  into `Turn` objects carrying `outcome`, `duration`,
  `triggerMessageId`, and the ordered inner events. The component
  itself is now thin — a collapsible list of turn cards with a
  separate "System events" section for lifecycle-independent rows
  (`start_requested`, `stop_requested`, `state_changed`,
  `replacement_requested`). Outcome is derived from terminal events:
  `response_sent` → responded, `handler_finished` only → silent,
  `handler_orphaned` → orphaned, otherwise in-flight.
- **Tests**:
  `packages/cluster/tests/test_ws_handler.py::TestActivityLogRequestIdCorrelation`
  adds two end-to-end cases (user send records `trigger_message_id`;
  agent-echoed `request_id` survives onto `Message.extra_metadata`).
  `packages/cluster/tests/test_agents_api.py::TestAgentActivityEndpoint`
  pins the endpoint's `request_id` surface for both lifecycle and
  system rows.
  `packages/cluster/frontend/src/components/agent-settings/ActivityPanel.test.ts`
  adds 5 pure-function cases on `splitLogs` covering partitioning,
  chronological sort, outcome derivation, recency order, and empty
  input.
- **Planning discipline**: wrote `.tmp/plan-222-turn-level-agent-activity.md`
  first with an explicit change-log entry capturing why the scope
  shrank from "turn_started/turn_completed synthetic events + LLM
  correlation" down to "UI grouping + one data-quality stamp".

## Result

- `cd packages/cluster && uv run --extra dev pytest tests/` → 683
  passed (3 new), `cd packages/machine && uv run --extra dev pytest
  tests/` → 283 passed (unchanged), `cd packages/cluster/frontend &&
  npx vitest run` → 282 passed (5 new), `npm run build` → clean tsc
  + vite.
- New surface: agent settings > Activity tab now shows a collapsible
  per-turn timeline with duration, outcome badge, and a short hash
  of the trigger message id. System events live below. Expand any
  turn to see its full ordered event list.
- Deferred work, recorded for later follow-ups:
  - `LLMGatewayUsage.request_id` + server-side `current_turn`
    mapping (blocked on deciding how to handle concurrent-turn
    attribution for Claude Code / Codex / Gemini paths).
  - `tool_call` / MCP invocation visibility — engine SDK support
    varies too widely to design in a single pass; will need per-
    engine probe first.
