# fix(rooms): drop orphan pending room_query chips after 7min TTL (#66)

- Commit: `919e675` (919e6758e191c571203ad99b50da5351742d8116)
- Author: Changyong Um
- Date: 2026-04-16T14:59:02+09:00
- PR: #66

## Situation

`room_query` banner chips are seeded in `ChatArea` from the message stream: each originating question adds a `pending` entry that a later `room_query_result` upgrades to `completed`/`timeout`/`solo`. When the agent process dies before `COLLECT_TIMEOUT` (5 min) fires, no result message is ever emitted, so the `pending` chip lingers. User dismissals live only in React-local `dismissedIds`, so refreshing the page resurrects the ghost chip indefinitely.

## Task

- Keep the banner self-healing without introducing server state, localStorage, or polling.
- Drop only orphan pending chips; never hide a terminal result (completed / timeout / solo) or suppress a legitimate, recent pending.
- Make the derivation unit-testable (previously inline in `ChatArea.tsx`).
- Hold the `PendingQuery` external type contract stable so `RoomQueryBanner` keeps working.
- Avoid `useMemo` dep thrash — `new Date()`/`Date.now()` in deps would loop.

## Action

- Extracted `buildPendingQueries` out of `packages/cluster/frontend/src/components/ChatArea.tsx` (removed the inline 60-line function) into a new `packages/cluster/frontend/src/lib/pending-queries.ts`. `ChatArea` now imports the helper and no longer references `parseQuestion`/`parseResult` directly.
- Added a `now: Date` last parameter and a `PENDING_TTL_MS = 7 * 60 * 1000` module constant (`COLLECT_TIMEOUT` 5 min + 2 min network/DB/broadcast slack).
- Tracked the originating question timestamp on an internal `_question_created_at` field, then in the final filter skipped entries where `status === 'pending' && !result_message_id && now - questionTime > PENDING_TTL_MS`. The internal field is stripped via destructuring before returning so `RoomQueryBanner`'s `PendingQuery` consumer sees no schema change.
- Treated malformed `created_at` defensively: `Number.isFinite` guard keeps the chip visible rather than hiding it on a parse error.
- `ChatArea.tsx` `useMemo` now passes `new Date()` inline with deps left at `[messages, currentRoomId, dismissedIds, resolveRoomName]`; added a comment explaining why `new Date()` must not be in the dep array.
- New test file `packages/cluster/frontend/src/lib/pending-queries.test.ts` mirrors `room-query.test.ts`'s `msg()` helper pattern and covers 11 cases: empty messages, other-room filter, recent pending inclusion, 8-min orphan exclusion, 8-min + result upgrade to completed, old timeout preserved, old solo preserved, `dismissedIds` exclusion, 6:59 boundary inclusion, 7:01 boundary exclusion, and malformed `created_at` defensive path.

## Result

- `npm test` → 60 green (49 prior + 11 new in `pending-queries.test.ts`). `npm run build` (tsc + vite) clean.
- Orphan pending chips now disappear on the next re-render after the 7-minute window; terminal chips and user-acknowledged dismissals behave exactly as before.
- Backend, `RoomQueryBanner`, `MessageBubble`, `MessageInput`, and `lib/room-query.ts` are untouched; the fix is frontend-only and localStorage-free per plan scope.
- A 60s interval tick for idle-room re-render is deliberately deferred — typing/presence/new-message events already drive frequent re-renders in practice; revisit only if observation shows gaps.
