# fix(turns): close durable turns the agent declines instead of leaving them leased (#720)

- Commit: `5fa2d2d` (5fa2d2d6ed24ea6fb734baa1c3b6a270af884dd4)
- Author: Changyong Um
- Date: 2026-09-28T13:40:35+09:00
- PR: #720 (issue)

## Situation

When a human sends a message, the server creates a durable turn for **every** agent in the room. If an agent's `decide_policy` declined it (SKIP / INGEST_ONLY), the handler in `register_room_adapter` returned without sending a lifecycle frame. The turn stayed `leased` until its lease expired (`turn_timeout_sec`+300s, about 20 minutes). During that time the `prior_open` fence blocked every later turn for that agent in the room. After expiry the recovery loop redispatched the turn, and the agent skipped it again. This was found and reproduced while live-verifying #719: after mentioning only one agent, the other agent stopped responding.

## Task

- Close a delivered turn the agent declines immediately.
- The closing path must not trigger a retry (lost-reply recovery) or a #463 task redispatch.
- Stop the turn health view (#716) and the activity panel from showing a declined turn as in flight or failed.
- Keep existing lease fencing and version compatibility.

## Action

- Protocol: added `"skipped"` to `LifecycleFrame.outcome` in `packages/agent/anygarden_agent/protocol/frames.py` and `packages/cluster/anygarden/ws/protocol.py`. The parity test is in `packages/agent/tests/test_protocol_compat.py`.
- Agent: in `packages/agent/anygarden_agent/integrations/codex_cli.py::register_room_adapter`, a non-RESPOND verdict now sends `handler_finished outcome="skipped"` after any ingest, but only when the message carries a `request_id`. pi-cli uses the same function, so it is covered too.
- Server: `packages/cluster/anygarden/turns/service.py::record_lifecycle` handles `skipped` on an active attempt of an open turn (`pending`/`leased`/`retrying`). The attempt becomes `completed(skipped)` and the turn becomes `completed` with terminal_reason `agent_skipped`. A turn in `completing` is left to the completion CAS.
- Frontend:
  - `turnHealth.ts::latestTurnHealth` skips `skipped` turns.
  - In `ActivityPanel.tsx`, the outcome type now includes `'skipped'`, the dot is neutral grey, and a label was added.
  - i18n: `admin.activity.outcome.skipped`, en "not addressed", ko "해당 없음".
- Tests:
  - `test_durable_turns.py`: 5 tests covering closing, unblocking the next turn, rejecting a forged lease, closing a legacy delivery, and staying out of the redispatch set.
  - New `test_room_adapter_skipped.py`: 4 tests.
  - Frontend: 3 test cases.

## Decisions

- **Where to fix:**
  - Chosen: the agent reports `skipped` and the server closes the turn. Only the agent knows its local judgments such as cycle detection and stale catch-up, and the failure is asymmetric: if it goes wrong, behavior simply returns to the current state.
  - Rejected as the main fix: narrowing fan-out on the server to "agents that will respond". If the server's judgment is wrong, a message is lost. It stays a possible follow-up optimization.
  - Rejected: an acceptance timeout on the server. It cannot tell a declined turn from a normal queued turn or a slow start.
- **How to express the close:**
  - Chosen: a new outcome, `completed` plus `terminal_reason="agent_skipped"`.
  - Rejected: reusing `ok`. The server treats "ok without a completion" as a lost reply and redispatches immediately.
  - Rejected: reusing `cancelled`/`rejected`. They mix with user cancellation, #463 task redispatch, and the "busy" failure category.
  - Rejected: a new turn state. It would require changing every enumeration.
- **Compatibility:** a new agent talking to an old server gets its frame rejected, which leaves today's behavior (waiting out the lease). An old agent talking to a new server never sends `skipped`, so nothing changes. No capability gate was added.
- **Revisit if:** an old server turns out to drop the connection on an unparseable lifecycle frame; or `decide_policy` starts sending further frames on the same turn after declining it.

## Result

- Live check on an isolated node with two codex-cli agents: after a message mentioning only local-agent, agent01's turn closed as `completed/agent_skipped` within 37ms. Both agents answered the follow-up unaddressed question within 8.2 seconds. There were no redispatches and no `stale_completion`.
- `packages/agent` pytest: 657 passed. Frontend: 738 passed across 94 files, and `npm run build` succeeds. `packages/cluster`: the changed test files pass. The full cluster suite result is recorded in the PR.
- Open question: whether to keep showing skipped turns as rows in the activity panel is a product decision. For now they are only labeled.
