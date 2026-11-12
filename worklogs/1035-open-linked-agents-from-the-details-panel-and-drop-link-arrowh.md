# fix(agent-links): open linked agents from the details panel and drop link arrowheads (#1035)

- Commit: `b5a221c` (b5a221c7be782c33e31653705f431cd797d23cc6)
- Author: Changyong Um
- Date: 2026-11-13T08:44:23+09:00
- PR: #1035 (issue)

## Situation

When an agent was selected on the Overview map, the lower part of the details panel did not behave as it looked:

- The reach list ("Talking to X can involve") used teal outline pills that look like buttons, but clicking them did nothing. They also repeated what the map already highlights.
- In "Can ask / Asked by", clicking a name selected the *link* and opened the link details (delete or add reverse), not the agent.

On the map, the input port looked different from the output port. Both are React Flow's default 6px handle; the resting link's arrowhead touched the input dot and read as one `→•` shape.

## Task

- Replace the reach pills with a single summary line.
- Clicking a linked agent's name selects that agent and pans the map to it, keeping the zoom.
- Each row gets a delete button that removes the link after confirming.
- Make both ports read the same. Keep direction readable, and keep the reduced-motion state of running questions clear.

## Action

- `frontend/src/pages/AgentLinksPage.tsx`:
  - `AgentDetails` now shows a summary (`reachSummary` or `reachNone`) instead of the pills.
  - Each row is a name button that calls `onOpenAgent` (with a chevron) plus an `X` icon button that calls `onDeleteLink`, which reuses `askDelete` and its confirmation.
  - The page keeps a `focusAgent {id, seq}` and passes it to the canvas.
- `AgentLinksCanvas.tsx`:
  - New `focusAgent` prop: `fitView` on that node with min and max zoom set to the current zoom, so the map pans without zooming.
  - `markerEnd` and the `MarkerType` arrow helper are removed.
- `agentLinks.css`:
  - Edge width goes from 1.25 to 1.5px (2px when emphasized).
  - The `marker-end` rules are removed. Under reduced motion a running question stays a 2px solid line.
  - The source port's `::after` hit area is 24px with any pointer and 44px on touch.
- i18n:
  - Added `reachSummary` and `deleteIncoming`.
  - Removed `reachTitle` and `reachHint`.
- Tests:
  - vitest (`AgentLinksPage.test.tsx`): summary line, opening a linked agent (canvas focus), and deleting from a row.
  - e2e (`agent-links.spec.ts`): the arrowless resting link reaches the target port, the reduced-motion running line is solid and 2px, and a panel row opens an agent and offers deletion.
- Docs: COL-14 and COL-19 in `docs/requirements.md`, and `docs/agent-links.md`.

## Decisions

- **Reach list:**
  - Clickable pills were rejected: they duplicate "Can ask" for direct links.
  - Grey text was rejected: it still duplicates the map.
  - A one-line count was chosen. The map's highlight already names the agents, and the hint text said so.
- **Name click:** opening the agent replaced selecting the link. Users expected navigation. Link deletion stays available from the row button and from clicking the edge.
- **Pan:** `fitView({nodes, minZoom = maxZoom = current})` was chosen over `setCenter`. React Flow already knows the node's box, so nothing has to be computed, and the zoom is kept.
- **Arrowheads:** compared in a mockup with options A–F.
  - Rejected: a 4px gap on the input side only, which left the two ports looking different, and a gap on both sides, which made lines float off their ports.
  - Chosen by the user: no arrowheads, as in Langflow. A link always leaves the asker's right port and enters the answerer's left port, including the lower return lane, so the side it meets gives its direction.
  - Taken from Langflow: a thicker line and a larger grab area.
  - Not taken: type-coloured 10px handles, because links carry no data types.
- **Assumption:** two-way links stay distinguishable because the return link uses a separate lower lane. Revisit if people misread direction.

## Result

- The details panel now navigates the graph and manages links. Both ports look the same, and no arrowheads are drawn.
- Tests:
  - vitest: page, agent-links components and lib, 336 passed
  - e2e `agent-links.spec.ts`: 22 passed
  - `npm run build` OK
- No server changes.
- Not done: on mobile, where the panel sits under the map, panning happens off screen.
