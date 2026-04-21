# feat(topology): per-user draggable node positions with localStorage persistence (#234)

- Commit: `870150e` (870150e3d6df4a513b77b5ba72b159526b627e6e)
- Author: Changyong Um
- Date: 2026-04-22T00:53:43+09:00
- PR: #234

## Situation

`/topology` canvas nodes had a `grab` cursor and the `draggable` class React Flow applies when `nodesDraggable` defaults to true, but dragging was visibly dead — only pane pan worked. Root cause: `<ReactFlow>` was in controlled mode (`nodes` prop supplied by the parent) without an `onNodesChange` handler, so every `NodeChange` React Flow dispatched during a drag was overwritten on the next render by the props-supplied dagre positions. The 5-second `useGraphData` poll made this worse — even if a transient local state had existed, each poll re-ran `useGraphLayout` and reset everything. Domain-wise, "nodes should be draggable" is a baseline expectation for any React Flow canvas; the feature had been assumed to work but never wired end-to-end.

## Task

- Make node drag visibly apply positions in real time.
- Persist positions so they survive refresh, the 5s poll, and unrelated graph mutations (new agents, status changes).
- Scope storage per `(userId, scope)` so admin's global view and a regular user's personal view don't collide.
- Add a way out — a Reset button that restores the auto-computed dagre layout.
- Keep the expensive dagre pass memoized; don't invalidate it on every drag.
- Leave the backend, API schema, and existing tests untouched (frontend-only UX improvement).

## Action

- Added `packages/cluster/frontend/src/components/topology/useTopologyLayoutOverrides.ts` (new). Returns `{ overrides, setPosition, reset, hasOverrides }` keyed on `(userId, scope)`. Storage key format: `doorae_topology_layout_v1_${userId}_${scope}`. When `userId` or `scope` is `null` (pre-login, first fetch still pending) every method is a no-op — callers don't need to guard. `readOverrides` / `writeOverrides` / `removeOverrides` all try/catch around `localStorage`, matching the Safari-private-mode policy from `useSidebarLayout`.
- Added `packages/cluster/frontend/src/components/topology/useTopologyLayoutOverrides.test.ts` (new, 8 cases): hydration default, localStorage hydration on first render, `setPosition` persist + merge with existing, `reset` clears both state and key, scope switch re-hydrates from the matching key, and two no-op paths (`userId=null`, `scope=null`). All pass with the `jsdom` environment and shared `beforeEach(localStorage.clear())`.
- Extended `packages/cluster/frontend/src/components/topology/useGraphLayout.ts`: added an optional `overrides` param. The dagre run stays in its own `useMemo` keyed on the input digest (no layout rerun when only overrides change); a second `useMemo` overlays `overrides` on the dagre result, keyed on `(dagreLayouted, overrides)` reference. When `overrides` is empty the overlay is a short-circuit return of the original layouted object, preserving reference identity so downstream memos don't invalidate unnecessarily.
- Rewired `packages/cluster/frontend/src/components/topology/TopologyCanvas.tsx`:
  - Introduced `useNodesState` for a local node list plus an `isDraggingRef` flag.
  - `useEffect` resyncs the local state from `props.nodes` whenever the parent-supplied array changes — but skips the resync mid-drag so the 5s poll can't tear the cursor-attached node.
  - `onNodeDragStart` sets the ref; `onNodeDragStop` clears it and calls `onPositionChange(id, {x,y})` exactly once per drag. `onLocalNodesChange` (from `useNodesState`) is passed to `<ReactFlow onNodesChange>` so in-flight position changes render optimistically.
  - Added a `RotateCcw` floating button to the top-right action cluster with a disabled/greyed state when `hasOverrides` is false. Clicking calls `onResetLayout()` and schedules a `fitView` on the next frame so the restored dagre layout is immediately centered.
  - `CanvasProps` gained three optional fields: `onPositionChange`, `onResetLayout`, `hasOverrides`.
  - All internal memos (`neighborNodes`, `displayNodes`, etc.) now read from `localNodes` instead of the raw `nodes` prop so hover/selection stays consistent with what the user is actually seeing.
