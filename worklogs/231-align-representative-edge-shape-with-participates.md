# fix(topology): align representative edge shape with participates (#231)

- Commit: `3f395ad` (3f395ad3a770d1dcdb87ce8edd649ec05b9a0e3e)
- Author: Changyong Um
- Date: 2026-04-22T00:23:52+09:00
- PR: #231

## Situation

#228 folded the standalone `represents` edge into the `participates` kind with a boolean `is_representative` flag, finishing the data-model unification started in #226. The visual layer still carried the pre-merge `represents` styling, though — representative agent→room edges rendered as `smoothstep + solid 2px Notion Blue`, while every other `participates` edge rendered as `straight + dashed 1px`. Same `kind` in the API, two different shapes on screen, so the "representative is just participates with a role flag" story only held in the JSON.

## Task

- Collapse the visual distinction between representative and non-representative `participates` edges so a single `kind` reads as a single shape.
- Preserve a visible cue that the representative is special — color-only per the user directive ("색상만 다르고"), reusing the existing `ACCENT` / `ACCENT_SOFT` pair so no new design tokens appear.
- Keep backend, API schema, and existing tests untouched; the change is frontend rendering only.

## Action

- `packages/cluster/frontend/src/components/topology/constants.ts:80-101` — removed the dedicated `if (isRepresentative)` branch that returned `{ stroke: ACCENT, strokeWidth: 2, type: 'smoothstep' }`. The agent-participates block now returns one style object whose `stroke` is a ternary: `isRepresentative ? ACCENT : ACCENT_SOFT`. `strokeWidth`, `strokeDasharray: '3 3'`, and `type: 'straight'` are shared by both cases.
- `packages/cluster/frontend/src/components/topology/edges/RelationEdge.tsx:14-20` — rewrote the JSDoc dispatch table entry for `participates`: was "smoothstep + Notion Blue 2px for representative … straight + dashed otherwise", now a single line describing straight+dashed with color-only differentiation and a `#231` back-reference.
- No other files touched. `strokeWidth: 2` no longer appears anywhere in the topology edge style function.

## Decisions

Four approaches were weighed in `.tmp/plan-231-topology-representative-edge-shape-unify.md`:

- **A (chosen)** — straight + dashed + 1px, `stroke` via ternary. Smallest diff, matches user wording verbatim, satisfies the "same kind → same shape" principle.
- **B** — same as A but with `strokeWidth: 1.5` for the representative. Rejected because "색상만 다르고" was explicit; width-bumping reintroduces a second axis of differentiation the user asked to remove. Kept as a one-line fallback if the color contrast later proves too subtle.
- **C** — keep the current `smoothstep + solid 2px`. Rejected: that is the state the user explicitly asked to change.
- **D** — keep the old style but add an SVG `marker-mid` (★ or similar) on the representative edge. Rejected because `RoomNode` already stamps a star icon for the representative at the node level, so an edge-level marker would duplicate the signal and cost far more complexity (React Flow marker positioning, hover-opacity interactions).

Decision tipped on the fact that the user phrased the desired change as a shape unification, and #226/#228 had already committed to "one kind, one relation" at the data layer — A is the smallest change that lets the visual layer agree with that commitment. Assumption to revisit: that the `ACCENT` vs `ACCENT_SOFT` (50% alpha) contrast on a thin dashed line is strong enough to pick out the representative. If that assumption breaks, swap to B (+0.5px width, one-line edit) rather than reintroduce `smoothstep` or markers.

## Result

- `cd packages/cluster/frontend && npm run build` → tsc + vite clean (9.33s, no errors).
- `cd packages/cluster && uv run pytest tests/test_graph_api.py -v` → 16/16 pass; API payload shape unchanged, so no test updates required.
- Built bundle (`TopologyPage-*.js`) grep confirms `strokeDasharray:"3 3"` with both `"#0075de"` and `"#0075de80"` present, `strokeWidth: 2` is gone from the edge styling (the one remaining occurrence is a lucide icon's `strokeWidth` prop, unrelated).
- Manual browser verification on the test server deferred until this branch is deployed there; no live backend available from the worktree to drive a full Playwright run.
