# fix(rooms): include source display_name in room_query metadata (#155)

- Commit: `beddf65` (beddf65e71fa45f3d780e5ab07852540e6269cf0)
- Author: Changyong Um
- Date: 2026-04-19T12:55:16+09:00
- PR: —

## Situation

Cross-room queries rendered their forward bubble in the target room as `↪ #<source room> · @<last-6-hex>` — a UUID hash instead of the sender's display name. `parseForward` carried only `source_participant_id`, and `MessageBubble.resolveUser` resolves names against the *target* room's `participants` map, which never contains the source-room user. The `.slice(-6)` fallback therefore engaged on every cross-room forward, hiding who actually asked. This was the forward-direction twin of the responder-name bug #153 fixed in the other direction two commits earlier — same class of defect, same data-ownership root cause.

## Task

- Surface the source user's real name on every cross-room forward bubble without introducing a new HTTP round-trip or admin-gated API.
- Keep the `[ROOM_QUERY] ...` body prefix intact so the server's `should_respond` startswith gate keeps working.
- Stay wire-compatible both directions: legacy frontends must ignore the new field, legacy agent SDKs must fall through to the existing hash fallback without crashing.
- Mirror #153's pattern so the two fixes are one-grokkable when read side by side.

## Action

- **Server (`packages/cluster/doorae/ws/handler.py`)** — where `room_query` metadata gets assembled at cross-room mention detection, resolve the sender's `display_name` using the same 3-way chain `rooms/router.py:290-302` uses (`User.display_name` → email local-part → `"Guest"`) and add `source_participant_name` to the metadata dict. Added `User` to the model imports; resolution is a single `session.get(User, ...)` on the already-open `rq_db` session, no new round-trip vs. existing code path.
- **Agent (`packages/agent/doorae_agent/integrations/room_query.py`)** — added `source_participant_name: str | None` to the `RoomQuery` dataclass, parsed it in `parse_room_query` (empty string collapses to `None` so the frontend `||` short-circuit works), and threaded it into `room_query_forward` metadata inside `execute_room_query`. When the value is falsy, the key is omitted entirely — pre-#155 wire shape preserved for legacy servers.
- **Frontend selector (`packages/cluster/frontend/src/lib/room-query.ts`)** — `RoomQueryForwardMeta` grew an optional `source_participant_name?: string | null`; `parseForward` accepts only non-empty strings (anything else collapses to `null`) so an empty server value can't break the fallback chain.
- **Frontend render (`packages/cluster/frontend/src/components/MessageBubble.tsx`)** — changed the `srcUserLabel` expression in the forward variant to prefer `forwardMeta.source_participant_name` before falling back to `resolveUser(source_participant_id)` → `.slice(-6)`. Used `||` (not `??`) so an empty string never produces a half-rendered badge.
- **Tests** — `test_ws_handler.py`: fixture user now has `display_name="Alice"`; existing `test_room_mention_attaches_room_query` asserts the new field; added `test_room_mention_source_name_falls_back_to_email_local_part` covering the no-display_name user case. `test_room_query.py`: `TestParseRoomQuery` covers the new field and its `None` default; `_make_query` ships `source_participant_name="Alice"`; added `test_forward_metadata_omits_source_name_when_absent`. `room-query.test.ts`: two new cases for `parseForward` covering presence/absence. `MessageBubble.test.tsx`: two new cases — `prefers source_participant_name over resolveUser when cross-room` and a pre-#155 legacy-fallback regression guard.

## Decisions

`.tmp/plan-155-room-query-forward-source-name.md` §3.2 weighed four options:

- **A. Server attaches `source_participant_name` at metadata assembly; agent relays; frontend prefers** — chosen.
- B. Agent fetches source-room participants at query start — rejected: adds a network round-trip and duplicates the display_name resolution rules in agent code; server already holds the data at metadata-assembly time.
- C. Frontend batch-fetches unknown pids via `/api/v1/users` — rejected: that surface is admin-gated, introduces N HTTP calls per forward bubble, and produces a render-then-flicker UX.
- D. Attach `sender_display_name` to every cluster broadcast — rejected as scope inflation: `room_query_forward` is the only variant with a cross-room audience, and touching the generic message schema risks DM/chat regressions.

What tipped toward A: #153 already solved the responder direction with the exact same "capture already-available name at metadata-assembly time, let the frontend prefer it" pattern. Using the same shape on the forward direction keeps the two fixes legible as a pair, and the server-side resolution reuses the 3-way chain from `rooms/router.py` that renders the same names everywhere else in the UI — no new authority of truth.

A sub-decision on the resolution rule (`.tmp/plan-155-...md` §3.2): extract a shared `resolve_participant_display_name(db, participant)` helper across `handler.py` and `rooms/router.py`, or inline? Inlined. The 3-way chain is five lines, the two call sites are far apart, and extracting a helper is a refactor with its own regression surface — separated into a follow-up so this commit stays a pure bug fix. A `# Rule must stay in sync with rooms/router.py:290-302` comment marks the synchronization point.

Assumptions that would force a revisit: if `UserClaims` ever gains `display_name` (it currently doesn't, unlike `GuestClaims`), the extra DB lookup becomes redundant and could be swapped for `identity.claims.display_name`. Also, the name is a snapshot at message-creation time: if a user renames mid-query, the forward badge shows the old name — intentional, matches #153's snapshot semantics.

## Result

- All targeted tests green: 587/587 cluster, 232/232 machine, 155/156 agent (pre-existing `test_openai.py::test_integrate_registers_handler` failure unrelated — needs `OPENAI_API_KEY` in the environment, identical on `main`), 231/231 frontend.
- `npm run build` (tsc + vite) succeeds; no new type errors.
- Wire-compatible both ways: legacy frontends ignore the new `source_participant_name`; legacy agent SDKs relay nothing and the frontend's `||` short-circuit drops into the existing `resolveUser` → hash chain (so upgrading the server alone doesn't make things worse, only the agent+frontend combo closes the loop).
- Paired with #153, cross-room query UI now shows real names on both the inbound forward badge and the outbound result card.
