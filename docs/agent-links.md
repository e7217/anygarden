# Agent links

A link `A → B` lets A ask B for help from any room. Agents choose whether help is useful from the current task and each peer's public description. Edit that description in the agent's settings; relationships do not carry conditions or instructions. A reply returns to the requesting agent without a reverse link.

Open **Overview** at the top of the sidebar. When there are two or more agents, it is also the home screen: `/` without a room opens it, including after a room is archived or deleted. With fewer agents, home keeps the welcome screen. Hover, select or keyboard-focus an agent to reveal the small hollow connection point on its right. Drag from that point onto another agent, or use **Add link** to choose the two agents. On touch screens, tap the agent to select it; **Add link** also remains available without dragging. Select a link to delete it or add the reverse relationship. Each agent is a circle with its engine's logo, and its name sits under it on up to two lines. The circle grows 5% for each agent it links to, with no cap. Links have no arrowheads: a link leaves the asking agent's right point and enters the answering agent's left point, so the side it meets shows its direction. Selecting an agent highlights related paths while keeping all names readable. In its details, select a linked agent's name to open that agent and bring it into view, or use the button at the end of its row to delete that link. Cycles may be configured, but a running question cannot call an ancestor again.

Select an agent to see its description, accessible previous conversations and connections. Open a conversation by title to continue in that room. Administrators can explicitly start a new conversation. Other users can open conversations they already have access to. Conversation history is stored by DM room, not by the engine's internal session ID. The map remembers each user's layout and viewport in that browser.

## Overview (#1023)

The map shows what is happening now, limited to rooms the person can open:

- An agent answering in a room gets an aurora ring turning around its circle (a still ring when the system asks for reduced motion). Hover or select it to see a card with a link to that room (`Answering in #room`, or `+N` for more rooms).
- An agent that cannot answer gets a dashed outline and a faded logo; its card gives a short reason: stopped after an error, no machine to run on, not in any room, or offline. Failure details stay in the admin agent settings.
- A link only flows while a question runs along it. A resting link is a still line. With reduced motion, a running question stays a thicker solid line. A question between members of the same room needs no link; it is drawn as a temporary dashed line while it runs.
- An agent can carry badges for what needs you. The top right shows one, in this order: a problem (`!`, a failed task assigned to it or its last answer in a room failed), a request waiting on you (a clock: a workspace action, project approval or question it asked for), or an unread finished answer (a dot). The bottom right counts tasks queued for it. Badges go away when their cause does: the task is requeued or removed, a later answer in that room completes, you approve, reject or the request expires, or you read the room. Hover or select the agent to see each item with a link. Agents that cannot answer show no badges.
- With nothing selected, the side panel lists what needs you (inbox items waiting on you), what is running now, and rooms with news. Empty sections are hidden.
- **Auto-arrange** places agents left to right by who they can ask, so agents nobody asks sit in the first column.

`GET /api/v1/agent-links` now also returns each agent's `unavailable_code` (null when fine). `GET /api/v1/peer-chains/active` returns `{"edges": [{chain_id, room_id, from_agent_id, to_agent_id}]}` for open agent-to-agent questions in the caller's accessible rooms (the same rooms as `GET /api/v1/turns/active`). It requires a person; agent identities get 403. Questions a person asked are not edges.

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
