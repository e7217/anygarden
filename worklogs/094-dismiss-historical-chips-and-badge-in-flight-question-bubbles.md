# feat(rooms): dismiss historical chips and badge in-flight question bubbles (#94)

- Commit: `586dab8` (586dab8b9cf20298b9a71a40f151c6eb2488059b)
- Author: Changyong Um
- Date: 2026-04-17T15:21:22+09:00
- PR: #94

## Situation

After `#93` restored correct timestamps, three latent UX issues in `RoomQueryBanner` surfaced. (1) Chip state was derived from `messages` on every render, so re-entering an old room re-surfaced every `timeout`/`solo`/`completed` chip the user had already acknowledged — with no way to dismiss. (2) `completed` chips had no manual close affordance; if the user never scrolled to the result card, `IntersectionObserver` never fired and the chip lingered. (3) The only visual signal that a `room_query` was in flight lived in the banner; the originating question bubble in the main thread had no indicator, making it hard to tie "this pending chip" back to "this message".

## Task

- Preserve the "new chip is attention-worthy" UX — seeding must not suppress *fresh* results that arrive after the user enters the room.
- Add a manual dismiss on `completed` chips without regressing the existing "click chip → scroll to result → auto-dismiss" flow.
- Render a low-key pending indicator on the question bubble without touching unrelated message variants (forward/result cards, plain messages).
- Keep every change to `packages/cluster/frontend` — no server-side work.

## Action

- **Phase 1 (BrailleSpinner extraction)**: moved the inline `BrailleSpinner` from `ChatArea.tsx:11-24` into `packages/cluster/frontend/src/components/BrailleSpinner.tsx`. `ChatArea` now imports it; `MessageBubble` will reuse it for the pending badge.
- **Phase 2 (`completed` X button)**: `RoomQueryBanner.tsx:114-146` restructured the chip from a single `<button>` into a `<span>` wrapping two sibling buttons — a scroll trigger (`aria-label="결과로 이동: #{room}"`) and an X dismiss. Avoids nesting `<button>` inside `<button>`. `data-testid`/`data-status` live on the wrapper so existing assertions still work. The scroll test now targets the scroll button's aria label instead of the chip root.
- **Phase 3 (history seed)**: added a pure `seedTerminalDismissals(messages, currentRoomId)` in `packages/cluster/frontend/src/lib/pending-queries.ts:137-152` that returns the `query_id`s of every terminal result scoped to the current room. `ChatArea.tsx:78-95` replaced the unconditional `setDismissedIds(new Set())` effect with a `seededRoomRef` pattern: clears on a new room, waits for a non-empty `messages` snapshot, then seeds once per room. New results arriving afterwards are *not* seeded because the ref already matches `currentRoomId`.
- **Phase 4 (question pending badge)**: `ChatArea.tsx:78-87` computes `pendingQueryIds` from `pendingQueries` filtered by `status === 'pending'` and passes it as an optional `Set<string>` prop to `MessageBubble`. `MessageBubble.tsx` calls `parseQuestion(message)` and renders a `BrailleSpinner` + "응답 대기 중" span (`data-testid="question-pending-badge"`) next to `formatTime(...)` on both the own-message and other-participant branches. Prop is optional so existing callers (search, bookmarks, tests) stay valid.
- **Tests (9 new, 117 total pass)**:
  - `RoomQueryBanner.test.tsx` — completed X button dispatches `onDismiss` but not `onScrollTo`.
  - `pending-queries.test.ts` — `seedTerminalDismissals` empty/terminal/cross-room/question-only cases.
  - `MessageBubble.test.tsx` — four-case visibility matrix: question+pending id → badge; question+other id → none; question+no prop → none; non-question + matching id → none.

## Decisions

Mined from `.tmp/plan-94-B-chip-badge-ux.md` (§3.2):

- **History seed shape — one-shot sentinel vs. per-messages re-seed vs. server-side per-user state**: chose the sentinel because per-messages seeding would erase freshly-arriving chips the user hasn't seen, and server-side per-user "already seen" state would require a migration and cross-device sync outside this issue's scope. The sentinel is a three-line primitive (`useRef` + `useEffect` that early-returns on match) that delivers 80% of the value.
- **Purity of `seedTerminalDismissals`**: moved out of `ChatArea` on purpose. The original plan noted writing a full-blown ChatArea integration test was heavy because of `useRooms` and other provider dependencies. A pure function over `ChatMessage[]` lets vitest exercise it in microseconds and keeps the React component thin.
- **`completed` chip structure — `span` + two `<button>` siblings vs. `role="button"` wrapper with nested `<button>`**: picked the stricter structure. The `role="button"` trick works in most browsers but violates ARIA nesting rules for interactive elements. Writing proper sibling buttons cost one test update (scroll click now targets the inner button's aria-label) — cheap enough to justify the cleaner semantic.
- **Pending badge placement — inline with timestamp vs. overlaid on bubble vs. inside body text**: inline with the timestamp keeps time-adjacent status grouped and won't clip on narrow chat widths. Overlay on the bubble risks clashing with the bookmark icon; inside body text would confuse users about whether the spinner is part of the message content.
- **Pending badge color/size**: used `text-[var(--color-foreground-subtle)]` + `text-[11px]` to match the timestamp's weight — per `DESIGN.md` "whisper-weight" guidance the badge should feel like metadata, not a CTA.

Assumptions worth revisiting:
- The seed ref keys off `currentRoomId` only. If a future feature surfaces multiple overlapping rooms in one `ChatArea` instance, the ref model would under-seed; revisit when that feature lands.
- `pendingQueryIds` is a `Set<string>` recomputed on every `pendingQueries` change. Given `pendingQueries` is capped by the 100-message history window, this is O(n) per render — fine today but worth swapping for a memoized derivation on the consumer side if the chat grows to thousands of messages.

## Result

- `npm run build` (tsc + vite) passes.
- `cd packages/cluster/frontend && npm test --run` → 117 passed, 9 new across three files.
- `cd packages/cluster && uv run pytest` → 403 passed (no regression from the Phase 2 chip restructure, which is frontend-only).
- Manual verification pending on a live dev server: entering an old room should not show historical chips; sending a fresh `room_query` should render both a banner pending chip and a badge on the originating question bubble; closing `completed` chips with the new X should dismiss them without scrolling.
- Depends on `#93` (merged in `cf0bd25`) so timestamps render correctly — without that fix, badges would appear alongside wrong-time bubbles.
