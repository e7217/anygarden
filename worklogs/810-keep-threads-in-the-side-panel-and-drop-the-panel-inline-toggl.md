# refactor(chat): keep threads in the side panel and drop the panel/inline toggle (#810)

- Commit: `46ee048` (46ee04857209abb6343f5171fee9be0043803ff5)
- Author: Changyong Um
- Date: 2026-10-13T14:33:55+09:00
- PR: #810

## Situation

The chat UI shipped with two ways to read a thread: a side panel that opens next to the timeline, and an inline expansion that inserts replies underneath the root message. A header button and a settings-menu entry let users flip between the two. The inline mode was always framed as a temporary comparison scaffold, not a settled product direction, and the evaluation concluded that the panel experience was clearly superior. The toggle therefore continued to exist past its useful life, adding code surface and confusing users.

## Task

- Remove the inline thread display path entirely while leaving the panel path intact and unmodified.
- Delete `ThreadInline` component and `useThreadDisplayMode` hook.
- Strip the toggle button from `RoomHeader` and the corresponding settings-menu entry from `RoomSettingsMenu`.
- Remove the `renderInlineThread` render-prop slot from `ChatArea`.
- Drop the i18n keys that were only used by the removed controls.
- Ensure a stale `localStorage` value of `"inline"` is silently ignored rather than crashing or reverting behaviour.
- Pin the correct post-removal behaviour with an e2e test: a deep-link `?message=msg-reply` must open the side panel and focus the linked reply even when the old preference is set in storage.

## Action

- **`packages/cluster/frontend/src/components/ThreadInline.tsx`** — deleted in full (111 lines). The component rendered replies beneath a root with an indent/left-rule layout and forwarded composer events to the room's WebSocket.
- **`packages/cluster/frontend/src/hooks/useThreadDisplayMode.ts`** — deleted in full (62 lines). The hook persisted `'panel' | 'inline'` to `localStorage` under `anygarden.threadDisplayMode` and exposed a toggle callback.
- **`packages/cluster/frontend/src/components/ChatArea.tsx`** — removed the `renderInlineThread` prop from the interface and the render call that injected the inline expansion between messages. Also dropped the unused `ReactNode` import.
- **`packages/cluster/frontend/src/components/RoomHeader.tsx`** — removed the `threadDisplayMode` / `onToggleThreadDisplayMode` props, the toggle `<button>` in the header strip, and the `PanelRight` / `ListTree` lucide imports.
- **`packages/cluster/frontend/src/components/RoomSettingsMenu.tsx`** — removed the same two props from the menu interface, deleted the `safeActions` entry that wired the menu item to the toggle, and removed the `PanelRight` lucide import.
- **`packages/cluster/frontend/src/i18n/catalogs/chat.ts`** — removed six keys from both en and ko (12 lines): `threadModeTitle`, `threadModeAction`, `threadModePanel`, `threadModeInline`, plus `collapseThread` and `collapse`, which only `ThreadInline` used.
- **`packages/cluster/frontend/src/pages/ChatPage.tsx`** — removed the wiring layer that created the `useThreadDisplayMode` state and passed `threadDisplayMode` / `onToggleThreadDisplayMode` / `renderInlineThread` down to `RoomHeader` / `ChatArea` (32-line reduction).
- **`packages/cluster/frontend/e2e/threads.spec.ts`** — removed three tests (`inline composer addresses same root`, `unsaved reply survives switching layouts`, `mobile overflow keeps thread layout available`) that exercised the now-deleted path. Added one new test: `a link to a reply opens it in the panel even with a stale inline preference`, which seeds `localStorage` with `"inline"`, navigates to `?message=msg-reply`, and asserts the panel is visible and the target message carries `data-message-focused="true"`. Also added a `context` API stub to support the deep-link navigation.

## Decisions

- **Panel over inline**: decided in the issue itself; `useThreadDisplayMode`'s own comment said both surfaces exist only "while the shape is being settled" and to delete the loser together with the hook. Both read the same `lib/threads.ts` index and post through the same WebSocket path, so the inline view was presentation only and cheap to restore from history if ever needed.
- **Hard removal vs. hiding the toggle or pinning `threadMode` to `'panel'`**: both alternatives leave unreachable code that still has to be tested, and fail the issue's acceptance criterion that `ThreadInline` / `renderInlineThread` / `useThreadDisplayMode` no longer appear in the codebase. Given that no external caller depended on `ThreadInline` or `useThreadDisplayMode` (both were internal to the frontend package), full deletion was cleaner and presented no migration risk.
- **e2e tests deleted, not rewritten**: the panel tests already cover the thread_root_id contract (`[MSG-05]`, keeping requirement coverage) and draft discard; "draft survives switching layouts" and the mobile menu entry have no panel equivalent once switching is gone.
- **Silent `localStorage` ignore**: the stale `"inline"` value is simply never read again; no explicit migration or removal was needed. The e2e test verifies the behaviour rather than documenting an assumption.
- **Assumption to revisit**: if a future requirement calls for an alternate thread layout (e.g. a compact digest view), the `renderInlineThread` render-prop pattern that was removed would be the natural re-introduction point. The panel-only assumption would need re-examining at that point.

## Result

- The panel/inline toggle no longer appears in the header or the settings menu.
- `ThreadInline` and `useThreadDisplayMode` are gone from the codebase.
- The i18n catalog loses six keys per locale.
- `ChatArea` and `ChatPage` are 47 lines shorter in total.
- Verified: `npm run build`, vitest 859/859, Playwright 43/43. A new e2e test confirms that deep-links continue to work correctly and that old `localStorage` values do not cause regressions.
- No behaviour change for users who were already using panel mode (the default and majority).
