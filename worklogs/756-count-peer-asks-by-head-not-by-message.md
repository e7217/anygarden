# fix(rooms): count peer asks by head, not by message (#756)

- Commit: `47b21f7` (47b21f72945a5b9367d21eb1f6bfe3217dad88c4)
- Author: Changyong Um
- Date: 2026-09-30T14:19:35+09:00
- PR: —

## Situation

A user asked PM to "ask the other agents what they can do". PM called `ask_peer` twice (agent01, local-agent) and told the user that both would answer. Only agent01's question was delivered. The one to local-agent was stored with `peer_depth: 2`, `peer_blocked` and `peer_call_undelivered: limit_reached`. The peer safety net approximated depth by counting peer-calling messages in the user turn (`used > MAX_PEER_DEPTH`). #737 posts each scheduled ask as a separate thread message, so the second ask counted as depth 2. Meanwhile `check_peer_ask()` only looked at the budget at tool time, so it returned `scheduled` for both asks. The model's false promise came from the tool's answer.

## Task

- Asking several peers from one turn must deliver them all, within the total cap of 8.
- A call from a turn that a peer's call started must still be blocked, so A→B→C chains stay closed.
- One body message naming two peers and two `ask_peer` calls must be judged the same way.
- An ask the tool returns as `scheduled` must not be blocked at send time. One that won't fit must be `rejected` while the model can still fix its reply.

## Action

- `orchestration/rules.py`
  - Rewrote the `MAX_PEER_DEPTH` and `MAX_TOTAL_PEER_HANDOFFS_PER_USER_TURN` comments (hop vs. head count).
  - `PeerHandoffBudget` gains `mark_peer_called()`/`peer_called()`, which `reset()` clears.
  - `would_block(room_id, *, hop=1, reserved=0)` is now `hop > MAX_PEER_DEPTH or remaining < reserved + 1`.
- `orchestration/peer_ask.py`
  - `turn_hop(db, turn)` returns 2 only when the turn's trigger is an agent message that mentions the turn's target and is not a delegation. Person triggers, delegations, round-robin/handoff nominations and a missing turn all return 1.
  - `sender_hop(db, request_id=, participant_id=)` trusts a turn only if its `target_participant_id` is the sender.
  - `PendingPeerAsks.pending_in_room()`.
  - `check_peer_ask(..., pending=)` rejects targets in `woken ∪ peer_called` as `already_answering`. Otherwise it calls `would_block(hop=turn_hop(open turn), reserved=room-wide pending minus this caller's same-target ask)`.
- `mcp/tools.py` passes `pending` into `check_peer_ask`.
- `ws/handler.py`
  - New `_peer_caller_hop()`: the sender's turn via its `request_id`, falling back to hop 2 when the sender is in `peer_called`.
  - `injected_sends` now holds `(raw, hop)`. The hop is computed once from the reply that scheduled the asks.
  - Safety net:
    - Redundant set is `woken ∪ peer_called`.
    - Checks the hop first. Otherwise it runs `consume(room, len(called_pids))`, and a successful call is recorded with `mark_peer_called`.
    - Stamps `peer_depth` = hop (or cap+1 on budget exhaustion) and `kind` from the hop.
- Tests:
  - `test_peer_ask.py`: `TestPendingInRoom`, a rewritten `TestWouldBlock`, `TestTurnHop` (6 cases), and 5 new `check_peer_ask` cases.
  - `test_orchestration.py`: a `peer_called` budget test.
  - `test_peer_mention_safety_net.py`: `TestPeerCallHop` (2 peers in one message, a later call to another peer, the called peer being depth-blocked, the head-count cap). Three existing tests were updated.
  - `test_mcp_ask_peer.py`: two asks delivered, the over-budget ask rejected by the tool, a peer woken by an ask can't ask on. The old depth-cap test was redefined.

## Decisions

- **Where depth comes from.** Options weighed:
  - Keep the `used` count and only make the tool count reservations. This still leaves one peer per turn.
  - An in-memory "peer_called" set alone.
  - The sender's turn trigger.

  Chose the turn trigger. The set is kept only as a fallback for sends without a turn proof. Agent thread replies skip the safety net, so a peer that wakes A through a thread mention would not appear in the set. The set alone would then allow A↔B ping-pong up to the total cap, which is weaker than today.
- **Narrowing hop 2 while reading the code.** The plan said "agent-authored trigger = hop 2". Round-robin/handoff nominations and delegations also create turns from agent messages, so hop 2 now also requires the trigger to *mention* the turn's target and not be a delegation. This keeps the current behavior for those paths.
- **Re-calling the same peer (added beyond the issue).** Under the new rule a second call to the same peer would be hop 1 and wake it twice. It is now treated like #719's redundant wake (`already_answering`, no slot spent). That is why three existing tests changed reason from `limit_reached` to `already_answering` and still assert "not delivered".
- **Reservations are counted room-wide, not per caller as the issue suggested.** With two woken agents each scheduling several asks, per-caller counting could still return `scheduled` and then block at send time. The cost is that asks from a reply that never goes out hold slots until the next human message or the TTL (600s).
- Rejected (from the issue): raising `MAX_PEER_DEPTH`, which reopens A→B→A; merging asks into one message, which works around the rule instead of fixing it.
- **Assumptions:**
  - A reply's `metadata.request_id` is its own turn proof. `sender_hop` checks `target_participant_id`.
  - check→schedule is not atomic. Concurrent tool calls could over-reserve, and the send-time net still blocks them (the safe direction).

## Result

- Tests:
  - Issue repro `test_two_asks_in_one_turn_are_both_delivered` and two `TestPeerCallHop` cases fail on `main` and pass here. The depth-block and cap tests pass on both, as regression guards.
  - Peer-related files (`test_peer_mention_safety_net`, `test_mcp_ask_peer`, `test_peer_ask`, `test_orchestration`): 74 passed.
  - Cluster full suite: 2646 passed, 2 skipped and 2 failed in `test_ws_handler.py` (`TestActivityLogRequestIdCorrelation::test_message_received_records_trigger_message_id`, `TestAgentCausalLink::test_directed_delegation_creates_only_target_child_turn[owner-round_robin]`).
    - Those two failed with a SQLite FK error. The earlier run failed a different `TestAgentCausalLink` case.
    - Each failing test passed 3/3 when rerun in isolation, and neither user sends nor delegations reach the changed peer branch.
    - `test_ws_handler.py` as a whole then passed twice in a row (149 passed each).
- Known gap, unchanged: an agent's *thread* reply that mentions a peer still skips the safety net entirely. Only `ask_peer` sends in threads go through it.
