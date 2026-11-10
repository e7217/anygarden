# feat(agents): ask linked agents from any room, at any depth (#1006)

- Commit: `84c2710` (84c271010891206fff20d2f29306bc7debd6f321)
- Author: Changyong Um
- Date: 2026-11-10T10:52:03+09:00
- PR: #1006

## Situation

Before this change, an agent could only ask peers already seated in the same room. This forced teams with different specialisations into the same room or into awkward sub-rooms, and it meant every room had to be manually configured with the right set of agents. There was no way to share a specialist agent across rooms without duplicating its membership everywhere.

## Task

Introduce a global, one-directional agent link system (A → B, with a "when to ask" label) so that any agent can ask any linked agent regardless of room membership. The `ask_peer` tool must accept an `agent_id` for a linked agent and seat the absent agent for the thread only. Linked participants must stay invisible to member lists, `@everyone`, the one-agent rule, and the room representative. Peer chains can now go arbitrarily deep; cycle detection must refuse asking an agent that is already answering higher up the same chain. Humans must be able to see and cancel running chains. Requirements COL-14..COL-17.

## Action

**DB / migration (`105_agent_links`)**
- `packages/cluster/anygarden/db/migrations/versions/105_agent_links.py`: new `agent_links` table (unique pair constraint, self-link check constraint, index on `from_agent_id`); added `participants.joined_via` column; added `peer_ask_groups.parent_target_id` for chain tracking.
- `packages/cluster/anygarden/db/models.py`: `AgentLink` ORM model; `joined_via` field on `Participant`.

**Orchestration layer**
- `packages/cluster/anygarden/orchestration/agent_links.py` (new): `visible_member()` SQL filter, `link_exists()`, `links_by_agent()`, `room_agent_links()`, `seat_linked_agent()`. Defines `JOINED_VIA_LINK = "link"` and `MAX_WHEN_TO_ASK_CHARS = 300`.
- `packages/cluster/anygarden/orchestration/peer_chains.py` (new, 281 lines): `list_chains()` builds the call-tree JSON the UI renders; `cancel_chain()` stops every open turn in a chain and returns cancelled turns for WebSocket broadcast.
- `packages/cluster/anygarden/orchestration/peer_fanin.py`: extended fan-in to wait for a peer's own sub-peers before settling; added `awaiting_group_id` logic so a question waits for its own peer group before propagating the answer upward.
- `packages/cluster/anygarden/orchestration/rules.py`: `ask_peer_max_depth()` and `ask_peer_max_calls()` env-driven caps (`ANYGARDEN_ASK_PEER_MAX_DEPTH`, `ANYGARDEN_ASK_PEER_MAX_CALLS`); unlimited by default.
- `packages/cluster/anygarden/mcp/tools.py`: `ask_peer` schema extended — `agent_id` field added, `participant_id` made optional, description updated. Implementation now resolves `agent_id` via link check, calls `seat_linked_agent`, detects chain cycles, and enforces optional depth/call caps.
- `packages/cluster/anygarden/rooms/membership.py`: `visible_member()` filter applied throughout; linked participants excluded from member lists, `@everyone` expansion, one-agent rule and representative selection.
- `packages/cluster/anygarden/rooms/roster.py`: links included in roster broadcast payload.

**API**
- `packages/cluster/anygarden/api/v1/agent_links.py` (new, 228 lines): `GET/POST /api/v1/agent-links`, `PATCH/DELETE /api/v1/agent-links/{id}` — CRUD for links, auth-gated to signed-in users. `GET /api/v1/rooms/{id}/peer-chains` and `POST .../peer-chains/{chain_id}/cancel`.
- `packages/cluster/anygarden/app.py`, `rooms/router.py`, `messages/router.py`, `ws/handler.py`, `ws/protocol.py`: router registration and WebSocket protocol additions for `LinkBrief`.

**Frontend**
- `src/pages/AgentLinksPage.tsx` + `AgentLinksCanvas.tsx` + `LinkEditorDialog.tsx` + `agentLinks.css`: relationship map at `/agents/links`; interactive canvas for creating/editing/deleting links.
- `src/components/agent-settings/AgentLinksPanel.tsx`: links list in agent settings dialog.
- `src/components/peer-chains/PeerChainProgressCard.tsx`, `PeerChainTree.tsx`, `PeerChainGraph.tsx`, `PeerChainStopButton.tsx`, `PeerMessagePath.tsx`, `PeerStateBadge.tsx`: progress card in main chat and call tree with path labels in thread panel.
- `src/hooks/useAgentLinks.ts`, `usePeerChains.ts`: data-fetching hooks.
- `src/lib/agentLinks.ts`, `peerChains.ts`, `peerChainGraph.ts`: client-side models and graph utilities.
- `src/i18n/catalogs/agentLinks.ts`, `peerChains.ts`: i18n strings.

**Tests**
- `tests/test_agent_links.py` (456 lines): comprehensive API tests for CRUD, cycle, cancel.
- `tests/agent/test_roster_agent_links.py` (86 lines): roster broadcast with links.
- `tests/test_mcp_ask_peer.py`, `tests/test_peer_ask.py`: updated for new agent_id path and depth behaviour.
- `frontend/src/components/ChatArea.peerChains.test.tsx`, `ThreadPanel.peerChains.test.tsx`, `peer-chains/PeerChainProgressCard.test.tsx`, `PeerChainTree.test.tsx`: UI tests.
- `frontend/src/lib/agentLinks.test.ts`, `peerChains.test.ts`: lib unit tests.
- `frontend/e2e/agent-links.spec.ts` (219 lines): end-to-end Playwright tests.

## Decisions

- **Link granularity: global pair with "when to ask" label vs. per-room link or capability-based access.**  
  Global one-directional links were chosen so a specialist agent (e.g. a code reviewer) can be shared across all rooms without per-room configuration. The "when to ask" label gives the requesting agent the same in-context hint as the roster description without requiring a richer permission layer. Per-room links were considered but would have reproduced the original problem at a finer scale.

- **Thread-only seating (`joined_via = 'link'`) vs. full room membership.**  
  Seating the linked agent only for the thread keeps room state clean — member lists, `@everyone`, the one-agent rule, and the representative are unaffected. Full membership would pollute every downstream feature that iterates participants; thread-only seating isolates the change to membership predicates via `visible_member()`.

- **Unlimited depth by default with opt-in caps vs. hard cap.**  
  The routing-token fallback already enforced caps; removing the default cap allows genuinely deep specialist chains without breaking existing short chains. Operators who need a bound can set `ANYGARDEN_ASK_PEER_MAX_DEPTH` / `ANYGARDEN_ASK_PEER_MAX_CALLS`. This assumes trust in the agent graph design; if agents form unintentionally deep chains in production, re-enabling the default cap should be the first remediation.

- **Cycle detection by chain ancestry vs. global per-room lock.**  
  A chain_id groups all `ask_peer` groups spawned from the same person's message. Refusing to ask an agent that already appears higher in that chain (via `parent_target_id` traversal) prevents infinite loops without blocking the same agent from participating in independent chains at the same time.

## Result

Agents can now be linked globally and asked from any room without prior room membership. The `ask_peer` tool accepts `agent_id` for linked peers and `participant_id` for room members (both still work; `participant_id` remains valid). Multi-hop chains propagate answers upward correctly. People see a live call-tree in the thread panel with path labels and a stop button to cancel the whole chain. The relationship map at `/agents/links` lets admins edit the graph visually. Migration 105 ships the schema changes. COL-14..COL-17 requirements are met. No depth/call cap is enforced by default; env vars restore the previous capped behaviour if needed.
