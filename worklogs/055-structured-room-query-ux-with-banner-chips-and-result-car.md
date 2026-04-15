# feat(rooms): structured room-query UX with banner chips and result cards (#55)

- Commit: `84c1595` (84c15950ccbe2569c46d681393c1b36163ab334a)
- Author: Changyong Um
- Date: 2026-04-16T00:54:06+09:00
- PR: #55

## Situation

Cross-room queries (`#room` mentions) only exposed raw `[ROOM_QUERY] ...` and `[취합 결과] ...` prefixes in chat. Users couldn't tell which questions were in flight, where forwarded messages originated, or how many agents had responded. The solo path (empty target room) returned silently, leaving the source room stuck waiting with no feedback. There was also no structured data on the wire that the client could use to render progress or per-agent responses.

## Task

- Tag the originating question with `query_id` + `role` + `source_participant_id` so the client can pair a question with its result without adding a new WS event type.
- Thread `query_id` + `source_participant_id` through `parse_room_query` / `RoomQuery` / `execute_room_query` so forwarded messages and result broadcasts carry the pairing metadata.
- Emit a structured `room_query_result` envelope (`responses[]`, `status`, counts) for completed / timeout / solo paths, and deliver a result even when the representative is alone so banners resolve.
- Keep wire bodies (`[ROOM_QUERY] ...`, `[취합 결과] ...`) unchanged so `should_respond`'s startswith path keeps working.
- Build banner chips, a structured result card, and a forward variant for `MessageBubble`, with tests covering every state.

## Action

- `packages/cluster/doorae/ws/handler.py:340-367` — added `role="question"`, `query_id=str(uuid4())`, and `source_participant_id=participant.id` to the user message metadata.
- `packages/agent/doorae_agent/integrations/room_query.py`
  - extended `RoomQuery` with `query_id` + `source_participant_id`, updated `parse_room_query` accordingly.
  - added `_deliver_result` centralising the completed / timeout / solo envelopes (same body shape, only `status` + `responses` differ).
  - the `expected_count == 0` branch now delivers a `status="solo"` result instead of returning silently.
  - forward send now carries `metadata={"room_query_forward": {...}}`; completion and timeout paths call `_deliver_result` with structured `responses`.
- `packages/cluster/frontend/src/lib/room-query.ts` — new pure selectors `parseQuestion`, `parseForward`, `parseResult`, `stripRoomQueryPrefix` (plus 16 unit tests in `room-query.test.ts`).
- `packages/cluster/frontend/src/components/RoomQueryBanner.tsx` — new chip strip (`role="status"`, `aria-live="polite"`) with pending/completed/timeout/solo variants, `onScrollTo` for completed click, `onDismiss` for timeout/solo ×.
- `packages/cluster/frontend/src/components/RoomQueryResultCard.tsx` — structured result render with 3px left accent bar, source badge, per-agent expandable response cards (default expanded), and fallbacks when participant name or target room name are missing.
- `packages/cluster/frontend/src/components/MessageBubble.tsx` — two new branches before the isMine/other render: a forward variant (strips `[ROOM_QUERY] ` at render time) and a result variant that delegates to `RoomQueryResultCard` with a derived participant-name `Map`.
- `packages/cluster/frontend/src/components/ChatArea.tsx` — rebuilt to derive `pendingQueries` from `messages`, track user-dismissed ids, and wire an `IntersectionObserver` on the Radix scroll viewport so completed chips auto-dismiss when their result bubble appears.
- Added RTL component tests (`RoomQueryBanner.test.tsx`, `RoomQueryResultCard.test.tsx`, `MessageBubble.test.tsx`) using per-file `// @vitest-environment jsdom`, plus `afterEach(cleanup)` to keep the shared jsdom document stable across tests.
- `packages/cluster/frontend/package.json` — added `@testing-library/react`, `@testing-library/jest-dom`, `jsdom` as devDependencies.
- `packages/cluster/tests/test_ws_handler.py`, `packages/agent/tests/test_integrations/test_room_query.py` — extended suites assert new metadata fields on forward, completed, timeout, and solo paths and guard the body-prefix regression.

## Result

- 24 cluster tests + 18 agent tests pass (`uv run pytest`), 45 frontend tests pass (`npm test -- --run`). Frontend typecheck + bundle via `npm run build` succeeds.
- Source-room banner now shows a live chip per `query_id` and transitions pending → completed/timeout/solo. Target-room bubble identifies forwards with a `↪ #src · @user` badge and a clean body. Result bubble renders per-agent responses.
- Solo path no longer leaves the banner stuck; empty target rooms get an explicit "응답 가능 에이전트 없음" chip + card.
- Wire-format bodies are untouched, so backend `should_respond` keeps routing. Presence-service work (#54) remains untouched: participant display names are exposed as a plain `Map<string,string>` that #54 can extend when it lands.
