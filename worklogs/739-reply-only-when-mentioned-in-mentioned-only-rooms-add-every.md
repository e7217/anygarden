# feat(rooms): reply only when mentioned in mentioned_only rooms, add @everyone (#739)

- Commit: `674c20a` (674c20aefeb01bee5638a0c2d7fbfd7cca823565)
- Author: Changyong Um
- Date: 2026-09-29T14:23:41+09:00
- PR: #739 (issue)

## Situation

In a `mentioned_only` room, a human message that mentioned no one woke every agent (`decide_policy` rule 6: no mention + human sender → RESPOND), and the server created a durable turn for every agent. With N agents the room got N answers, and usually only one of them fitted the question. #719 had to record "every agent woken" for such messages to stop peer mentions from re-waking them, and #737 showed that this bookkeeping could strip a legitimate peer mention. Nothing chose a suitable participant, so the room had no way to address everyone on purpose or no one at all.

## Task

- A human root message in a `mentioned_only` room wakes only the agents it mentions. No mention → no reply, but agents keep the message as context.
- `@everyone` calls every agent; a room with exactly one agent answers without a mention.
- One server-side decision that agent rules, turn creation, #719 and the peer safety net all reuse. Round-robin and orchestrator rooms keep their dispatcher.
- Guests cannot use `@everyone`; an agent's `@everyone` stays subject to peer depth and budget.
- Do not touch the `wake_trigger` code that #740 removes in parallel.

## Action

- `packages/cluster/anygarden/orchestration/rules.py`
  - `parse_mentions` (`:114`) returns `{"type": "everyone"}` for a literal lowercase `@everyone` with word boundaries (not `@everyone123`, `@everyone-bot`, `a@everyone.com`), even next to ID tokens, once, and never as a legacy name.
  - New pure `expand_room_mentions` (`:150`): replaces `everyone` with every agent pid except the sender (`via: "everyone"`, skipping pids already mentioned, any strategy); otherwise adds the only agent of a `mentioned_only` room for an unmentioned human root message (`via: "sole_agent"`).
  - New `names_agent_in_content` (`:206`): the agent-side `@name` content match, used by the server for legacy mentions.
- `packages/cluster/anygarden/ws/handler.py`
  - After the guest allowlist filter (`:1366`), looks up the room strategy and agent pids and expands mentions before the peer safety net.
  - Human-sender turn creation (`:2223`): in `mentioned_only` rooms a root message creates turns only for mentioned agents, agents named by `@name`, and the `#room` query representative; `[DELEGATED]`/`[ROOM_QUERY]` prefixes stay room-wide. The #719 branch that marked every agent woken for unmentioned messages is removed.
- `packages/cluster/anygarden/messages/router.py`: the REST send path applies the same expansion (`_expand_room_mentions`); the `wake_trigger` block is untouched.
- `packages/agent/anygarden_agent/integrations/base.py`: rule 6 returns INGEST_ONLY (SKIP when `_context_window_opt_out`); a `via: "everyone"` entry counts as reflected in a thread reply when the content has `@everyone`. `packages/agent-ts/src/routing/should-respond.ts` follows in lock-step.
- `packages/cluster/anygarden/api/v1/agents.py`: `everyone` (trimmed, case-insensitive) is rejected with 422 on create and rename.
- Frontend: `MentionPopover` gains kind `everyone`; `MessageInput` lists `@everyone` first in the `@` list when the room has an agent, inserts the literal `@everyone `, and with `showMentionHint` shows a muted line when the draft calls no one; `ChatPage` enables the hint for `mentioned_only` rooms with two or more agents; en/ko strings in `i18n/catalogs/chat.ts`; `Room.speaker_strategy` added to the type.
- `docs/design/11-anonymous-guests.md`: permission matrix row for `@everyone`.
- Tests: `test_expand_room_mentions.py`, `test_mention_only_routing.py` (turn counts 0 / 1 / N / one-agent room, legacy name, thread `@everyone`, round-robin and orchestrator unchanged, REST expansion, room-query representative), `@everyone` parser cases, guest cases, reserved name, #719 tests switched to `@everyone` plus the #737 regression and agent `@everyone` depth/budget; agent and agent-ts policy tests updated.

## Decisions

- Where the "who is called" decision lives:
  - Agent and server each decide (agent from the roster cache, server from the DB) — rejected: two sources of truth is the root cause of #737, and the roster cache has gone stale before (#732).
  - Server records a new metadata key (`route: everyone|sole_agent`) — rejected: agent rules, thread checks, #719, turn creation and the peer safety net would each need a new branch, and missing one breaks `@everyone` on that path.
  - Chosen: server expands into ordinary `user` mentions with a `via` marker. Every later step already handles explicit mentions. `metadata.mentions` was already server-derived routing data ("never client authority"), and the frontend renders mentions from content tokens, not metadata (only `lib/handoff.ts` reads it, gated on `[HANDOFF]`).
- Unmentioned human messages become INGEST_ONLY rather than SKIP, so an agent mentioned later still has the preceding conversation; this mirrors the existing unmentioned-thread-reply rule.
- `@everyone` is a literal keyword, not a new token, so it works when typed and LLMs can write it; this requires reserving the agent name `everyone`.
- The UI hint is a quiet line under the input instead of a blocking confirmation or a toast, so human-only conversation is not interrupted.
- Server turn filtering also matches legacy `@Name` mentions and keeps the `#room` representative and task-init prefixes, because the agent's `decide_policy` still answers those; without this they would respond without a tracked turn.
- Revisit if: agents and server are deployed at different versions (an old agent keeps answering unmentioned messages; a new agent behind an old server leaves one-agent rooms silent), or if a room in `orchestrator` mode without a pinned orchestrator needs the one-agent shortcut — it now behaves like `mentioned_only` without the expansion.

## Result

- In `mentioned_only` rooms with two or more agents, unmentioned human messages get no agent reply (by design); `@name` wakes one agent, `@everyone` wakes all, one-agent rooms answer as before.
- An unmentioned human message no longer marks agents as woken, so a following peer mention keeps its token (#737 path).
- Tests: cluster 2023 passed; agent 669 passed; agent-ts 121 passed; frontend vitest 760 passed, `npm run build` OK, Playwright e2e 31 passed.
- Pending: live check in a room with three agents and a 1:1 room (plan step 10).
