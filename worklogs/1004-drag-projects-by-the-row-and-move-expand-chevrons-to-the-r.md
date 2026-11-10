# feat(sidebar): drag projects by the row and move expand chevrons to the right (#1004)

- Commit: `83b87f8` (83b87f857f8a18539de39ff08c5b65e988a33819)
- Author: Changyong Um
- Date: 2026-11-10T09:44:03+09:00
- PR: #1004

## Situation

The sidebar's project section had two separate interactive elements left of each project name: a dedicated drag handle (`GripVertical` icon button) and an expand/collapse chevron. This pushed every project name 32 px inward and created visual misalignment with the section-title rows (PROJECTS / AGENTS), where the chevron sat before the label instead of after. The drag handle also carried `aria` attributes (`role`, `aria-roledescription`) that conflicted with its icon-button nature.

## Task

- Remove the standalone drag handle from project rows.
- Make the full project name button the drag source (pointer-only, 6 px activation distance so a click still toggles).
- Move the expand/collapse chevron to the right edge of each project row, after the menu button.
- Move the section-title chevrons (PROJECTS, AGENTS headings) to the right edge, after the label text.
- Ensure keyboard/touch users can still reorder from the project context menu; Space/Enter on the name button must keep toggling.
- Update requirement ROOM-14, unit tests, and e2e tests accordingly.

## Action

**`packages/cluster/frontend/src/components/Sidebar.tsx`**
- Added `projectSensors` using only `PointerSensor` with `{ activationConstraint: { distance: 6 } }` (line ~230), replacing the shared `sensors` that included `KeyboardSensor`.
- Added `projectDragRef` (`useRef(false)`) and `releaseProjectDrag()` via `setTimeout(..., 0)` to suppress the click that fires at drag-end, preventing toggle on drop (lines ~235–241).
- Wired `onDragStart`, `onDragCancel`, and `onDragEnd` on `DndContext` to manage `projectDragRef` (lines ~613–621).
- Removed the conditional `<button … GripVertical>` drag-handle element entirely (lines ~636–645 before, gone after).
- Spread `dragListeners` (not `attributes`) directly onto the name `<button>`, which now also guards `onClick` with `projectDragRef.current` (lines ~648–660).
- Added a `<span aria-hidden>` chevron at the row's right edge with its own `onClick → toggleProject` and `data-testid="sidebar-project-chevron-{id}"` (lines ~670–680).
- Moved the section-title chevrons after `<span className="min-w-0 truncate">` in both PROJECTS and AGENTS headers, using `ml-auto` to push them right (lines ~543–550, ~762–769).
- `SortableProject` now accepts a `disabled` prop passed through to `useSortable({ id, disabled })` and drops `attributes` from the spread returned to the render prop.

**`packages/cluster/frontend/e2e/sidebar-project-order.spec.ts`**
- Updated `handle()` helper → `row()` selecting by exact button name.
- Rewrote the drag test to drag from a fixed x offset within the row rather than the old handle centre.
- Replaced the keyboard reorder test: now focuses the project menu button, opens it, then clicks "Move up" — no more `Space` on the drag handle.
- Added a new test asserting a click on the name still opens the project.
- Added an assertion that after a drag the dragged project stays collapsed (drop ≠ click).

**`packages/cluster/frontend/src/components/Sidebar.test.tsx`**
- Replaced the "gives every project a drag handle" test with two tests: one verifying the name button and right-edge chevron both toggle `aria-expanded`, one asserting no `Reorder …` button exists.
- Removed the stale assertion `queryByRole('button', { name: 'Reorder Zero' })` from the single-project guard test.

**`packages/cluster/frontend/src/i18n/catalogs/chat.ts`**
- Removed `chat.reorderProject` (en + ko) since the drag handle that used it is gone.

**`docs/requirements.md`**
- Updated ROOM-14 wording: "행을 끌어서, 또는 프로젝트 메뉴(키보드 포함)로 바꿀 수 있다" — removes the mention of a separate drag handle.

## Decisions

**Layout: options compared as mockups (A–E), the user chose D+E**
- A (as shipped in #995): handle and chevron both left of the name. Names pushed 32px in, and the handle slot sat empty at rest.
- B: chevron at the far right, handle kept on the left. Names move left, but on hover the menu and chevron crowd the right edge.
- C: one shared left slot, chevron at rest and handle on hover (Notion style). Recommended because it keeps the conventional chevron spot, but it hides the open/closed state while hovering, and touch devices would need the handle always shown.
- **D (chosen):** no visible handle, the name drags, chevron at the right. Cleanest, with names starting on the same line as the section title. The cost is that nothing signals the row can be dragged, and keyboard dragging is gone; the menu covers that.
- **E (chosen with D):** section-title chevrons move to the right as well, so rows and titles agree.

**Drag source and click suppression**
- The listeners go on the name button with a 6px activation distance, so a plain click still toggles. `projectDragRef` skips the click that lands on the button when a drop ends, so a drop does not also open or close the project.
- The sortable `attributes` (`role`, `aria-roledescription`, `aria-describedby`) are left off the name button so it keeps its own role and name.

**Keyboard reordering via menu (not sensor)**
- `KeyboardSensor` was dropped from `projectSensors` so Space/Enter on the name button keeps its native toggle semantics. Keyboard reordering is preserved through the existing "Move up / Move down" menu items. This matches the commit body's explicit statement: "keyboard and touch users reorder from the project menu."

**`aria-hidden` chevron vs. a second toggle button**
- The right-edge chevron is `aria-hidden` with an `onClick` delegated to `toggleProject`, so screen readers see only the name button's `aria-expanded` state. Adding a second labelled button for the same action would create duplicate tab-stop noise.

**Assumptions to revisit**
- If touch-drag support for projects is added later, `PointerSensor` covers touch events, but the 6 px threshold and `projectDragRef` logic should be verified on touch devices.
- If the menu "Move up/down" actions are removed, keyboard users lose their reordering path and a `KeyboardSensor` or equivalent must be restored.

## Result

- Project rows no longer show a separate drag handle; the name button drags on pointer with a 6 px threshold.
- The expand chevron appears at the row's right edge, after the menu button.
- Section titles (PROJECTS / AGENTS) show their chevron after the label text.
- A drag-and-drop does not accidentally toggle the project open/closed.
- vitest 1212 passed. The project-order e2e (row drag, menu move, click to open) passed 18 of 18 over 6 repeats; one earlier failure was a cold dev-server start that hit the sign-in wait.
- Known gap: the section-title chevron sits just left of +, so it is not in the same column as the row chevrons (same as mockup E).
- `chat.reorderProject` i18n key removed (en + ko).
