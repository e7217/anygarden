# fix(goals): post goal reports once and clean up stop and peer-question noise (#969)

- Commit: `54671a9` (54671a9684a8816d61177b96cfe422848b36817e)
- Author: Changyong Um
- Date: 2026-10-28T18:41:25+09:00
- PR: #970

## Situation

A review of a live goal run (room "기획", messages #44–#65, issue #969) found seven problems. Three had a clear cause in code and a small fix:

- **The goal report landed twice.** On `awaiting_confirmation` the lead posted #60 and, 34 seconds later, #61. #60 had no `request_id` or `turn_outcome`, no `agent_turns` row and no `usage_ledger` row. The tracked turn behind #61 alone was 304,029 input tokens.
- **"Goal … was stopped. user".** Stopping a goal put the code `user` in the chat text and in the status card's detail line.
- **Peer answers ended in a failure paragraph.** Both peers in the cycle (#55, #56) closed with "claim_current_request was rejected, so no task and no mark_task_status".

## Task

- Make the lead receive a goal report request only through the turn the server opens for it.
- Keep stop reason codes out of the status notice; show a reason only when a person wrote one.
- Stop telling peers to claim a request the server can never claim.
- Leave issue items 2, 3, 6 and 7 (`ask_peer` attachments, evaluator/metric mismatch, report tone, approval acknowledgement turn) for later PRs.

## Action

**Duplicate report (`campaigns/notify.py`)**
- `request_report` marks its message for broadcast and opens the lead's turn in the same transaction. `publish()` sent that message with `manager.broadcast` to every subscriber, so the lead's client also saw a mention with no `request_id`. `decide_policy` answered it (`mentioned_me`), and the handler queued that run behind the previous turn's room lock. The leased delivery queued after it. Both ran.
- `publish()` now looks up `AgentTurn.target_participant_id` for each message and uses `broadcast_tailored` to skip that participant. Draft requests and approval results (`broadcast_project_messages`) and task assignments already did this. A message that opens no turn is broadcast as before, and so is any manager without `broadcast_tailored`.

**Stop notice (`campaigns/lifecycle.py`)**
- `stop_campaign` returned `{"reason": reason or stop_reason}` as event details. `apply_transition` uses `status_detail` or `details["reason"]` as the notice detail, so the code became the detail. The event now records `stop_reason` under its own key and `reason` only when one was written.

**Peer questions (`agent/integrations/codex_cli.py`)**
- `general_task_rule` returns nothing when the delivery's metadata has `kind == "peer_query"`. That is set on every peer question (`peer_fanin.post_question` and the routed form in `ws/handler.py`), and the turn's leased metadata starts from the message's metadata. The server rejects such a claim because the source is an agent's message in a thread.

**Requirements (`docs/requirements.md`)**
- CMP-16: a reason shows only when it is a written sentence, never a stop code.
- CMP-17: the lead receives the report request only as its turn and posts one report.

## Decisions

- **Server fix, not an agent-side filter.** The agent could ignore `goal_report` messages that arrive without a `request_id`, but draft requests do not rely on such a filter. Fixing the broadcast removes the cause in one place.
- **Narrow peer-question rule.** System notices and thread replies also cannot be claimed, but a peer-ask wake turn or a resumed task still needs the rule's `mark_task_status` and `request_task_input` instructions. Only peer questions are certain to fail, so only they lose the rule.
- **Event key change for stops.** Nothing reads a stop event's `reason`. The code moves to `stop_reason` in the event, and `campaigns.stop_reason` is unchanged.

## Result

- New tests in `tests/test_campaigns_notify.py`: the lead gets no broadcast frame for its report request while another subscriber does, and a stop notice reads "Goal “…” was stopped." with no detail (both failed before the fix). Guards: a stop notice still reaches the lead, and a written stop reason still shows.
- New test in `tests/agent/test_integrations/test_general_task_rule.py`: no rule for `kind: peer_query` (failed before the fix).
- Campaign, goal, general task, workspace and agent integration suites: 691 passed.
- Full cluster suite: 4,577 passed. 3 failures and 1 error all come from a missing `federation_executor_bindings` table; two of them fail the same way on the unmodified checkout, and none touch the changed code.
- Not yet checked live: a goal reaching its target on a node with a real engine posts one report.
