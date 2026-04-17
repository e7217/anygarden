# fix(topology): flip represents edge to agent→room for TB flow

- Commit: `ba5181b` (ba5181b259379cdddcf5833ad8d4534f8e95b85d)
- Author: Changyong Um
- Date: 2026-04-17T12:16:05+09:00
- PR: —

## Situation

The topology graph view lays nodes out top-to-bottom via dagre (`rankdir: 'TB'`): User → Machine → Agent → Room. All edge kinds followed that flow downward *except* `represents`, which was emitted as `room → agent`. Because every node only exposes `target=Top`/`source=Bottom` handles (`AgentNode.tsx:99-121`, `RoomNode.tsx:68-112`), the smoothstep path for this one edge had to leave the Room's bottom, arc downward, loop back up above the Agent, then drop into the Agent's top — a horseshoe detour that overlapped the `participates` edge on the same agent/room pair and made the graph visually noisy.

## Task

- Remove the reverse-flow smoothstep detour for `represents` edges.
- Keep the edge (and the existing ⭐ star on `RoomNode` that tags representative rooms); both channels stay live.
- Do not touch other edge kinds, handle layouts, or the dagre configuration.
- Keep the backend API payload shape unchanged — only swap `source`/`target` values for `represents`.

## Action

- `packages/cluster/doorae/api/v1/graph.py:587-608` — in `_assemble_edges`, swapped the `represents` edge so `source=_aid(r.representative_agent_id)` and `target=_rid(r.id)`. Added a comment explaining the TB-flow rationale and the prior detour it fixes.
- `packages/cluster/tests/test_graph_api.py:318` — flipped the asserted pair from `(r_r3, a_a2)` to `(a_a2, r_r3)` to match the new direction.
- `packages/cluster/frontend/src/components/topology/nodes/RoomNode.tsx:18-20` — updated the component docstring to note the edge now flows agent → room.

## Decisions

Three options were weighed for removing the visual noise:

1. **Drop the `represents` edge entirely** and rely on the ⭐ star alone. Cheapest visually, but removes one channel of information and changes the API payload shape in a way downstream consumers would have to track.
2. **Flip the edge direction to `agent → room`** (chosen). Three-line backend swap, no API shape change, both the ⭐ and the edge remain, and the resulting flow is strictly downward. Semantically reads "agent represents room", which matches how a representative relationship is naturally phrased even though the FK lives on `Room.representative_agent_id`.
3. **Switch to floating-edges** so handle position stops driving path shape. Fixes the symptom generically but is a ~150 LOC rewrite of `RelationEdge` plus all node Handle wiring, and risks regressing the other edge kinds that look fine today.

Option 2 tipped because it is the smallest change that preserves both visual channels while aligning the one outlier with the rest of the layout. Option 1 was rejected because the ⭐ is subtle enough that users scanning the whole topology benefit from a connecting line; option 3 was rejected as disproportionate investment for a single-edge problem.

Assumption worth revisiting: if future edge kinds also need to flow against the TB rank order (none today), the floating-edge approach becomes more attractive than flipping each one individually.

## Result

- `represents` edges now render as a single downward smoothstep with no horseshoe detour; they no longer overlap `participates` edges on the same agent/room pair.
- `uv run pytest packages/cluster/tests/test_graph_api.py` — 16 passed.
- `npm run build` in `packages/cluster/frontend` — TypeScript check and Vite build succeed.
- API response keeps its `{id, source, target, kind}` shape; only the values on `represents` rows changed.
