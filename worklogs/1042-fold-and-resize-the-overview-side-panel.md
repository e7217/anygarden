# feat(agent-links): fold and resize the overview side panel (#1042)

- Commit: `3da80d2` (3da80d2953ebeb9f7b6ce08895632ea676bc5bed)
- Author: Changyong Um
- Date: 2026-11-13T15:08:28+09:00
- PR: #1043

## Situation

The overview page's side panel (showing agent summary or selection details) had a fixed width of 320 px and offered no way to fold it. It shared no visual controls with the chat rail, but it also had no controls of its own, so users could neither collapse it to give the map more horizontal room nor widen it to see more content. COL-21 required that desktop users be able to fold and resize the panel independently of the chat rail.

## Task

- Add a toggle button in the canvas toolbar that folds/unfolds the overview side panel on desktop (lg breakpoint and above).
- Add a drag-and-keyboard resize handle on the left edge of the panel, with double-click to reset to the default width.
- Persist the collapsed flag and width to `localStorage` under keys separate from the chat rail's.
- Automatically re-open a folded panel when the user picks an agent or link on the map.
- Leave the mobile bottom sheet behaviour unchanged.
- Cover the behaviour with both Vitest unit tests and Playwright e2e tests.

## Action

- **`src/hooks/useOverviewPanelLayout.ts`** (new, 44 lines): Custom hook that owns the panel's `collapsed` boolean and `width` number. Uses the same `clampRailWidth` / `RIGHT_RAIL_WIDTH` bounds as the chat rail hook but writes to `anygarden_overview_panel_collapsed` and `anygarden_overview_panel_width`. Wraps every `localStorage` call in try/catch for private-window safety.
- **`src/hooks/useOverviewPanelLayout.test.ts`** (new, 64 lines): Five Vitest tests (jsdom) verifying default state, persistence, clamping/reset, isolation from the chat rail keys, and storage-throws fallback.
- **`src/pages/AgentLinksPage.tsx`**: Instantiates `useOverviewPanelLayout`; threads `layout` and `panelRef` into `DetailsPanel`; adds a `useEffect` that calls `setPanelCollapsed(false)` when `selection` changes; inserts a `PanelRightClose`/`PanelRightOpen` icon button (desktop-only via `hidden lg:inline-flex`) into `leadingActions`; sets `aria-controls`, `aria-expanded`, and `--overview-panel-w` CSS variable on the panel.
- **`src/pages/AgentLinksPage.test.tsx`**: Two new unit tests covering fold/unfold with `localStorage` assertions and keyboard-resize via `ArrowLeft` key on the separator handle.
- **`src/pages/AgentLinksPage.tsx` — `DetailsPanel`**: Gains `layout`, `panelRef`, and `id="agent-links-panel"`. Wraps inner content in a scrollable `div` so the resize handle can overhang the left border without clipping the scroll area. Applies `lg:hidden` when collapsed and `--overview-panel-w` for the dynamic width.
- **`src/i18n/catalogs/agentLinks.ts`**: Three new keys — `agentLinks.showPanel`, `agentLinks.hidePanel`, `agentLinks.resizePanel` — in both English and Korean.
- **`e2e/agent-links.spec.ts`**: Three new tests in an `overview side panel` describe block: drag-resize + reload persistence + double-click reset; toolbar fold/unfold + agent-pick re-opens; below-lg has no button or handle. Also fixes the existing mobile overlap test to guard against null bounding boxes (the fold button is `hidden` below lg and returns no box).
- **`docs/requirements.md`**: Adds COL-21 row documenting the new requirement.

## Decisions

- **Separate storage keys from the chat rail**: The chat rail (`useRightSidebarLayout`) persists to `anygarden_right_sidebar_*`. Reusing the same keys would mean folding a room's chat rail would also hide the overview summary and vice versa. A unit test (`does not share state with the chat rail`) locks this in. The only downside is a second set of keys; that is negligible.
- **Reuse `clampRailWidth` / `RIGHT_RAIL_WIDTH` bounds**: Both panels have the same minimum (200 px), maximum (800 px), and default (320 px), and `PanelResizeHandle` already enforces these. Reusing them avoids a second set of magic numbers and keeps the two panels visually consistent when both are visible.
- **`lg:hidden` for collapse vs. `width: 0`**: Applying `lg:hidden` removes the element from the layout flow entirely, letting the flex canvas fill the freed space without any transition overhead or CSS variable arithmetic. The panel still exists in the DOM (accessibility subtree stays intact for `aria-controls` to remain valid); `data-collapsed` is set on the `aside` so tests can assert state without relying on the class list.
- **Re-open on selection via `useEffect`**: Calling `setPanelCollapsed(false)` inside a `useEffect` that watches `selection` is the simplest approach. An alternative was to call it inside the `setSelection` handler, but that would require threading the setter through every code path that calls `setSelection`. The effect is idempotent (a no-op when already open) and fires synchronously in tests.
- **`hidden lg:inline-flex` for the fold button**: Tailwind's `hidden` utility sets `display: none` unconditionally, then `lg:inline-flex` overrides it at the breakpoint. This avoids a wrapper div and keeps the button accessible: a hidden button does not receive focus, so keyboard users on mobile never reach a non-functional control.

## Result

- The overview side panel folds and unfolds from a toolbar button on desktop; the button's icon and aria-label toggle between "Hide side panel" and "Show side panel".
- Dragging the left edge or pressing Arrow keys on the resize handle adjusts the panel width; double-clicking resets to 320 px.
- Both collapsed state and width survive a page reload (stored separately from the chat rail).
- Picking an agent or link while the panel is folded re-opens it.
- Mobile behaviour is unchanged: no fold button or resize handle appears below 1024 px.
- 7 new tests (5 Vitest + 2 Playwright e2e); COL-21 added to requirements.