- Wired `packages/cluster/frontend/src/pages/TopologyPage.tsx`: computes `scope` from `data?.scope` (narrowed to `'global' | 'personal'` to satisfy the hook's `Scope` type), composes `useTopologyLayoutOverrides(user?.id ?? null, scope)`, passes `overrides` into `useGraphLayout`, and threads `setPosition`, `reset`, `hasOverrides` through to `<TopologyCanvas>`.
- Regenerated `packages/cluster/frontend/tsconfig.tsbuildinfo` (already tracked per repo convention).

## Decisions

Mined from `.tmp/plan-234-topology-draggable-node-persistence.md`. Five design questions were resolved there:

1. **Storage backend — localStorage vs backend DB.** Weighed: (a) localStorage — no backend changes, matches the existing pattern used by `doorae_sidebar_collapsed` and `doorae_expanded_projects`; (b) backend `user_layout` table — cross-device sync but a schema migration, two API endpoints, permission policy, and new failure modes. localStorage won because the feature is "how I want to see the graph on this machine" — same class as the sidebar toggle, not account data. If cross-device sync is ever requested, the localStorage payload can be mechanically dumped and migrated. Assumption to revisit: that no user asks for cross-device layout sync.
2. **Storage key shape — flat vs nested.** Weighed: (a) flat `doorae_topology_layout_v1_${userId}_${scope}` → `{ nodeId: {x,y} }`; (b) single key with nested `{ userId: { scope: {...} } }`. Flat won because drag is a high-frequency event and flat keys don't require a parse-merge-stringify round trip on every save. `removeItem` also gives clean per-scope resets for free. Key-count overhead is trivial (max 2 keys per user).
3. **In-flight drag state — `useNodesState` vs manual `useState + applyNodeChanges`.** Went with `useNodesState`: it's the `@xyflow/react` canonical pattern and routes `NodeChange[]` through the library-provided helpers, so future changes (selection, dimensions) don't regress.
4. **Reset button placement.** Picked the top-right floating-action cluster alongside "Toggle minimap" and "Fit view" (rejected: FilterPanel section for an action that's rarely used, DetailPanel footer which requires selection). Discoverability sits with similar actions.
5. **Stale overrides for deleted nodes.** Decided not to prune on load — per-entry footprint is ~16 bytes and UUID-based IDs don't collide, so a deleted agent's entry is either inert or reusable if the ID ever comes back. If ID reuse patterns change, prune-on-load is a one-liner follow-up.

The load-bearing cross-cutting decision in the implementation itself: keeping dagre and overrides in **separate memos**. That choice is what lets the 5s poll continue to function without invalidating the expensive layout pass — the digest is a hash of graph structure, so status changes, label churn, and drag events all leave it stable.

## Result

- Nodes now drag and stay where they're dropped.
- After refresh the positions re-hydrate from localStorage — confirmed by the `hydrates from localStorage on first render` test and the `setPosition persists the position to state and localStorage` test.
- The 5s `useGraphData` poll no longer resets positions because the resync `useEffect` skips while `isDraggingRef` is true, and because dagre re-runs don't overwrite overrides (they're layered on top).
- New nodes appear at their dagre positions automatically — they just have no override entry.
- `Reset layout` button is disabled when nothing is customized and, when clicked, clears the scope's key and runs `fitView` so the user sees the restored layout centered.
- Scope switch (global ↔ personal) re-hydrates from a different key, covered by the `scope switch re-hydrates from the matching key` test.
- Regression verified: `npm run build` clean, `npm run test` 291/291 pass, `uv run pytest tests/test_graph_api.py -v` 16/16 pass.
- Manual Playwright-driven E2E (drag, refresh hold, poll hold, reset) was deferred because the worktree doesn't have a live backend; the test server at 192.168.100.81 will exercise the feature end-to-end after deploy. This is the remaining open verification item.
