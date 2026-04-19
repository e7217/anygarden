# fix(rooms): include responder display_name in room_query_result metadata (#153)

- Commit: `73e1b64` (73e1b64123a1d8b327d3be4de1348ee2136c090e)
- Author: Changyong Um
- Date: 2026-04-19T12:24:06+09:00
- PR: —

## Situation

Cross-room queries (`<#room:...>`) returned a `[취합 결과]` card whose per-response header read `@cfb47a`-style 6-char hex instead of the responder's display name. The representative agent's `room_query_result.responses[]` payload only carried `participant_id`, and `RoomQueryResultCard` looked names up exclusively against the *source* room's participants map — which never contained a cross-room agent. The fallback `nameFallback(pid).slice(-6)` engaged on every cross-room responder, masking who actually answered.

## Task

- Serialize the sender's human-readable name next to each response so the source-room card does not need a second round-trip.
- Avoid adding any extra network lookups — the agent already fetched the target room's participants (with `display_name`) to compute `expected_count` and offline annotations.
- Keep the fix wire-compatible: pre-#153 payloads, empty names, and replies from late-joiners must all fall through the legacy chain without crashing.
- Preserve the `[취합 결과]` body prefix and `room_query_result` schema's other fields (plan §6.1 startswith contract).

## Action

- **Agent runtime** — `packages/agent/doorae_agent/integrations/room_query.py` (+15): in `_register_multi_reply_callback`, build a `name_lookup: dict[str, str]` at registration time from the `agent_candidates` list the caller already threads in. `_on_reply` now appends `{"participant_id", "name", "content"}` (`name=name_lookup.get(pid, "")`), and `_deliver_result` serializes each response with the `name` field alongside `participant_id` and `content`.
- **Agent tests** — `packages/agent/tests/test_integrations/test_room_query.py` (+92): `_make_client` fixture participants now carry `display_name` ("Daisy"/"Ethan"); the existing `test_callback_collects_and_synthesizes` asserts the new response shape; two new cases — `test_response_metadata_includes_name` (name pulled from snapshot) and `test_response_name_falls_back_to_empty_for_unknown_sender` (late-joiner `pid` not in snapshot → `name == ""`).
- **Frontend selector** — `packages/cluster/frontend/src/lib/room-query.ts` (+13): `RoomQueryResponseEntry` grows an optional `name?: string`; `parseResult` threads `e.name` through only when it is a string, omitting the field otherwise so legacy payloads parse identically.
- **Selector tests** — `packages/cluster/frontend/src/lib/room-query.test.ts` (+47): two cases cover the presence and absence of `name` on the wire.
- **Result card** — `packages/cluster/frontend/src/components/RoomQueryResultCard.tsx` (+8): display-name expression changed to `r.name || participantNames.get(pid) || nameFallback(pid)`. `||` (not `??`) so an empty-string `name` from an unknown sender still drops into the legacy chain.
- **Component tests** — `packages/cluster/frontend/src/components/RoomQueryResultCard.test.tsx` (+46): `prefers server-provided response name over participantNames map` and `falls back to participantNames when response name is empty string`.

## Decisions

Plan `.tmp/plan-153-room-query-response-name.md` §3.2 weighed three options:

- **A. Serialize `name` server-side from already-fetched candidates** — chosen.
- **B. Frontend batch-fetches names for unknown pids** — rejected: `/api/v1/agents` is admin-gated (`get_admin_identity`), adds N HTTP requests per card, and produces a flicker where names pop in after the card renders.
- **C. Generic `sender_display_name` on every broadcast message** — rejected as scope-inflation: `room_query_result` is the only variant that needs this, and changing the generic message schema risks regressing DM/chat/system-message paths.

What tipped the scale toward A: `client.get_room_participants` is *already* called earlier in `execute_room_query` (for `expected_count` + offline annotation), and the result list already includes `display_name` from `ParticipantOut` serialization (router.py:284-324). One-line dict comprehension reuses data that is literally in scope — no new infrastructure, no new API surface.

Assumptions that would force a revisit: if `[ROOM_QUERY]` ever extends to accept human (user) responses, `agent_candidates` no longer covers all senders and the lookup fails silently for users. Plan §3.2 notes this is out of scope — today only agents in the target room answer `[ROOM_QUERY]`. Also, the name is a snapshot at response time: if an agent renames mid-collection, the stored name is stale. This is intentional (a result is semantically a snapshot), but worth remembering if someone later reports stale names.

## Result

- 22/22 agent room_query tests green; 586/586 cluster + 232/232 machine + 227/227 frontend green.
- Frontend `tsc -b && vite build` passes (no new type errors).
- Wire-compatible: legacy frontends ignore the new `name` field; legacy servers send no `name` and the new frontend falls through to the existing `participantNames` → last-6 chain.
- Unrelated `tests/test_integrations/test_openai.py::test_integrate_registers_handler` fails identically on `main` (needs `OPENAI_API_KEY`); pre-existing, not in scope.
