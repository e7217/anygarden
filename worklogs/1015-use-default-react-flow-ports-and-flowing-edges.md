# fix(agent-links): use default React Flow ports and flowing edges (#1015)

- Commit: `bcbcfba` (bcbcfbae58b6d09078d9953cbdf6faae2954a375)
- Author: Changyong Um
- Date: 2026-11-11T01:38:10+09:00
- PR: #1015

## Situation

The agent relationship map implemented connection handles with custom CSS — the source port was hidden behind a 1px transparent anchor and only appeared on hover/focus/selection, while the target port stretched invisibly across the entire card. This meant the two ends of an edge had different visual treatments: the source was a small dot that appeared and disappeared, and the target was an invisible full-card area. The edge's arrowhead sat on top of the target port and caused visual collision on drop. Additionally, there was no direction cue on the edge itself; direction was implied only by the arrowhead.

## Task

- Replace the custom source/target handle implementation with React Flow's default handles.
- Both handles must be always-visible and symmetrically styled using theme tokens only.
- A drop anywhere on a card (not just on the small port) must still create a link, via `onConnectEnd`.
- The self/duplicate link check must be shared between `onConnect` and `onConnectEnd`.
- Edges must show direction via flowing dashes; under reduced motion, a solid line with an arrowhead carries direction instead.
- Bezier curvature should be tuned so edges arrive level at each card rather than hooking at the end.

## Action

- **`AgentLinksCanvas.tsx`**: Removed `useConnection` import and usage. Simplified `LinkAgentNode` to render plain `<Handle type="target" ... />` and `<Handle type="source" ... />` without custom class names or connection state. Added `nodeIdAt(event)` helper to resolve the card under the pointer on a touch or mouse event. Extracted the duplicate/self-link guard into `canLink()`. Added `onConnectEnd` callback (`AgentLinksCanvas.tsx:160`) that fires `onConnect` when a drag ends on a card but not on its port. Set `animated: true` on every edge object (`AgentLinksCanvas.tsx:135`) to enable the flowing-dashes class. Increased bezier `curvature` to `0.5` (`AgentLinksCanvas.tsx:50`).
- **`agentLinks.css`**: Removed all `.agent-link-node__source` and `.agent-link-node__target` rules (the hidden anchor, pseudo-element dot, full-card invisible target, hover/focus/selection opacity rules). Replaced with two overrides on `.react-flow` that map React Flow's documented handle defaults (`--xy-handle-background-color-default` → `--color-foreground`, `--xy-handle-border-color-default` → `--color-surface-elevated`), keeping the default 6px filled dot; a valid drop port fills with `--color-brand-text`. Slowed the dash animation to 1s. Keyboard focus on an edge now reads through stroke width (2.5) instead of a dash pattern, since every edge is dashed. Added `@media (prefers-reduced-motion: no-preference)` rule to suppress the arrowhead (`marker-end: none`), and a `@media (prefers-reduced-motion: reduce)` rule to stop the dash animation and restore a solid line. Kept the coarse-pointer `::after` hit-expansion rule for the source port on touch devices.
- **`agent-links.spec.ts`**: Updated all handle selectors from `.agent-link-node__source` / `.agent-link-node__target` to `.react-flow__handle.source` / `.react-flow__handle.target`. Changed opacity assertions from `0` to `1` for the always-visible handles. Added two new tests: `[COL-14] dropping exactly on the target port saves the link once` (verifies `onConnectEnd` deduplication) and `[COL-14] flowing dashes show direction, with a solid arrow under reduced motion` (verifies `animated` class, `animation-name: dashdraw`, and reduced-motion fallback with `marker-end`).
- **`docs/requirements.md`**: Updated COL-14 requirement text to reflect that ports are always visible ("카드 양쪽의 작은 연결점은 항상 보이고") and that a drop anywhere on the card creates a link.

## Decisions

- **Default handles over custom anchor**: The 1px hidden anchor approach caused the two ends of an edge to look and behave asymmetrically. React Flow's default handles are always visible and identical on both ends; edges attach at each port's outer side (React Flow's default anchoring), so nothing shifts on hover. An intermediate version kept #1011's 8px hollow teal ring, but it read as the old hidden port rather than the documented default, so only the two colour variables are overridden.

- **`onConnectEnd` for card-body drops, shared `canLink` guard**: React Flow calls `onConnect` when a drag ends on a valid port, and `onConnectEnd` after every drag. The guard checks `state.isValid`, not `state.toHandle`: a drop near a card's right edge lands on that card's own source port, which React Flow reports as an invalid `toHandle`. Rather than duplicating the guard, `canLink` is extracted and called from both. If `state.isValid` is true (port hit), `onConnectEnd` returns early to prevent a double-create.

- **Flowing dashes for direction, arrowhead only under reduced motion**: An arrowhead placed on the target port was colliding with it visually. Flowing dashes communicate direction without occupying the same point. Users who prefer reduced motion lose the animation cue, so the CSS restores a solid line with an arrowhead for them. Alternatives discussed were hiding the target port except while connecting, or a filled arrowhead (reverting #1011's open arrow); the user preferred dashes and keeping the target port visible.

- **`curvature: 0.5`**: The default bezier curvature produced a noticeable hook as edges approached cards at an angle. 0.5 makes the control points further from the source/target, keeping the final segment more horizontal. Increasing dagre `ranksep` would have had a similar effect but widens the map.

This reverses #1011's user-chosen hidden ports, so COL-14 was rewritten in the same change.

## Result

- Source and target ports are now always-visible small circles on the right and left card edges respectively, symmetrically styled.
- Dropping anywhere on a card creates a link; dropping on the port also works; duplicate/self-link drops are silently ignored.
- Edges show flowing dashes in normal-motion mode; reduced-motion mode shows a solid line with an arrowhead.
- Bezier edges arrive approximately level at each card for typical canvas layouts.
- Two new e2e tests cover the port-drop deduplication and reduced-motion edge direction. Verified: `agent-links.spec.ts` 17/17, agent-links Vitest 8/8, `npm run build`; light/dark screenshots compared.
- Known: where several edges leave one port, their first dashes overlap into a small blob at the start; left as is.
- COL-14 requirement text updated to match new behavior.
