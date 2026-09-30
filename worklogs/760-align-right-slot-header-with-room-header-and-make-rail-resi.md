# fix(frontend): align right-slot header with room header and make rail resizable (#760)

- Commit: `44a8094` (44a80945d64cb5df43298679164fda34b62b039e)
- Author: Changyong Um
- Date: 2026-09-30T16:01:18+09:00
- PR: #760 (issue)

## Situation

The chat page's right-hand slot (context rail or thread panel) had fixed header heights: 56px for the rail and 48px for the thread panel. The room header is 57px on one row and about 81px once the chat column is narrower than 54rem and it wraps onto two rows. Their bottom borders never lined up. The baseline Playwright measurement was 25px off at 1024 and 1280px, 1px off at 1440 and 1920px, and 33px off for the thread panel. The rail could only be opened or closed; its width was fixed at `lg:w-80`.

## Task

- The right-slot header border must line up with the room header on desktop (lg+), on one row or two.
- Let users resize the rail on desktop by pointer and keyboard, persist the width, and share it with the thread panel.
- Keep the mobile/tablet drawer (< lg) unchanged. The left sidebar is out of scope.

## Action

- `src/hooks/useElementHeightVar.ts` (new): a ResizeObserver writes the source's border-box height to a CSS variable on the target. It skips unchanged values and clears the variable on unmount.
- `src/pages/ChatPage.tsx`: callback refs on the page root and the RoomHeader wrapper publish `--room-header-h`.
- `src/components/RightContextRail.tsx` and `src/components/ThreadPanel.tsx`: headers use `lg:h-[var(--room-header-h,3.5rem)]`. Asides use `--right-rail-w`, `lg:w-[var(--right-rail-w)] lg:max-w-[40vw]` and `lg:relative`, and mount the resize handle. The rail disables transitions while `data-resizing` is set, and ThreadPanel drops `xl:w-96`.
- `src/hooks/useRightSidebarLayout.ts`: adds `width`/`setWidth`/`resetWidth`, `RIGHT_RAIL_WIDTH` (320 default, 280–560, step 16), `clampRailWidth`, and the persisted key `anygarden_right_sidebar_width`.
- `src/components/right-rail/RailResizeHandle.tsx` (new): a `role="separator"` handle. Pointer capture drives the drag, which writes the CSS variable directly and commits once on release. ←/→/Home/End adjust the width and a double-click resets it. The handle is hidden below lg.
- i18n `chat.resizeContext` (en/ko). Test IDs `room-header`, `right-rail-header` and `thread-panel-header`.
- Tests: `useElementHeightVar.test.ts`, `RailResizeHandle.test.tsx`, width cases in `useRightSidebarLayout.test.ts`, and the browser spec `e2e/right-rail.spec.ts`.

## Decisions

- **Header alignment**: options were (1) a fixed shared height token, (2) restructuring the page into a CSS grid so the headers share a row, and (3) measuring the room header and publishing a CSS variable. (1) cannot follow the room header's responsive two-row wrap, which the new resizing makes change even more often. (2) would have to split the headers out of each `<aside>` and rework the mobile fixed drawers. (3) was chosen because it tracks every cause of height change at the cost of one observer. Measuring the wrapper's border-box height makes the 1px border difference disappear.
- **Resize mechanism**: `react-resizable-panels` would impose a percentage PanelGroup layout that conflicts with the flex + fixed-drawer structure. `@dnd-kit` targets reordering, not a single dimension. Direct Pointer Events come to about 100 lines.
- **Drag updates**: writing the DOM CSS variable per move and committing on release avoids re-rendering the chat column and writing localStorage on every frame.
- **Width bounds**: the 320 default matches the old `lg:w-80`, so nothing moves on upgrade. The ThreadPanel's former `xl:w-96` (384px) becomes the shared width, which is intended. A 40vw cap keeps a width saved on a wide screen from crushing the chat column on a narrow one.
- Revisit if the RoomHeader wrapper gains in-flow content besides the header (for example a banner). The measurement should then move to the RoomHeader root.

## Result

- Header borders line up at 1024, 1280, 1440 and 1920px, with a one- or two-row room header, for both the rail and the thread panel. Checked by `e2e/right-rail.spec.ts`.
- The rail resizes by drag and keyboard, the width persists across reloads, and the thread panel inherits it.
- Checks: full frontend vitest (99 files, 790 tests), Playwright e2e (41 tests) and `npm run build` pass. Screenshots in light and dark themes checked.
