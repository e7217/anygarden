# fix(rooms): mark undelivered peer calls and tell co-woken agents about each other (#743)

- Commit: `edb0f19` (edb0f19e7cca7a5971c49867f0776be2c08cf730)
- Author: Changyong Um
- Date: 2026-09-30T10:04:07+09:00
- PR: —

## Situation

`@everyone` expands into a mention of every agent, so all of them start a turn at the same moment and none sees the others' replies. In room `9eb1547e…` (2026-09-29) PM introduced itself last and then wrote `<@local-agent> <@agent01> 두 분도 각자 소개해 주세요.` — to two agents that had already introduced themselves. The #719 safety net refused the redundant wake but only replaced the tokens with spaces, so the room showed PM asking for something already done, with no sign that the call went nowhere. The agent prompt never said that other agents received the same message.

## Task

- Reduce how often a co-woken agent hands the turn to peers that are already answering.
- Keep the agent's text as written, but make visible which peer calls the server refused and why, for both the redundant-wake path and the depth/budget-limit path.
- Do not change the existing `peer_redundant` / `peer_blocked` / `peer_depth` metadata that logs and tests rely on.
- Leave the structured peer-ask tool's rejection contract to #737.

## Action

- `packages/agent/anygarden_agent/integrations/base.py`: new `compose_concurrent_call_hint()` builds a `<concurrent-call>` block when the message's `metadata.mentions` call this agent plus at least one other agent (roster `kind == "agent"`, or `via: "everyone"`). It names the other agents, or gives only the count when a name is unknown. `EngineAdapter.assemble_user_content()` appends it after `<referenced-files>` and before the sender-labelled message. Codex, Pi and room-execution adapters all build turn input through this method.
- `packages/cluster/anygarden/orchestration/rules.py`: new `undelivered_peer_calls(mentions, reason)` returns `[{participant_id, reason}]` for `user` mentions.
- `packages/cluster/anygarden/ws/handler.py`: the redundant path adds entries with `already_answering`; the blocked path adds `limit_reached`. Both extend one `metadata.peer_call_undelivered` list.
- `packages/cluster/frontend/src/lib/undeliveredCalls.ts`: `extractUndeliveredCalls()` groups valid entries by reason and ignores malformed ones.
- `packages/cluster/frontend/src/components/MessageBubble.tsx`: `UndeliveredCalls` renders one muted chip per reason ("Call not delivered · names (already answering | call limit reached)") with an icon and a tooltip; `i18n/catalogs/chat.ts` adds en/ko strings.
- Tests: `packages/agent/tests/test_integrations/test_base_adapter.py` (hint conditions, placement, unchanged output without the hint), `packages/cluster/tests/test_peer_mention_safety_net.py` (both reasons, mixed reasons in one message, blank-after-strip message still delivered, no field for valid calls), `lib/undeliveredCalls.test.ts`, `components/MessageBubble.test.tsx`.

## Decisions

- **Undelivered calls: delete the calling paragraph vs keep text as-is vs keep text and mark it.** The issue's first draft deleted a paragraph whose tokens were all invalid. Rejected: `mark_woken` records every target when the human message arrives, so the server only knows "already woken", not "request already satisfied". If PM had finished first, the same sentence would read naturally, and a new request (`<@agent01> 빠진 일정 보충해 주세요`) or a legitimate call stopped by the limit would be erased too. Keeping the text and marking it preserves what the agent said while telling the user what happened.
- **How to tell agents about the concurrent call: roster prompt vs per-turn hint vs server-side content prefix.** The roster block is sha-tracked and re-injected only when it changes, so it would not be restated per message; a server prefix would pollute stored content for every client. The per-turn hint reuses the `referenced-files` precedent and needs no protocol change because mentions and roster already reach the agent.
- **Hint condition: only `via: "everyone"` vs any message calling ≥2 agents including me.** Chose the latter: a human typing `<@A> <@B>` causes the same simultaneous wake.
- **Sequential answering / a facilitator agent** were rejected (order bias from conformity, latency growing with participant count, a per-room queue). Humans act as facilitators in mention-only rooms; revisit only with a concrete use case such as automatic `@everyone` summaries.
- Assumptions: roster `kind` for agents is `"agent"` (verified in `ParticipantBrief`). Whether the hint actually suppresses hand-offs depends on the model; the root fix is #737 returning messages posted since turn start in the tool's rejection result.
- Sources: `.tmp/plan-743-peer-call-undelivered.md`, issue #743 body and the direction-change comment.

## Result

- Co-woken agents now see a `<concurrent-call>` block naming the other agents called by the same message.
- Refused peer calls are listed in `metadata.peer_call_undelivered` and shown as a "Call not delivered" chip under the agent's message; the text is unchanged.
- Tests: agent package 678 passed; cluster ws/peer/mention subset 355 passed; frontend vitest 765 passed; `npm run build` passes.
- Pending: #737 rejection contract (return messages posted since turn start); live E2E check of whether the hint reduces hand-off sentences.
