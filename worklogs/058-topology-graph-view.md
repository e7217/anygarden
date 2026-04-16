# feat(topology): add /topology graph view (#58)

- Commit: `0e90763` (0e90763dcb0f9ad85508a04af83992feaceff502)
- Author: Changyong Um
- Date: 2026-04-16T20:52:07+09:00
- PR: #58

## Situation

`/admin/machines` was the only surface to inspect cluster topology,
and its tabular layout hid the relationships between Users, Machines,
Agents, Rooms, and Projects. Operators debugging placement or
permission issues had to cross-reference multiple tables manually.
There was no visual way to see "which agents live on which machine"
or "which rooms does this agent represent" at a glance.

## Task

- Build an interactive topology page at `/topology`:
  - Admin sees the full cluster; regular users see only their own
    slice (own machines / participating rooms / agents in those rooms).
  - Guests must be rejected outright.
- Keep the snapshot endpoint read-only, cacheable (ETag), and free of
  N+1 fetches even as the graph grows.
- Personal scope must not leak any other user's id — security-critical.
- Every UI surface had to adhere to `DESIGN.md` (warm neutrals,
  whisper-weight borders, sub-0.05 shadows, single Notion Blue accent).
- Ship as v1 MVP — read-only, no WebSocket deltas, no drag-to-replace.

## Action

**Backend** (`packages/cluster/`):
- New router `doorae/api/v1/graph.py` exposing
  `GET /api/v1/graph?scope=personal|global|auto`.
- Pydantic `NodeOut`/`EdgeOut`/`GraphOut` models with kind enums.
- `_resolve_scope()` maps identity × requested scope to final mode
  (admin auto → global; user auto → personal; user global → 403).
- `_build_global_graph()` issues 6 bulk `SELECT`s (users, machines,
  agents, rooms, projects, participants) — no per-row fetches.
- `_build_personal_graph()` computes the visible id sets
  (owned_machines, my_room_ids, agents_on_my_machines,
  agents_in_my_rooms) and filters edge assembly to those sets; any
  Participant row referencing another user is dropped before edges
  are assembled. Cross-user ids in agent `placed_on_machine_id` /
  room `parent_room_id` / room `representative_agent_id` are nulled
  out so no foreign id leaks through node `data`.
- `_etag_for()` produces a stable short sha256 digest over sorted
  nodes+edges (excluding `generated_at`) so re-fetches short-circuit
  with `304 Not Modified`; `Cache-Control: private, max-age=5` set.
- Router registered in `doorae/app.py:23,338`.

**Tests** (`packages/cluster/tests/test_graph_api.py`):
- Permission matrix (admin/user/guest × global/personal/auto).
- Schema validation (node kinds, edge kinds, id prefixes).
- Personal-scope leak test that explicitly asserts Bob's ids never
  appear in Alice's response (node list and edge source/target).
- ETag roundtrip produces 304 and `Cache-Control` header.
- N+1 guard via a `before_cursor_execute` listener asserting ≤12
  SELECTs for a populated global graph.

**Frontend** (`packages/cluster/frontend/`):
- Added deps: `@xyflow/react@^12`, `dagre@^0.8`, `@types/dagre`.
- `src/components/topology/`:
  - `types.ts` — mirrors the backend shape.
  - `constants.ts` — design tokens (borders, shadows, engine tints,
    `agentStateColor`, `machineStatusColor`, `edgeStyleFor`).
  - `useGraphData.ts` — SWR-lite fetch with ETag `If-None-Match`,
    `AbortController` cleanup on unmount, `refresh()` trigger.
  - `useGraphLayout.ts` — dagre wrapper memoised by a cheap
    nodes+edges digest; converts to React Flow node/edge objects;
    rank-by-kind keeps user→machine→agent→room hierarchy.
  - `nodes/`: MachineNode (136×56 card with status dot + agent count),
    AgentNode (64×64 circle with engine icon + state-colored ring,
    Notion Blue for running), RoomNode (auto-width pill with `#`/`@`
    prefix + participant count + representative star), UserNode
    (56×56 avatar with admin crown), ProjectGroup (dashed container,
    hidden by default in v1). All wrapped in `React.memo`.
  - `edges/RelationEdge.tsx` — dispatches on `data.kind`/`data.actor`
    to smoothstep / straight / dashed styles per the plan's edge table.
  - `TopologyCanvas.tsx` — ReactFlow canvas with
    `onlyRenderVisibleElements`, `fitView`, hover focus-fade
    (opacity 0.18 for non-neighbors via `getIncomers/getOutgoers`),
    click-to-select, double-click Room → `navigate('/rooms/<id>')`,
    Esc clears; floating fit-view + minimap-toggle buttons.
  - `FilterPanel.tsx` — kind checkboxes (with counts), engine chips,
    actual_state chips, name search; pure-memo filter application.
  - `DetailPanel.tsx` — slide-in 320px right panel with per-kind
    field layouts; reuses `AgentRoomsDialog` for agent "View rooms".
- `pages/TopologyPage.tsx` — page shell with Sidebar, mobile top bar,
  desktop header (scope + node/edge counts + refresh), Suspense
  skeleton, ErrorBoundary, and empty state. Wires `useGraphData` →
  filter → `useGraphLayout` → canvas.
- `App.tsx` — `/topology` route with `React.lazy` + Suspense so the
  ~100KB gzip chunk only loads when visited.
- `components/Sidebar.tsx` — admin-only "Topology" entry alongside
  "Machines" (Share2 icon).

## Result

- `/topology` is live for any authenticated user, with scope
  automatically narrowed to their visible slice and admin nav in
  the Sidebar.
- Backend tests: 12/12 pass in `test_graph_api.py`; full cluster
  regression 378 passed, machine 209 passed, frontend 72 passed
  (one unrelated pre-existing agent test needs `OPENAI_API_KEY`).
- Vite build produces an isolated `TopologyPage-*.js` chunk at
  99.5KB gzip — main bundle unchanged.
- Personal scope leak test explicitly guards that other users' ids
  never leak in nodes or edges — first line of defense going forward.
- v2 items explicitly deferred: WebSocket `graph.delta` stream,
  Force-directed layout toggle, drag-Agent-to-Machine reassignment,
  activity-weighted edge thickness.
