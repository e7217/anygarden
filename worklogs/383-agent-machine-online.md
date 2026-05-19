# fix(agents): expose machine online status in agent responses (#383)

- Commit: pending in `fix/383-agent-machine-online`
- Author: Changyong Um
- Date: 2026-05-19T23:10:00+09:00
- PR: #383

## Situation

Agent UI surfaces used `Agent.actual_state` as the presence source.
When a hosting machine disappeared, the daemon could no longer report
state, so the DB could keep a stale `running` value. The frontend
already had `machineOffline` handling in `agent-liveness.ts`, but most
agent call sites did not have machine presence data to pass into it.

## Task

- Add a server-provided `machine_online` boolean to `/api/v1/agents`
  responses.
- Derive that field from `MachineBus.is_connected(machine_id)`, the
  live WebSocket presence source, rather than the mirrored DB status.
- Wire sidebar, settings, overview, and room-management presence dots
  to treat `actual_state='running' + machine_online=false` as
  unreachable/offline.

## Action

- `packages/cluster/doorae/api/v1/agents.py` now adds
  `AgentOut.machine_online` and routes all `AgentOut` response paths
  through `_agent_to_out(agent, machine_bus)`.
- `tests/test_agents_api.py` covers connected, disconnected, and
  unplaced agents so stale `running` cannot serialize as online.
- `packages/cluster/frontend/src/hooks/useAgents.ts` adds the optional
  `machine_online` field for backward compatibility.
- `Sidebar`, `AgentSettingsDialog`, `OverviewPanel`, and
  `ManageRoomAgentsDialog` now pass `machineOffline:
  agent.machine_online === false` into the shared liveness helpers.

## Result

- A fresh `/api/v1/agents` fetch now reflects machine WebSocket
  presence directly.
- A stale `running` agent on an offline machine renders with a gray
  presence dot and an `unreachable` agent status label where that UI
  shows status text/tooltips.
- Verification:
  - `uv run --extra dev pytest -x -v` in `packages/cluster`:
    972 passed, 1 deselected.
  - `uv run --extra dev ruff check doorae` in `packages/cluster`:
    clean.
  - `npm test` in `packages/cluster/frontend`: 420 passed.
  - `npm run build` in `packages/cluster/frontend`: passed.
