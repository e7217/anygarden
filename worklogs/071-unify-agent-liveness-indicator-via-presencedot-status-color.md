# feat(agents): unify agent liveness indicator via PresenceDot + status color semantic (#71)

- Commit: `6ddb466` (6ddb466e0abd220e4027714c00ef71f3453b7046)
- Author: Changyong Um
- Date: 2026-04-16T19:04:59+09:00
- PR: #71

## Situation

Agent liveness was surfaced inconsistently across the UI. The sidebar DM section and the agent selection dialogs (`ManageRoomAgentsDialog`, `AgentEditDialog`) had no live dot at all, while the machine page ran its own `statusDot()` helper -- three different visual languages for the same signal. On top of that, `PresenceDot`'s "online" color was Notion Blue (`--color-brand`), the project's single interactive accent, so the dot was overloading "press me" with "this thing is alive". PR #60 had established presence infra; this change completes the UI unification promised in issue #71.

## Task

- Introduce one pure source of truth for agent liveness derivation (pure functions, unit-tested) so sidebar, machine page, and dialogs all read the same signal from `actual_state`.
- Extend `<PresenceDot>` with agent-variant tooltip voice so offline agents can show their lifecycle state (`stopped` / `crashed` / `unreachable`) instead of a last-seen timestamp intended for human participants.
- Move the online color off the Notion Blue accent to a sage green status color, without polluting the interactive palette.
- Respect the guardrail that `/api/v1/agents` is admin-only -- non-admin sidebar must not 403 nor show misleading all-offline dots.
- Document the status-color vs accent-color split in `DESIGN.md §2` so future status colors inherit the same separation.

## Action

- **New pure helpers** `packages/cluster/frontend/src/lib/agent-liveness.ts`: `ALIVE_AGENT_STATES = {running, starting}`, `deriveAgentOnline(actualState, { machineOffline })`, `agentStatusLabel(actualState, { machineOffline })`. `machine_offline` forces `online=false` and maps the label to `"unreachable"` without mutating the DB state.
- **Tests** `packages/cluster/frontend/src/lib/agent-liveness.test.ts`: 12 vitest cases covering every state transition listed in plan §5 plus the `ALIVE_AGENT_STATES` set and the `undefined → "unknown"` fallback.
- **CSS variable** `packages/cluster/frontend/src/index.css`: `--color-status-online: #5b9e6d` added next to the semantic tokens, with a comment tying it back to DESIGN.md §2.
- **`PresenceDot.tsx`**: added `variant?: 'user' | 'agent'` + `agentState?: string`. Offline tooltip now branches: user variant keeps the "마지막 응답 …" phrasing; agent variant shows `"오프라인 · ${agentState}"`. Online background switched to `var(--color-status-online, #5b9e6d)`.
- **`Sidebar.tsx`**: `useAgents()` gated by admin -- extracted a new `AgentDMListAdmin` subcomponent that only mounts for `user?.is_admin`, keeping the fetch out of guest sessions. The non-admin path renders the same DM list as before, just without the dot. A `findAgentForDM(dm, agents)` helper resolves `dm.representative_agent_id` first and falls back to name matching after stripping `^DM:\s*`.
- **`AdminMachines.tsx`**: replaced the per-row `statusDot(...)` + hand-rolled `displayState` / `isRunning` with the shared helpers and a `<PresenceDot variant="agent">`. The machine-self card at ~line 293 keeps `statusDot()` as explicitly out of scope. The `actual_state` text label stays next to the dot.
- **`ManageRoomAgentsDialog.tsx`**: added `<PresenceDot>` to every agent row (both "in this room" and "available").
- **`AgentEditDialog.tsx`**: added `<PresenceDot>` to the dialog header next to the agent name.
- **`DESIGN.md §2`**: new "Status colors" subsection documenting the sage green online color, the gray offline color, and the usage rule that status colors never double as interactive affordances.

## Result

One presence component, one pure derivation, one visual grammar for agent liveness across the app. Admin sidebar DM list, machine-page agent rows, room-agent manager, and agent-edit dialog all show the same sage dot driven by the same `actual_state → online` mapping, with `unreachable` correctly derived when a machine goes offline. Non-admin sidebar continues to work without hitting the admin-only agents endpoint (no 403, no misleading "all offline" state).

Validation: `npm test` → 72 passed (60 existing + 12 new). `npm run build` → clean tsc + vite build. Manual QA pending on the PR per the test plan (sidebar DM admin vs guest, machine-offline tooltip, regression check on participant-list WS presence path).

Followups noted in plan §6 remain out of scope: global WS presence subscription for higher accuracy, a non-admin-safe `/api/v1/rooms/dms` extension that carries agent state, and pulse/halo animations on state transitions.
