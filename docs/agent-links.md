# Agent links

A link `A → B` lets A ask B for help from any room. Agents choose whether help is useful from the current task and each peer's public description. Edit that description in the agent's settings; relationships do not carry conditions or instructions. A reply returns to the requesting agent without a reverse link.

Open **Agent links** beside the sidebar's Agents heading. Hover, select or keyboard-focus an agent to reveal the small hollow connection point on its right. Drag from that point onto another card, or use **Add link** to choose the two agents. On touch screens, tap the agent to select it; **Add link** also remains available without dragging. Select an arrow to delete it or add the reverse relationship. A slim card shows only its name; a small increase in size represents more directly outgoing connections. Thin arrows show the direction of help, and selecting a card highlights related paths while keeping all names readable. Cycles may be configured, but a running question cannot call an ancestor again.

Select an agent to see its description, accessible previous conversations and connections. Open a conversation by title to continue in that room. Administrators can explicitly start a new conversation. Other users can open conversations they already have access to. Conversation history is stored by DM room, not by the engine's internal session ID. The map remembers each user's layout and viewport in that browser.

## API change in #1009

All agent-link endpoints require an authenticated person, including non-admin users. Agent identities cannot edit links. This leaves the existing admin-only permission for creating DM rooms unchanged.

- `GET /api/v1/agent-links` returns `agents` and `links`. Links contain `id`, `from_agent_id`, `to_agent_id`, `created_at` and `updated_at`.
- `POST /api/v1/agent-links` takes only `from_agent_id` and `to_agent_id`, returning 201. Self-links return 400, missing agents 404 and duplicate pairs 409.
- Sending the retired `when_to_ask` field returns 422. It is not silently retained or applied.
- The old condition-editing `PATCH /api/v1/agent-links/{id}` is removed (405). Change a relationship's endpoints by deleting it and creating another.
- `DELETE /api/v1/agent-links/{id}` retains its existing behavior (204).

The roster's `LinkBrief` carries `agent_id`, `display_name` and `description`. Updating a target's name or description refreshes its linked callers' rooms even when the target is not a room member. Deleting the target removes its links and refreshes surviving affected rooms. The existing `ask_peer`, fan-in, thread-only participant and cancellation behavior is unchanged.

## Upgrade and rollback

Upgrade the server and agent clients together. Older agent clients do not render public descriptions for out-of-room linked peers and still expect the previous condition-based prompt. New clients ignore any legacy condition field in older roster snapshots.

Migration `106_agent_links_autonomous` preserves relationships, identifiers, audit timestamps and constraints, and removes `when_to_ask`. The old strings are intentionally discarded. Downgrade restores the column with empty values; it cannot recover the original text. Restore a database backup made before upgrading if original condition strings are required for a rollback.
