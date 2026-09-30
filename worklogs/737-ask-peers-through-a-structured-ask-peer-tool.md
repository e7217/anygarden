# feat(agent): ask peers through a structured ask_peer tool (#737)

- Commit: `c0de8c6` (c0de8c633734bda6192953f4c1af8badadadace0)
- Author: Changyong Um
- Date: 2026-09-30T11:12:03+09:00
- PR: —

## Situation

Agents could call another agent only by writing a `<@user:PARTICIPANT_ID>` routing token into the reply text, guided by the roster prompt. Models often dropped the token: in room `9eb1547e…` local-agent wrote "PM에게 물어봐 드릴게요 …" with no token, PM never woke for that question, and the room read as if the question had been asked. Comments and docstrings referred to a `handoff_to` MCP tool that did not exist. When a call targeted a peer that was already answering, the server could only strip the token after the reply was posted, leaving the request sentence in place (#743; #747 added a "call not delivered" marker for that case).

## Task

- Give agents a tool call for peer asks, so a call exists as a tool record rather than as prose.
- Return rejections to the model during its turn, including the messages posted since the turn started (the contract handed over from #743).
- Post accepted asks without duplicating the WS handler's turn creation, lease and peer safety-net logic.
- Keep the routing token as a fallback for engines without the tool, and fix the stale `handoff_to` comments.

## Action

- `packages/cluster/anygarden/orchestration/peer_ask.py` (new): `PendingPeerAsks` stores asks per `(agent_id, room_id)`, bound to the caller's open turn `request_id`, replaced per target, expiring after 10 minutes, cleared per room. `check_peer_ask()` validates the caller and target participants (agents only, not self), finds the caller's open `AgentTurn`, rejects with `already_answering` when the target is in `PeerHandoffBudget.woken()` or `limit_reached` when `would_block()`, and collects up to 10 root/thread messages after the turn's trigger (400 chars each).
- `packages/cluster/anygarden/orchestration/rules.py`: `PeerHandoffBudget.would_block()` mirrors the safety net's block rule without consuming a slot.
- `packages/cluster/anygarden/mcp/tools.py`, `mcp/router.py`: `ask_peer` schema and handler; bad arguments are `isError: true`, rejections are normal results with `structuredContent.status = "rejected"`, `reason`, `targets` and `since_turn_start`, and a text summary telling the model not to repeat the request.
- `packages/cluster/anygarden/app.py`: `app.state.pending_peer_asks`.
- `packages/cluster/anygarden/ws/handler.py`: after an agent's reply is stored and broadcast, scheduled asks for the reply's `request_id` are turned into send frames (`<@user:B> question`, `thread_root_id` = the reply or its thread root, `metadata.peer_ask.via = "tool"`) and queued in `injected_sends`, which the receive loop reads before the socket. The peer safety net now also runs for these thread sends (`is_tool_peer_ask`); a human root message clears the room's pending asks.
- `packages/agent/anygarden_agent/client.py`: roster guidance tells the model to call `ask_peer`, not to write the request in the reply, to answer from the returned messages on rejection, and to use a routing token only if the tool is unavailable. `handoff_to` comments in `client.py`, `ws/protocol.py`, `rooms/router.py` now describe `ask_peer` / `[HANDOFF]`.
- Tests: `cluster/tests/test_peer_ask.py` (store, `would_block`, checks), `cluster/tests/test_mcp_ask_peer.py` (scheduled ask posted in a thread and waking the peer with that message as trigger, rejection with recent messages, person target error, depth cap applied to a thread ask, pending asks cleared by a human message), `test_mcp_server_create_skill.py` tool list, `agent/tests/test_roster_self_identity.py` guidance phrases.

## Decisions

- **How the ask reaches the room.** Options: (a) the tool posts a message immediately; (b) the tool schedules and the server appends the call line to the caller's final reply; (c) the tool returns a token for the model to paste; (d) the tool schedules and the server posts the ask as a thread reply under the final reply. (a) needs turn creation, per-recipient `request_id` frames and the safety net, all of which live inline in the WS handler, and the REST message path does none of it. (c) keeps the "forgot to paste" failure. (b) was the first plan; the user proposed (d) so the main timeline keeps only the caller's reply while the peer Q&A sits in a thread. (d) was chosen: re-feeding a send frame into the same loop reuses the whole agent-send path, and a send without turn proof is treated as `legacy` by `begin_completion`.
- **Safety net in threads.** Agent thread replies skipped the peer safety net (`not is_thread_reply`). Posting asks in threads would have bypassed depth/budget and the redundant-wake check, so the condition now includes tool asks explicitly, using a local flag rather than client metadata.
- **Budget is read, not spent, by the tool.** Spending at tool time would double-count with the send path or cost a slot when no reply goes out. The race between check and post is covered by the safety net plus #743's `peer_call_undelivered`.
- **Rejection is not a tool error.** An error result may be retried or surfaced as a failure by an engine; a rejection is an expected outcome the model should act on.
- **Out of scope:** detecting "said it would ask but did not" from wording (the issue's secondary idea); wording differs by language and would produce false positives, so it waits until the tool's effect is visible.
- Assumptions to revisit: agents always send a final reply after calling the tool (otherwise the ask expires with a warning); state is in memory like `PeerHandoffBudget` (single process exact); codex agents need `permission_level=trusted` for MCP tools, and the roster keeps the token fallback for those that lack it.
- Sources: `.tmp/plan-737-ask-peer-tool.md`, issue #737 and its contract comment from #743.

## Result

- Agents can call a peer with `ask_peer`; the question appears as a thread reply under the caller's final reply and the peer's turn is triggered by that thread message.
- A call to a peer already answering, or over the limit, is rejected inside the caller's turn with the recent messages, so no dangling request is posted.
- Tests: agent suite 678 passed; cluster related subset 578 passed; new tests 20 passed; full cluster suite 2054 passed.
- Pending: live check with a codex agent (`permission_level=trusted`) to confirm models use the tool.
