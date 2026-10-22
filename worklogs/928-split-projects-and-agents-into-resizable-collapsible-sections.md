# feat(sidebar): split projects and agents into resizable, collapsible sections (#928)

- Commit: `af4b372` (af4b37264efc5bba17178e983559aa4c259d9fb5)
- Author: Changyong Um
- Date: 2026-10-22T18:18:52+09:00
- PR: — (issue #928)

## Situation

The sidebar's projects tree and agent DM list shared a single scroll container. When a workspace had many rooms, the room tree consumed all available height and pushed agent DMs below the viewport with no way for users to reclaim that space. There was no mechanism to divide the sidebar's height between the two sections independently.

## Task

Split the sidebar into two independently scrollable sections — one for projects, one for agent DMs — separated by a draggable handle. Each section must:
- Scroll independently under a fixed header
- Collapse independently (collapsing one cedes its space to the other)
- Persist its collapsed state and the split ratio across reloads and screen changes
- Keep a minimum height floor so neither section becomes unusable

## Action

- **`packages/cluster/frontend/src/hooks/useSidebarSplit.ts`** (new): `useSidebarSplit` hook managing split ratio state with `localStorage` persistence (`anygarden_sidebar_split_ratio`), collapse flags for projects and agents (`anygarden_sidebar_projects_collapsed`, `anygarden_sidebar_agents_collapsed`), clamp/default logic, and `SIDEBAR_SPLIT` constants (`default: 0.5, min: 0.1, max: 0.9, step: 0.05`). Reads are wrapped in try/catch to survive private-mode Safari.
- **`packages/cluster/frontend/src/components/SidebarSplitHandle.tsx`** (new): `SidebarSplitHandle` component — a `role="separator"` drag handle on the Y-axis. Mirrors the right rail's `RailResizeHandle` (#760): writes the `--sidebar-split` CSS variable directly on the container during drag (no React re-renders per frame), commits once on pointer release. Supports keyboard navigation (ArrowUp/Down adjust by 5%, Home/End jump to bounds) and double-click to reset.
- **`packages/cluster/frontend/src/components/Sidebar.tsx`** (modified): Wired `useSidebarSplit` into the sidebar layout; split the single `ScrollArea` into two `<section>`s, each a fixed header over its own `ScrollArea`, with a `min-h-24` (96 px) floor; the agents section takes `basis-[var(--sidebar-split)]`; made both section headers collapse toggles (the projects one is new; the agents one is now persisted); inserted `SidebarSplitHandle` between sections; hidden the handle when either section is collapsed.
- **`packages/cluster/frontend/src/components/Sidebar.test.tsx`** (modified): Added `[ROOM-12]` tests for the default 50% split, a restored split, collapse combinations and handle visibility, and the no-agents-section case.
- **`packages/cluster/frontend/src/components/SidebarSplitHandle.test.tsx`** (new): Unit tests for keyboard navigation, double-click reset, pointer drag start/move/end, and ARIA attributes.
- **`packages/cluster/frontend/src/hooks/useSidebarSplit.test.ts`** (new): Unit tests for `clampSplitRatio`, `useSidebarSplit` initial state, `setRatio`, `resetRatio`, and toggle actions.
- **`packages/cluster/frontend/e2e/sidebar-split.spec.ts`** (new): Playwright E2E tests covering drag-to-split, collapse/expand, handle visibility, keyboard control, and reset.
- **`packages/cluster/frontend/src/i18n/catalogs/chat.ts`** (modified): Added `chat.resizeSidebarSections` i18n key for the handle's `aria-label`.
- **`docs/requirements.md`** (modified): Added ROOM-12 (verified by vitest and e2e).

## Decisions

- **Ratio vs. pixel storage**: The split is stored as the agents section's share of the total height (0–1) rather than pixels. Sidebar height varies far more across screens than the right rail width does; a ratio captures "how I divided it" meaningfully across a laptop and a monitor. Each section also has a CSS `min-h` floor (96 px) so the ratio bounds only prevent degenerate stored values.
- **CSS-variable preview, commit once**: During drag, only the `--sidebar-split` CSS variable is updated directly on the container DOM node; React state is written once on pointer release. This avoids re-rendering the entire sidebar tree on every pointer-move event — the same pattern used by the right rail handle (#760).
- **Hook vs. context**: `useSidebarSplit` is a plain hook rather than a context (unlike `useSidebarLayout` / `useRightSidebarLayout`). Only `Sidebar.tsx` consumes it, so a context would add indirection with no benefit.
- **Hiding the handle on collapse**: When either section is collapsed it shrinks to its header, so there is no boundary left to move. The handle is hidden in that state; re-expanding restores it. This avoids a confusing draggable element that has no effect.
- **No shared drag hook**: The handle copies `RailResizeHandle`'s pointer handling instead of extracting a `useDragResize` hook — the axis, the unit (ratio vs px) and the state owner (local hook vs context) all differ, and two callers do not justify the abstraction yet. `react-resizable-panels` was rejected to avoid a new dependency interacting with Radix ScrollArea and the mobile drawer transform.
- **Even split by default**: The user chose a fixed 50:50 start even though few agents leave empty space; revisit if that space draws complaints.
- **96 px floor in CSS**: The minimum per-section height is enforced by CSS (`min-h-24`) rather than in the hook's clamp, so layout remains correct even if a stale stored ratio is applied before the DOM is measured.

## Result

- Projects and agent DMs each scroll independently; a long room tree no longer hides agents.
- Users can drag the separator to allocate height, adjust with keyboard, or double-click to reset to 50/50.
- Collapsed sections hand their space to the other; the handle disappears when it would be inert.
- Split ratio and collapse states persist across page reloads and survive moving between screens.
- New unit tests for `useSidebarSplit`, `SidebarSplitHandle`, and updated `Sidebar` tests; new Playwright E2E spec covering the full interaction surface. Full vitest (1055) and e2e (82) suites and `npm run build` pass.
