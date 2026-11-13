# fix(chat): show a loading placeholder while room history loads (#1044)

- Commit: `0571078` (0571078c6c85b6c82ccf417742360f8564b23f29)
- Author: Changyong Um
- Date: 2026-11-13T18:38:48+09:00
- PR: #1044 (issue)

## Situation

Opening a chat room fetches its history over REST (`GET /rooms/{id}/messages?since_seq=0&limit=100`, which returns the latest 100 messages), but `useWebSocket` tracked no loading state. Until the response arrived `ChatArea` saw zero messages and rendered the "No messages yet / Start the conversation" prompt — wrong for rooms with history, and visible longest in the busiest rooms.

## Task

- Distinguish "history still loading" from "room is empty" in the chat timeline.
- Show a loading state only while nothing is displayed yet; live messages that arrive first must still render.
- Apply to both the member chat (`ChatPage`) and the guest room (`GuestRoomPage`).
- Follow DESIGN.md: semantic tokens only, state not conveyed by motion alone, a11y status announcement.

## Action

- `packages/cluster/frontend/src/hooks/useWebSocket.ts`: added `historyLoading` to the per-visit `RoomState` (initially `true` when a room is selected). The history effect clears it in a `finally` guarded by `accepts()` (so an aborted or stale visit never touches the new one), and immediately when there is no auth token. Exposed in the hook's return value.
- `packages/cluster/frontend/src/components/ChatArea.tsx`: new optional `loading` prop; with `loading && messages.length === 0` it renders `ChatHistorySkeleton` — alternating received/sent rows (`animate-pulse`, `--color-surface-hover`) plus a visible caption, wrapped in `role="status"`, `data-testid="chat-history-loading"`.
- `packages/cluster/frontend/src/pages/ChatPage.tsx`, `pages/GuestRoomPage.tsx`: pass `loading={historyLoading}`.
- `packages/cluster/frontend/src/i18n/catalogs/chat.ts`: `chat.loadingMessages` (en/ko).
- Tests: `src/components/ChatArea.loading.test.tsx` (placeholder vs. empty prompt vs. early messages) and `e2e/chat-history-loading.spec.ts` (holds the messages request open, asserts the placeholder, releases, asserts it disappears; empty history shows the prompt).

## Decisions

- Options weighed: a spinner in the empty area, a skeleton of message rows, or delaying the empty prompt with a timer. A conversation-shaped skeleton was chosen because it previews the layout that will appear and avoids a layout jump; a timer would still lie about the room being empty on slow loads.
- Loading state lives in the hook's per-visit snapshot (not a separate `useState` in the page) so the existing scope guards prevent a previous room's response from clearing the next room's loading flag.
- Skeleton colour: `--color-surface-alt` was nearly invisible on white in the light theme; switched to `--color-surface-hover`.
- Under reduced motion the global rule stops `animate-pulse`; the visible "Loading messages…" caption keeps the state readable, so no special busy-signal animation was added.
- No delay before showing the skeleton: fast loads may flash it briefly, judged better than flashing the empty prompt. Revisit if the flash is reported as noise (e.g. ~200ms delay).

## Result

Rooms show a skeleton with "Loading messages…" until history arrives; the empty-room prompt appears only after loading finished with no messages. Related vitest (39) and Playwright (`chat-history-loading`, `chat-width`; 13) pass, `npm run build` passes. Not checked against a live server. Separately noted: regular rooms have no way to load messages older than the latest 100 (no `before_seq` on `/rooms/{id}/messages`) — out of scope here.
