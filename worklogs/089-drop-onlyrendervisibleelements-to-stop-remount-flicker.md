# fix(topology): drop onlyRenderVisibleElements to stop remount flicker (#89)

- Commit: `9a49750` (9a49750ce1923dfa8f9005e7b51bdb1c170de611)
- Author: Changyong Um
- Date: 2026-04-17T08:01:09+09:00
- PR: #89

## Situation

Issue #82 fixed hover-time flicker in `/topology` by switching wrapper dimming to a `className` toggle with a CSS `opacity` transition. That fix held when the viewport was stable, but a second flicker path surfaced: after clicking a Room node, the DetailPanel opened as a flex sibling and shrank the canvas width. React Flow's internal `ResizeObserver` then re-evaluated node visibility, and the `onlyRenderVisibleElements` option unmounted any node that crossed the new viewport edge. When the user subsequently hovered, nodes at the boundary would remount — the CSS transition restarted from its initial opacity, producing the flicker the original fix was supposed to eliminate.

## Task

- Stop the remount-triggered flicker when DetailPanel (or any other resize) changes the canvas width.
- Preserve the `className`-based dimming pipeline introduced in #82 — do not revert or rework it.
- Keep build and existing tests green; ensure hover, selection, double-click navigation, and MiniMap toggle all continue to work.

## Action

- `packages/cluster/frontend/src/components/topology/TopologyCanvas.tsx:153` — removed the `onlyRenderVisibleElements` boolean prop from `<ReactFlow>`. No other flags touched; `fitView`, `minZoom`, `maxZoom`, `proOptions` are unchanged.
- Verified `npm run build` (tsc + vite) and `npm test` (vitest, 100/100) still pass. No test had to be updated because the removed prop is purely a render-optimization knob; there is no behavioral test coupled to it.

## Decisions

Considered three options in the triage before landing this fix:

1. **Remove `onlyRenderVisibleElements`** — drops the culling optimization at the cost of rendering all nodes regardless of viewport. One-line change. **Chosen.**
2. **Convert DetailPanel from flex sibling to absolutely-positioned overlay** — keeps culling, but requires reworking `TopologyPage` layout (including mobile breakpoints) and introduces separate concerns around z-index and click-through. Deferred as a larger follow-up if needed.
3. **Keep culling and tune `fitView` / transition timing to hide the remount** — only masks the symptom, still vulnerable to window resize, MiniMap toggle, or any other width change.

Decisive factor: the current topology graphs are well under the scale where culling is measurable (tens to low hundreds of nodes; `onlyRenderVisibleElements` mostly matters past ~500). Removing the flag cleanly severs the root cause — DOM churn on resize — and closes every other resize-triggered flicker path at the same time (window resize, MiniMap toggle). Option 2 was rejected for scope; option 3 was rejected because it leaves the same bug waiting to reappear the next time the layout changes.

Assumption to revisit: if the graph grows past the point where rendering every node hurts pan/zoom fluidity, culling should come back, but paired with the overlay DetailPanel (option 2) so culling never has to fight a canvas-width change.

## Result

- DetailPanel-open hover no longer flickers — nodes stay mounted across canvas resize, so the CSS opacity transition is never reset by remount.
- Build clean; test suite 100/100 green on the feature branch.
- Follow-up documented: if node counts grow significantly, consider reinstating `onlyRenderVisibleElements` together with an overlay-style DetailPanel layout.
