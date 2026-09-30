# fix(chat): drop duplicate status strip and move composer actions inside input (#770)

- Commit: `a549f77` (a549f771bd8bea1ee538b10c40c9c8d580d30748)
- Author: Changyong Um
- Date: 2026-09-30T23:54:05+09:00
- PR: #770

## Situation

In a room, an agent's progress (e.g. `garden-dev · 응답 준비 중…, garden-pm · 동료 답 기다리는 중 (0/1)…`) was rendered twice: once above the typing bubble inside the message stream (`ChatArea`) and again in a fixed strip above the composer (`TypingIndicator`). The composer also placed an outline attach button, the textarea and a send button side by side with mismatched heights.

## Task

- Show agent/participant progress in only one place.
- Rebuild the composer so the add (`+`) and send controls sit inside a single rounded input, with cleaner icons.
- `+` opens a drop-up menu whose entry is file attachment.

## Action

- `packages/cluster/frontend/src/pages/ChatPage.tsx`: removed the `TypingIndicator` render and import.
- Deleted `components/TypingIndicator.tsx` and its test; removed the now-unused `chat.isTyping` / `chat.twoTyping` / `chat.othersTyping` catalog keys in `i18n/catalogs/chat.ts`.
- Added `lib/typingStage.test.ts` to keep coverage for the `waiting_peers` count label that the deleted test exercised.
- `components/MessageInput.tsx`: one rounded container with `focus-within` ring; 32px round `+` button (lucide `Plus`) toggling a `role="menu"` drop-up (closes on outside pointer / Escape) holding a "파일 첨부" item that clicks the hidden file input; borderless transparent textarea; 32px round send button with `ArrowUp`, brand fill when sendable and muted when disabled. Added `chat.addMenu` key (en/ko).

## Decisions

- Options: keep the bottom strip and drop the in-stream bubble, or keep the in-stream bubble and drop the strip. Chose the in-stream bubble because `GuestRoomPage` already relied only on it, so both surfaces now match, and it sits next to the conversation where the reply will appear.
- Trade-off accepted: human typing now shows name + spinner without the "입력 중…" wording.
- Drop-up is a small local menu inside `MessageInput` rather than a new Radix dropdown dependency; there is only one item today. Revisit if more composer actions are added.
- Visual direction follows the user's reference (ChatGPT-style pill input with inner `+` and round send).

## Result

- Status text appears once per room view; composer controls are aligned inside one input across room, thread panel, inline thread and guest views.
- `npm run build` passes; vitest 99 files / 791 tests pass. Verified closed and open-menu states by screenshot on the local dev server; live agent progress display was not observed (empty room).
