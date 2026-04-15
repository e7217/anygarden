# fix(rooms): break the [ROOM_QUERY] forwarding loop (#42)

- Commit: `a084e9b` (a084e9bc3a78f8f4efd3042544600312e4bd058c)
- Author: Changyong Um
- Date: 2026-04-15T17:50:30+09:00
- PR: #42

## Situation

When a user typed `#room` in a room that had a representative agent, the agent forwarded the question to the target room's representative, which in turn forwarded it back — producing an infinite `[ROOM_QUERY] [ROOM_QUERY] [ROOM_QUERY] …` ping-pong. Each hop prepended another `[ROOM_QUERY]` prefix and alternated senders, filling chat history until someone noticed and killed the agents.

## Task

- Stop the agent SDK from forwarding the literal `<#room:…>` token, which let the server re-detect the mention on every hop.
- Add a server-side guard so that even if some other SDK (or a future regression) emits the token, the loop cannot restart via an agent-originated message.
- Preserve the legitimate human workflow: a user typing the literal string `[ROOM_QUERY]` in message content must still get normal room routing.
- Leave the duplicate "representative permanently joins the target room" side-effect for a follow-up (tracked, out of scope here).

## Action

- **Agent SDK** — `packages/agent/doorae_agent/integrations/room_query.py` (+28): new `_strip_room_mention` helper removes `<#room:…>` tokens and tidies the whitespace they leave before `execute_room_query` forwards the question. When stripping produces an empty string the forward falls back to the original content (so the question itself is never lost) and the server's new guard neutralises the hop anyway.
- **Cluster server guard** — `packages/cluster/doorae/ws/handler.py` (+28): skip `room_query` metadata attachment when `identity.kind == "agent"`. An earlier draft keyed off `content.startswith("[ROOM_QUERY]")` but reviewer pointed out that trapped a human typing the literal string into losing room routing with no feedback — dropped in favour of the agent-identity check alone, which closes the loop at the source.
- **Tests**:
  - Agent — `packages/agent/tests/test_integrations/test_room_query.py` (+89): 6 `_strip_room_mention` unit cases (single/multiple/idempotent/token-only/whitespace edge), `test_forward_strips_room_mention_token`, and `test_forward_falls_back_when_strip_empties_content`.
  - Cluster — `packages/cluster/tests/test_ws_handler.py` (+87): `test_agent_sender_does_not_trigger_room_query` verifies `room_query` metadata is NOT attached when an agent token sends a fresh `<#room:…>`; `test_user_typing_room_query_prefix_still_routes` pins the dropped content-prefix guard — human typing `[ROOM_QUERY] <#room:…>` still routes normally.

## Result

The forward chain is broken at both ends (SDK strip + server identity check). Cluster 328 + agent 102 passing; no other regressions. Follow-up: representative auto-joining the target room on forward remains, tracked separately.
