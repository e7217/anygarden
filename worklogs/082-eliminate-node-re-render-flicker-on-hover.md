# fix(topology): eliminate node re-render flicker on hover (#82)

- Commit: `fb1606f` (fb1606fe715b01814759bab15b89d97d2cc25158)
- Author: Changyong Um
- Date: 2026-04-17T01:43:23+09:00
- PR: #82

## Situation

The `/topology` page (introduced in #58) implemented hover focus-fade by spreading
every React Flow `Node` / `Edge` into a fresh object on each hover move and writing
the dim opacity onto the `style` prop. Because React Flow treats its node/edge
entries by reference, replacing every item every time forced the internal diff to
touch every node, invalidated `React.memo` on `AgentNode` / `MachineNode`, and
applied the opacity change instantly (no CSS transition). The net effect was a
perceptible flicker as the user moved between neighbouring nodes — the exact
symptom reported in issue #82.

## Task

- Stop re-creating unchanged node/edge objects on hover so React Flow and the
  memoised custom node components skip redundant work.
- Smooth the opacity transition so brief hover moves do not read as a visual
  flash.
- Preserve every existing behaviour: hover focus set, selection ring, double-click
  navigation to `/rooms/<id>`, `Fit view`, minimap toggle, and Escape to clear.
- Scope the new CSS so it cannot leak to any other `@xyflow/react` usage in the
  app.

## Action

- Added `packages/cluster/frontend/src/components/topology/topology.css` with a
  180ms `opacity` transition on `.react-flow__node` / `.react-flow__edge` and an
  `.is-dimmed { opacity: 0.18 }` rule, both scoped under `.topology-root`.
- `packages/cluster/frontend/src/components/topology/TopologyCanvas.tsx`:
  - Imported the new CSS immediately after `@xyflow/react/dist/style.css`.
  - Added `className="topology-root"` on the outer container `<div>` so the new
    selectors activate only inside this canvas.
  - Replaced `displayNodes` and `displayEdges`: compute the target className
    (`'is-dimmed'` or `undefined`) from `hoverId` + `neighborNodes` /
    `neighborEdges`; return the original object reference when the class is
    unchanged, and only spread into a new wrapper when it flipped.
  - Rewrote `withSelected` with the same identity-preserving pattern: only
    the node whose `selected` flag changes gets a fresh wrapper.
  - Removed the now-unused `DIMMED_OPACITY` constant and the prior `style.opacity`
    assignments.

No changes in `useGraphLayout.ts`, `nodes/*.tsx`, `edges/RelationEdge.tsx`, or
`TopologyPage.tsx` — the custom nodes never read `style.opacity`, so the move to a
wrapper-level class is transparent for them.

## Decisions

Rationale sourced from `.tmp/plan-82-topology-hover-flicker.md` §3.2 which
weighed four approaches:

- **① CSS transition only** — leaves the per-hover full-array spread, so
  `React.memo` still tears down every node. Fixes the visual flicker but not the
  underlying CPU work. Rejected.
- **② Identity-preserving useMemo only** — drops the needless renders but
  retains `style.opacity`, which has no transition and so still flashes when
  hover targets change quickly. Rejected — fixes one axis, not both.
- **③ Pure CSS via attribute selector on the container** — would require
  emitting a dynamic `<style>` block whose body depends on the current neighbour
  set, which is more fragile than the official React Flow `className` field.
  Rejected.
- **④ CSS class + identity-preserving useMemo** (chosen) — uses the public
  React Flow `className` API, transitions opacity in the browser, and eliminates
  redundant diff work in one change. Two small files, one code path.

Assumptions worth revisiting if this issue recurs:
- `dagre` layout results remain stable enough that `useGraphLayout` returns the
  same `data` references across polls — if that degrades, memoised nodes will
  re-render anyway and this fix would only cover the visual flash half.
- `@xyflow/react` continues to honour `className` on both `Node` and `Edge`
  objects.
- ETag polling of `/api/v1/graph` is out of scope; if users see flicker during
  data refresh rather than hover, that is a separate issue.

## Result

- `cd packages/cluster/frontend && npm run build` passes (tsc + vite), 2451
  modules transformed, no type errors.
- Hover now uses a browser opacity transition; items whose dim / selected state
  did not change keep their object reference, so React Flow's internal diff and
  the memoised `AgentNode` / `MachineNode` skip work on hover moves between
  neighbours.
- Existing behaviour (selection ring, double-click to room, `Fit view`, minimap
  toggle, Escape) is preserved — touched code paths are limited to the three
  `useMemo` blocks and the container `className`.
- No new tests — the change is presentational and the repo has no vitest
  infrastructure for `/topology` yet; verification is the production build plus
  manual check per plan §4 step 5.
