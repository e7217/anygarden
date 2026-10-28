# feat(layout): resize the sidebar and let side panels yield to a chat floor (#967)

- Commit: `649ecdd` (649ecdd05fd449c13ae79d0856914bc876889e0a)
- Author: Changyong Um
- Date: 2026-10-28T17:31:18+09:00
- PR: #967

## Situation

The desktop sidebar was hard-coded to 256 px (`md:w-64`) and the right rail was capped at `min(560px, 40vw)`. Users on wide monitors could not expand the navigation or context rail past those limits, and on narrower displays the rail's fixed cap could push the conversation column uncomfortably narrow. ROOM-13 did not exist; there was no spec for resizable panels or a minimum conversation width.

## Task

- Add a drag-resizable right edge to the desktop sidebar (min 208 px, max 480 px, default 256 px) with keyboard support (Arrow keys, Home/End) and double-click-to-reset; persist the width in `localStorage` as `anygarden_sidebar_width`.
- Extract the existing `RailResizeHandle` drag logic into a shared `PanelResizeHandle` component so both the sidebar handle and the rail handle stay in sync on behaviour without code duplication.
- Replace the right rail's hard cap (`max: 560`) with a wider bound (`max: 960`) and introduce a **chat-column floor** (`--chat-min-w: 28rem`) on `<main>`: both panels shrink toward their own minimums before the conversation column can go below 28 rem.
- Ensure gestures start from and commit the width currently on screen (not the stored value) so dragging a panel that is already squeezed by the floor responds immediately.
- Add ROOM-13 to `docs/requirements.md` and record the rule in `DESIGN.md`.

## Action

- **`packages/cluster/frontend/src/components/PanelResizeHandle.tsx`** (new, 147 lines): shared horizontal resize handle. Accepts `edge: 'left' | 'right'` to flip delta direction, a `cssVar` to preview during drag, `bounds`, `onCommit`, and `onReset`. Writes the CSS variable on the panel element on every `pointermove` and calls `onCommit` once on `pointerup`/`lostpointercapture`. The `shownWidth()` helper reads `getBoundingClientRect().width` so a gesture from a squeezed state commits the screen width, not the stored surplus.
- **`packages/cluster/frontend/src/components/SidebarResizeHandle.tsx`** (new, 25 lines): thin wrapper that wires `PanelResizeHandle` to `useSidebarLayout`'s `width`/`setWidth`/`resetWidth`, `edge="right"`, `cssVar="--sidebar-w"`, `showClassName="md:block"`.
- **`packages/cluster/frontend/src/components/right-rail/RailResizeHandle.tsx`** (−91 / +23 lines): all drag/key logic removed; now delegates to `PanelResizeHandle` with `edge="left"`, `cssVar="--right-rail-w"`, `showClassName="lg:block"`.
- **`packages/cluster/frontend/src/hooks/useSidebarLayout.ts`**: adds `SIDEBAR_WIDTH` bounds constant, `clampSidebarWidth`, `WIDTH_STORAGE_KEY = 'anygarden_sidebar_width'`, `width` state initialized from `localStorage`, `setWidth` and `resetWidth` callbacks, and persists `width` via `useEffect`. All exposed through `SidebarLayoutContext`.
- **`packages/cluster/frontend/src/hooks/useRightSidebarLayout.ts`**: raises `RIGHT_RAIL_WIDTH.max` from 560 to 960; updates comment to clarify the max only bounds the stored value.
- **`packages/cluster/frontend/src/index.css`**: adds `--chat-min-w: 28rem` CSS custom property under `:root`.
- **`packages/cluster/frontend/src/pages/ChatPage.tsx`**: adds `md:min-w-[var(--chat-min-w)]` to `<main>` so the conversation column enforces the floor.
- **`packages/cluster/frontend/src/components/Sidebar.tsx`**: renders `SidebarResizeHandle` and applies `style={{ '--sidebar-w': width + 'px' }}` on the desktop column.
- **`packages/cluster/frontend/src/components/PageShell.tsx`**, **`RightContextRail.tsx`**, **`SharedRoomConversation.tsx`**, **`ThreadPanel.tsx`**: minor wiring for the ref/width props needed by `SidebarResizeHandle` and `RailResizeHandle`.
- **`packages/cluster/frontend/src/components/PanelResizeHandle.test.tsx`** (new, 118 lines): unit tests for pointer drag, keyboard (Arrow, Home, End), double-click reset, and `shownWidth()` fallback.
- **`packages/cluster/frontend/src/components/Sidebar.test.tsx`**: extended with resize-handle render and `setWidth` persistence tests.
- **`packages/cluster/frontend/src/hooks/useSidebarLayout.test.ts`**: extended with `width` init, clamp, `setWidth`, `resetWidth`, and `localStorage` round-trip tests.
- **`packages/cluster/frontend/e2e/right-rail.spec.ts`**: extended with drag + keyboard scenarios for both the rail handle and the sidebar handle.
- **`docs/requirements.md`**: adds ROOM-13 row.
- **`DESIGN.md`**: extends §5 to note that the sidebar and right rail are resizable from their inner edge and yield to `--chat-min-w`.
- **`packages/cluster/frontend/src/i18n/catalogs/chat.ts`**: adds `resizeSidebar` key.

## Decisions

- **Shared `PanelResizeHandle` vs. duplicating the logic**: the rail already had a self-contained drag implementation (`RailResizeHandle`). Duplicating it for the sidebar would mean maintaining two copies of pointer-capture, cursor locking, and keyboard handling. Extraction into a single parametric component (`edge`, `cssVar`, `bounds`, `onCommit`) keeps both wrappers trivial and makes future panel handles (e.g. a thread panel) one-liners.
- **Chat-column floor instead of panel max**: the original cap (`min(560px, 40vw)`) guarded the conversation column indirectly. Replacing it with a CSS floor on `<main>` (`md:min-w-[var(--chat-min-w)]`) makes the guarantee explicit and independent of either panel's width, so the sidebar and the rail can each grow freely until the floor is hit; at that point both panels shrink toward their own minimums. The 28 rem value was chosen so the default 256 + 320 layout at 1024 px is exactly unchanged (1024 − 256 − 320 = 448 px ≈ 28 rem).
- **Commit from screen width on drag end**: the stored width can exceed the screen width when the panel is squeezed by the floor. If `onCommit` were called with the stored value, dragging a squeezed panel would appear to do nothing until the pointer crossed the hidden surplus. Starting and committing from `getBoundingClientRect().width` makes every gesture responsive from its first pixel.
- **`max: 960` for the rail**: raising the upper bound removes the artificial 560 px ceiling for users on wide monitors; the chat-column floor (`--chat-min-w`) is now the effective constraint, so the stored-max is just a sanity guard against runaway values.
- **Assumption to revisit**: the 28 rem floor is correct at the default font size. If the app ever introduces a user-adjustable base font size, `--chat-min-w` should become a `rem`-based value derived from the configured size rather than a fixed CSS variable.

## Result

- Desktop sidebar gains a drag/keyboard-resizable right edge; width is persisted across reloads.
- Right rail's 560 px / 40 vw ceiling is gone; both panels grow freely and yield to the `--chat-min-w` floor before the conversation column narrows.
- `PanelResizeHandle` is the single implementation of horizontal panel resize; `RailResizeHandle` and `SidebarResizeHandle` are thin wrappers.
- ROOM-13 is formally captured in requirements and design docs.
- 19 files changed; 567 insertions, 110 deletions. All new behaviour covered by unit tests and e2e specs.
