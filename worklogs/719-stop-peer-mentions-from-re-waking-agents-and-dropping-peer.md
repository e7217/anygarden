# fix(rooms): stop peer mentions from re-waking agents and dropping peer replies (#719)

- Commit: `c4c7473` (c4c7473bf9a27069b0795f75c5091e9e85740aa3)
- Author: Changyong Um
- Date: 2026-09-28
- PR: #719 (issue)

## Situation

In a `mentioned_only` room, a human message without mentions wakes every agent, and each one answers. When one agent's answer peer-mentioned another agent (for example "agent01, please introduce yourself"), the target agent queued that mention while it was still answering. It then ran the LLM a second time and answered the same question again. The second reply reused the sender's `request_id`, which came in on the peer message's metadata. The server's `begin_completion` rejected it as `stale_completion(authorization_revoked)` and dropped it without any notice.

This happened on current main (reproduced on a local isolated node, origin/main d9f10a4). On other builds or paths the duplicate showed up on screen. In both cases every legitimate peer reply was silently dropped.

## Task

- Stop a peer mention from waking an agent that the same user turn already woke.
- Make sure a receiving agent never mistakes the sender's `request_id` for its own turn.
- Keep the existing peer safety net (depth and budget) and the durable turn completion checks unchanged.

## Action

- `packages/cluster/anygarden/orchestration/rules.py`: `PeerHandoffBudget` gains `mark_woken()` and `woken()`. `reset()` also clears the woken set.
- `packages/cluster/anygarden/ws/handler.py`:
  - Human send fan-out records the woken set. With mentions, only the mentioned agents. Without mentions in a `mentioned_only` room, every agent. Otherwise nothing.
  - In the peer safety net block, a peer mention aimed at a woken agent is stripped from the content and from `mentions` without consuming budget. The message gets `peer_redundant=True` and a `ws.peer_mention_redundant` log line.
- `packages/cluster/anygarden/messages/metadata.py`: `strip_turn_proof(keep_request_id=)` becomes `correlate_reply=`. An agent reply's id is stored and broadcast as `reply_to_request_id` (`REPLY_REQUEST_ID_KEY`). Caller-supplied `reply_to_request_id` values are always dropped.
- `ws/handler.py`: the tracing and `response_sent` correlation id now reads the new key.
- Tests:
  - Added the woken unit test and three redundant-peer integration tests.
  - Added a test that the peer frame does not carry the sender's `request_id`.
  - Updated three existing assertions to the new key.
  - Changed the human message in `test_user_send_resets_peer_budget` to mention only the sender, so the test keeps checking the budget reset.

## Decisions

- **Where to stop the re-wake:**
  - Chosen: server-side filtering.
  - Rejected: agent-side dedupe. The peer frame carries no identifier of the originating human turn, so the agent has no basis to decide.
  - Rejected: banning all peer mentions after an unaddressed message. That is a product policy change.
  - Deciding factor: "who was already asked this turn" is only fully known at the server's fan-out.
- **Where to keep the woken state:**
  - Chosen: in-memory on `PeerHandoffBudget`. It has the same lifecycle and precision as the existing budget and needs no extra query.
  - Rejected: querying `agent_turns`. It would need separate tracking of the user-turn message and one more query per peer mention.
  - Losing the state on restart only brings back the old behavior (one duplicate).
- **Where to fix the `request_id` confusion:**
  - Chosen: the server stores the reply id under a different key. That covers live broadcast, replay, and history in one place, and the frontend does not use this field.
  - Rejected: the agent ignoring `request_id` on frames without a lease. That breaks legacy delivery, which sends `request_id` without a lease.
  - Rejected: `begin_completion` letting a foreign id pass as legacy. The root cause and the log noise would remain.
- **Revisit if:** the agent `decide_policy` wake rules (rules 5 and 6) change, or some consumer starts reading `request_id` from message metadata.

## Result

- Live check, unaddressed question: both agents peer-mentioned each other, both mentions were removed as `peer_redundant`, and there were exactly 2 replies. Each agent called the engine once, with no `stale_completion`.
- Live check, a legitimate peer_query to a non-woken agent: the reply is **stored as a message** (it used to be dropped).
- `packages/cluster` pytest: 1966 passed. `packages/agent` pytest: 653 passed. The changed code adds no ruff findings.
- Separate issue found during live testing: when an agent SKIPs a turn, that turn stays `leased` until its lease expires and blocks later turns in the room. This is out of scope for this change and is tracked separately.
