# fix(rooms): apply peer-mention safety net to agent thread replies (#763)

- Commit: `49c300b` (49c300bbd93011b8f0a571479231ae9a219a8875)
- Author: Changyong Um
- Date: 2026-09-30T16:53:07+09:00
- PR: #763

## Situation

The peer-mention safety net in `ws/handler.py` enforces hop depth (`MAX_PEER_DEPTH=1`), the per-user-turn call budget (8) and the already-answering check. Its entry condition was `(not is_thread_reply or is_tool_peer_ask)`, so an agent's own thread reply skipped it — a condition kept since threads were introduced (#562). Since #737 moved every peer conversation into a thread, a hop-2 agent could write `<@user:C>` in a thread reply and wake C with no depth or budget check; the only brake was prompt text in the `ask_peer` rejection result.

## Task

- Make agent thread replies go through the same safety net as main-channel replies.
- Keep human thread replies unchecked, and keep `ask_peer`-posted questions behaving as before.
- Blocked thread mentions must not wake the target and must carry the same observability (`peer_blocked`, `peer_depth`, `peer_call_undelivered`).

## Action

- `packages/cluster/anygarden/ws/handler.py:1552-1561` — dropped the `(not is_thread_reply or is_tool_peer_ask)` clause from the net's entry condition and rewrote the comment. No change to the thread wake path: it builds `thread_agent_parts` from the local `mentions` list the net rewrites (`handler.py:2168-2188`), so stripping there also suppresses the wake.
- `packages/cluster/tests/test_peer_mention_safety_net.py` — added `_send_capture` (thread-aware send/echo) and `_turn_targets` helpers plus `TestThreadReplySafetyNet` with five cases: hop-2 thread mention is depth-blocked and wakes nobody; thread mentions consume the turn budget; re-calling an already-called peer in a thread is redundant; a hop-1 agent's thread mention still wakes the peer and records `peer_called`; a human thread mention is untouched.

## Decisions

- Options weighed:
  - Remove the thread exception from the net's entry condition (chosen).
  - Add a separate hop/budget check on the thread wake path (`handler.py:2229`).
  - Forbid agent peer mentions in threads altogether.
- The deciding observation: the thread wake path reads the post-net `mentions`, so one condition change reuses the already-tested strip/record/undelivered block and keeps main-channel and thread behaviour identical.
- A separate wake-path check was rejected because it would duplicate the block and leave the mention text in place while silently not waking (no #743 marker). Banning thread mentions was rejected because a hop-1 agent asking a peer inside a user's thread is legitimate.
- Accepted side effect: a hop-2 peer mentioning its caller back in a thread is now blocked as too deep. Collecting peer results back to the caller is #762's job (async fan-in). Revisit if #762 is abandoned.
- Assumption: `PeerHandoffBudget` stays in-memory and per room, reset only by a human root message; thread replies now draw from that same budget.

## Result

- Agent thread replies obey hop depth, budget and redundant-wake rules; blocked calls show as undelivered.
- New tests: 5 in `TestThreadReplySafetyNet` (4 failed before the fix, as expected). Safety-net file 20 passed; full cluster suite 2653 passed, 2 skipped.
- Follow-up: #762 removes `is_tool_peer_ask` / injected sends entirely.
