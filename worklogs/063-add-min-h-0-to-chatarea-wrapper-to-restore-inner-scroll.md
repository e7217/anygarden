# fix(rooms): add min-h-0 to ChatArea wrapper to restore inner scroll (#63)

- Commit: `eebba22` (eebba22e1e450bd34278b92c08cd1b50ccace369)
- Author: Changyong Um
- Date: 2026-04-16T11:56:02+09:00
- PR: #63

## Situation

PR #59 introduced `RoomQueryBanner` above the message list by wrapping `ChatArea`'s contents in a new flex column `<div>`. The wrapper did not set `min-h-0`, so CSS's default `min-height: auto` on flex items kept the wrapper sized to its content rather than its available space. That broke the flex height-constraint chain from `h-dvh` down to the Radix `ScrollArea` Viewport: the Viewport's `clientHeight` equalled its `scrollHeight`, so inner scroll silently stopped working.

## Task

- Restore the flex height chain so `ScrollArea` Viewport can overflow and scroll internally.
- Stop `scrollIntoView()` (used by the banner's "scroll to" handler) from scrolling the root `h-dvh overflow-hidden` container, which had been pushing the sidebar and header off-screen.
- Keep the fix minimal — no layout rewrite, no JS behaviour change.

## Action

- `packages/cluster/frontend/src/components/ChatArea.tsx:230` — added `min-h-0` to the wrapper's Tailwind `className` so the flex item can shrink below its intrinsic content height.

## Result

- Wrapper now caps at the main column's available height; `ScrollArea` Viewport regains `clientHeight < scrollHeight` and scrolls normally.
- `scrollIntoView()` stays inside the Viewport, so sidebar/header no longer disappear when jumping to a room-query result.
- One-line change, no type or test changes; `npm run build` (tsc -b + vite build) passes clean.
