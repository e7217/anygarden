# feat(agent): async fan-in for ask_peer results (#762)

- Commit: `8393745` (83937458be660e07d8f94775c490e60fb145bd35)
- Author: Changyong Um
- Date: 2026-09-30T17:27:01+09:00
- PR: #762

## Situation

A user asked PM to check the host's resources "through the agents". PM called `ask_peer` twice, but the tool only scheduled the questions in memory (`PendingPeerAsks`) and the WS handler posted them after PM's final reply. PM therefore wrote its answer without the peers' results, the peers answered in a thread, and nothing woke PM again: no combined answer reached the main channel, and agent01's failure (`bwrap` network namespace limit) stayed buried in the thread. The roster prompt still carried #283's "only synthesize if asked" rule, whose premise (peer replies land in the main channel) ended when #737 moved peer conversations into threads.

## Task

- Let the caller write one final answer from its peers' results.
- Peer work often takes minutes, so the caller must not block inside a tool call (engine MCP tool timeouts, the room queue of 3 items / 60 s, the 900 s turn timeout).
- Keep hop 1 only: a peer asked by another agent must not ask on.
- Survive a restart; show progress while waiting; keep the main channel to one caller message.

## Action

- **Data model** — `PeerAskGroup` / `PeerAskTarget` (`db/models.py`, migration `081_peer_ask_groups`): one group per caller turn (`caller_request_id` unique) with scope, draft, deadline and state (`collecting → ready → waking → woken | abandoned`); one target row per question with the peer's turn `request_id`, terminal state/reason, reply and forwarded requests.
- **Fan-in module** — `orchestration/peer_fanin.py`:
  - `post_question` posts `<@user:target> question` in the trigger's thread (authored by the caller, so the peer turn is hop 2) and starts the peer turn. The target row is flushed first so a turn born cancelled settles it.
  - `absorb_caller_reply` keeps the caller's reply as the draft.
  - `on_turn_terminal` (called from `turns/service.py::_on_turn_terminal`, the hook added in #765) settles targets and marks the caller closed; the group is `ready` when both sides are done.
  - `dispatch_peer_ask_groups` (run by the turn-recovery loop in `app.py`) times out overdue targets (`ANYGARDEN_PEER_ASK_DEADLINE_SEC`, default 1800), claims ready groups with a `ready → waking` CAS, posts a hidden result message (`system_origin=peer_ask_results`, participant `None`) in the caller's original scope and starts the caller's next turn, and sends a `waiting_peers` typing heartbeat every 3 s.
- **Tool** — `mcp/tools.py::ask_peer` takes `asks: [{participant_id, question}]` (single-target arguments still accepted), rejects `already_answering` (target has an open turn in the room, `orchestration/peer_ask.py::check_peer_ask`) and budget overflow per target, forwards hop-2 calls to the caller, and returns guidance that the reply becomes a draft. `mcp/router.py` commits and broadcasts the questions, withholding the live frame from targets with a durable turn.
- **WS handler** — after `begin_completion` accepts a reply whose turn owns a group, the reply is absorbed and the turn closed with `turns/service.py::finish_deferred` (`completed/awaiting_peers`). Removed `PendingPeerAsks`, `injected_sends`, `is_tool_peer_ask` and the post-reply injection block.
- **Caller turn lookup** — `_open_turn` prefers the open turn whose active attempt is leased/started, since the MCP call carries no turn id.
- **Protocol/frontend** — `TypingOut` gains `waiting_peers` plus `waiting_done/total/names`; `useWebSocket` keeps `typingProgress`; `lib/typingStage.ts::stageLabel` is shared by `ChatArea` and `TypingIndicator`; `lib/systemMessages.ts` hides result messages from the timeline and thread reply counts.
- **Prompt** — roster suffix (`agent/anygarden_agent/client.py`) and tool description drop the #283 rule and tell the caller to write one final answer, naming failed/timed-out peers.

## Decisions

- **Sync vs async vs model polling.** A synchronous `ask_peer` (wait for answers inside the tool) was the issue's first draft. It was dropped once it was clear peer work often takes minutes: it hits engine tool-call timeouts, blocks the caller's room queue so user messages are dropped after 60 s, and can exceed the turn timeout. Model polling (`wait_peers`) was rejected because the model tends to end its turn claiming to wait (MCP SEP-1686, use case 5). Async fan-in with server-side wake matches Claude Code background agents, A2A push, MCP Tasks, ADK and Temporal.
- **Ending the caller's first turn.** An empty reply on a tracked turn becomes `failed` with a user notice, and there is no model-driven skip. So the server absorbs whatever the model writes and keeps it as a draft; the model's own findings (PM's CPU table) are handed back instead of lost.
- **Where to wake.** Engine sessions are keyed by `(room, thread_root_id)`, so waking in the question thread would lose the caller's context and put the final answer in the thread. The wake message goes to the caller's original scope and is hidden in the UI (precedent: `auto_route_*`).
- **Hook placement.** Terminal transitions were spread over 12 sites; #765 funnelled them into `mark_turn_terminal`. The hook only updates rows; broadcasting stays in the worker, which also claims groups with a CAS so two workers never wake twice.
- **`already_answering`.** The in-memory `woken | peer_called` sets live until a human speaks again, which would stop a woken caller from re-asking a finished peer; an open-turn DB check matches the intent.
- Review fixes before merge: round-2 questions anchor on the origin message (not the hidden wake message); delegation results are never absorbed; the worker re-checks readiness of every closed-caller group (a peer and the caller closing at once could each miss the other's write on Postgres); a racing second `ask_peer` joins the group via a savepoint instead of failing on the unique key; one failing group is logged and skipped instead of stalling the recovery tick.
- Assumptions to revisit: the 1800 s default deadline; the in-memory `PeerHandoffBudget` resets on a human root message, so a woken turn after new user input starts a fresh budget; a caller agent stopped at wake time leaves the group `abandoned` with only a log line (surfacing it to users is future work).

## Result

- `ask_peer` posts at once, the caller's first turn posts nothing, and the caller is woken with all answers (or failure/timeout reasons, forwarded requests, draft and original request) to write one final answer in place.
- Tests: `tests/test_mcp_ask_peer.py` rewritten (21: immediate posting, rejection, budget, forward, draft/wake, deadline, cancelled peer, re-ask, single wake, thread scope, waiting heartbeat, late answers, rendering, and the five review fixes), `tests/test_peer_ask.py` updated, roster test pins the new wording, frontend tests for the waiting stage, progress state and hidden thread replies. Cluster 2675 passed / 2 skipped, agent 680 passed, frontend 794 passed, `npm run build` OK.
- Pending: live E2E on a running node (the original PM/agent01/local-agent scenario).
