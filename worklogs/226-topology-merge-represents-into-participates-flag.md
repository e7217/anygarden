# refactor(topology): merge represents edge into participates flag (#226)

- Commit: `20030d0` (20030d04c6f3ebbd5826374dee52db6ed0397b98)
- Author: Changyong Um
- Date: 2026-04-21T22:37:36+09:00
- PR: #226

## Situation

The topology view (`/topology`) drew two separate edges on every
agent→room pair where the agent was the room's representative: a
`participates` edge (dashed, semi-transparent Notion Blue 1px) and a
`represents` edge (solid Notion Blue 2px). Because dagre computes the
same path for identical source/target pairs, React Flow stacked the two
lines on top of each other, producing visual noise and making
hover-focus dim logic awkward. The API schema also carried the
duplication — `EdgeKind` advertised five kinds even though `represents`
added no information beyond a flag on the agent→room line.

## Task

- Drop `represents` as a distinct edge kind on both the backend
  `/api/v1/graph` payload and the frontend `EdgeKind` union.
- Fold the representative relation into the agent-flavored
  `participates` edge as `data.is_representative: bool`.
- Preserve the existing visual signal (Notion Blue 2px smoothstep) for
  the representative line — it still reads as "this agent speaks for
  the room" per DESIGN.md §2's single-accent rule.
- Keep the test suite honest: assert that `represents` no longer
  surfaces as a kind and that the flag arrives on the right edge with
  the right boolean value.

## Action

- `packages/cluster/doorae/api/v1/graph.py`
  - Shrank `EdgeKind` Literal from 5 members → 4 (dropped
    `"represents"`).
  - Built `rep_by_room: dict[str, str]` before the participants loop
    from `Room.representative_agent_id`.
  - In the agent branch of the participates build, set
    `data={"actor": "agent", "is_representative": rep_by_room.get(p.room_id) == p.agent_id}`.
    Explicitly emits `False` for non-representative agents for
    consistent payload shape.
  - Removed the dedicated `# represents:` block (previously L590-608)
    that built agent→room edges from `Room.representative_agent_id`.
- `packages/cluster/tests/test_graph_api.py`
  - Removed `"represents"` from `valid_edge_kinds` and added a negative
    assertion that the kind no longer appears anywhere in the payload.
  - Replaced the old `represents_pairs` assertion with: (1) a
    `rep_edges` filter on `kind=participates`, `actor=agent`,
    `is_representative=True` asserting the expected
    `(a_a2, r_r3)` pair, (2) a boundary check that at least one
    agent participant carries `is_representative=False`.
- `packages/cluster/frontend/src/components/topology/types.ts`
  - Removed `'represents'` from `EdgeKind` union.
  - Added `is_representative?: boolean` to `GraphEdge.data` with a
    comment pointing back to this change.
- `packages/cluster/frontend/src/components/topology/constants.ts`
  - Extended `edgeStyleFor(kind, actor, isRepresentative)` signature.
  - Inside `case 'participates'`, the flag short-circuits to
    `{ stroke: ACCENT, strokeWidth: 2, type: 'smoothstep' }` — the
    exact return value the `case 'represents'` branch used to produce.
  - Removed the `case 'represents'` branch entirely.
- `packages/cluster/frontend/src/components/topology/edges/RelationEdge.tsx`
  - Extracted `const isRepresentative = Boolean(data?.is_representative)`
    and forwarded it to `edgeStyleFor`.
  - Refreshed the JSDoc table to document the new dispatch rule.
- `packages/cluster/frontend/src/components/topology/nodes/RoomNode.tsx`
  - Updated the stale header comment that referenced the removed
    `represents` edge — now points to `data.is_representative`.

`useGraphLayout.ts` needed no change; `rfEdges.map` already spreads
`...(e.data ?? {})` so the flag propagates into React Flow edge data
without a dedicated wiring step.

## Decisions

The plan at `.tmp/plan-226-topology-represents-merge-into-participates.md`
weighed three options:

- **A (chosen): Backend owns the flag** — one agent→room edge with
  `data.is_representative`. API shrinks by one kind, clients have a
  single line to reason about, extension to filters like
  "Representative only" becomes a property check.
- **B: Frontend merges on the client** — leave the API shape alone,
  have `useGraphLayout` deduplicate `represents` into a flag on the
  matching `participates` edge. Rejected because it leaves the
  contradictory "two edges for one relationship" contract at the API
  boundary. Any future client (CLI viz, external dashboard, tests)
  would have to reimplement the merge, and `test_graph_api` would
  still have to assert the duplicated shape.
- **C: CSS z-index stacking** — render `represents` above
  `participates` so the overlap is hidden. Rejected because the two
  edges still exist for hit-testing and hover opacity; the problem is
  the data model, not the paint order.

The decisive tipping point is a comment already in `graph.py`
explaining that the `represents` edge direction had been flipped from
room→agent to agent→room *specifically to avoid the smoothstep
horseshoe that overlapped `participates`*. That flip reduced the
visual damage but could never eliminate it: identical source/target
pairs route identically under dagre. Only a schema change removes the
duplication at its source.

Assumption worth flagging on revisit: `Room.representative_agent_id`
is expected to point at an actual participant in that room. If a
future scheduler bug ever assigns a representative who isn't in
`RoomParticipant`, the flag has no edge to attach to and the
representation disappears from the graph silently. The old
`represents` edge had the same blind spot (it also didn't verify
membership), so this is not a regression — but a sanity warning on
the topology endpoint would be a reasonable follow-up if the case ever
materializes.

The emit-`False`-explicitly choice (rather than omitting the flag for
non-representatives) keeps frontend code as `Boolean(data?.is_representative)`
regardless of payload shape and gives the test suite a positive
assertion surface. The cost is a handful of extra bytes per payload.

## Result

- 16/16 tests in `tests/test_graph_api.py` pass, including the new
  negative assertion for `represents` and the new
  `is_representative=True/False` assertions.
- Frontend `tsc -b && vite build` passes with no type errors.
- `rg "represents" packages/cluster/` returns only intentional
  references: explanatory comments pointing back to this change in the
  frontend/backend, plus unrelated prose in `scheduler/lifecycle.py`,
  `db/models.py`, and `ManifestPanel.tsx` that uses "represents" in a
  generic English sense. No stale edge-kind references remain.
- The `/topology` view now draws one line per agent→room relationship;
  the representative line is Notion Blue 2px smoothstep, non-rep
  agents stay on the dashed semi-transparent blue.

Pending: a `FilterPanel` toggle for "Representative only" becomes a
one-liner now that the relationship is a property rather than a kind,
but it's explicitly deferred to a follow-up issue.
