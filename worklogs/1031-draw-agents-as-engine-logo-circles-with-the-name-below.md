# feat(agent-links): draw agents as engine-logo circles with the name below (#1031)

- Commit: `026cee2` (026cee260265e1495f0f65d829d2fa6173019663)
- Author: Changyong Um
- Date: 2026-11-12T17:40:24+09:00
- PR: #1031 (issue)

## Situation

On the Overview map (`/agents/links`) each agent was a slim card whose width came from its outgoing links (`agentNodeSize`: `96 + 8n`px, capped at 4). A card with no links had about 72px for its name, so most names were cut to `…`, and names sharing a prefix (`backend-code-reviewer-01` / `-02`) became indistinguishable. One width carried both "room for the name" and "more links means bigger".

## Task

- Separate the name from the size signal: draw agents as circles holding the engine logo, with the name under the circle on up to two lines.
- Circle diameter grows 5% per distinct outgoing link, with no cap.
- Show state through the outline, never color alone: aurora ring while answering (still under reduced motion), dashed outline and faded logo when unavailable, filled outline when selected.
- Move the in-card status line and room link into a hover/selection card, and keep them in the accessible name.
- Keep ports, drop-anywhere linking, auto-arrange and the saved layout working.

## Action

- API: `AgentBriefOut.engine` added and filled from `Agent.engine` (`packages/cluster/anygarden/api/v1/agent_links.py`), with `test_agents_carry_their_engine` (`packages/cluster/tests/test_agent_links.py`).
- `agentNodeSize` replaced by `agentNodeDiameter` (`40 × (1 + 0.05n)`, uncapped) and `agentNodeBox` (layout space = circle plus a 124 × 40px name area) in `frontend/src/lib/agentLinks.ts`. `LinkAgent.engine` added. Unit tests updated.
- `AgentLinksCanvas.tsx`: the node renders an `EngineGlyph` circle, a name label, an aurora ring when busy, and a `NodeToolbar` card (name, engine, status, room button) shown on hover or selection. dagre lays out `agentNodeBox` so names never overlap. The node's aria-label appends the status text. `fitView` bottom padding grows to 64px for the last row's names.
- `agentLinks.css`: the card rules are replaced with circle, label, aurora (conic gradient masked to a ring, rotating 2.4s, stopped under reduced motion), unavailable, selected and hover-card rules.
- `index.css`: `--color-aurora-1..4` tokens for light and dark.
- e2e (`agent-links.spec.ts`): name assertions now target `.agent-link-node__name`; status is checked in the hover card and aria-label; the reference-width test asserts the full name instead of a `title`.
- Docs: COL-14 and COL-19 in `docs/requirements.md` and `docs/agent-links.md`.

## Decisions

- Options weighed for the name: content-fit card width, fixed width with two-line wrap, middle ellipsis, zoom-dependent labels, and name outside the node. Mockups compared all of them, and the user chose the name outside a circle. It separates the size signal from the name space by structure, so names stop being cut because of link count.
- Size: an earlier cap at 4 links (+20%) was dropped at the user's request, so the circle grows 5% per link with no limit. A 5% step is 2px at 40px, so neighbouring steps are hard to tell apart. This was accepted.
- Answering: the options were a spreading ring (the sidebar's existing signal), a teal aurora rotation, a multicolour Google-style aurora, and an aurora-coloured spreading ring. The teal aurora was chosen because the circle holds brand-coloured logos (Claude orange, Gemini blue) and a multicolour ring would blur into them. It also keeps the quiet-teal palette. The trade-off is that the sidebar still uses the spreading ring.
- Selection stays still rather than pulsing: motion reads as "in progress", and selecting an answering agent would stack two animations.
- Hover card: React Flow's `NodeToolbar` over a hand-placed div or a new popover dependency. It needs no new package, stays the same size at any zoom, and tracks the node.
- Assumption: the 40px name area (two 12px lines) fits typical names. Revisit if long localized names wrap badly. Saved per-user positions are top-left based and need no migration. "Auto-arrange" fixes any crowding.

## Result

- Names show in full up to two lines under each agent, and same-prefix names stay distinguishable.
- Circle size reflects outgoing links. The answering, unavailable and selected states read from the outline, and the room link opens from the hover/selection card.
- Tests: `tests/test_agent_links.py` 20 passed, vitest (agent-links, lib, AgentLinksPage) 331 passed, e2e `agent-links.spec.ts` 20 passed, `npm run build` OK.
- Pending: per-agent alert badges (#1032) build on this node.
