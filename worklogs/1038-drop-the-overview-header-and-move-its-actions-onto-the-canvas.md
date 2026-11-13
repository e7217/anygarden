# feat(agent-links): drop the overview header and move its actions onto the canvas (#1038)

- Commit: `f7fd898` (f7fd898f3aac9bfda3acc9bce67a7c558f60689e)
- Author: Changyong Um
- Date: 2026-11-13T08:52:53+09:00
- PR: #1038

## Situation

The AgentLinks overview page had a persistent header bar (~100px tall) containing a title, agent/link counts summary (`agentLinks.summary`), a description paragraph, Refresh and Add link buttons, a saving status message, and cycle warnings. This header consumed significant vertical real estate on a page whose primary value is the interactive canvas itself.

## Task

Remove the overview header entirely and relocate its interactive elements onto the canvas toolbar, while ensuring existing functionality (refresh, add link, saving indicator, cycle warning, empty state) remains accessible and visible. The dot grid background needed to be legible in both light and dark themes.

## Action

- **`AgentLinksPage.tsx`**: Removed the entire `<div>` header block (title, summary span, description paragraph, Refresh/Add link buttons, saving status, and cycle warning section). Replaced with a screen-reader-only `<h1>` for accessibility. Moved the conditional overlay region (empty state, cycles, saving) below the toolbar (`top-14` instead of `top-3`), and merged saving/cycle/empty into a single `flex-col` container. Passed `leadingActions` (Refresh icon-button) and `actions` (Add link button) props to `AgentLinksCanvas`. Removed `busyCount` memo and `formatNumber` usage since the summary string is gone.
- **`AgentLinksCanvas.tsx`**: Added `leadingActions?: ReactNode` and `actions?: ReactNode` props to the `Props` interface. Rendered `{leadingActions}` before the layout buttons and `{actions}` after them in the top-right toolbar `<div>`. Changed `flex` to `flex flex-wrap justify-end` on the toolbar container. Updated `Background` color from `--color-border-subtle` to `--color-border-strong` and increased gap/size for better contrast in both themes. Added `ReactNode` to imports.
- **`i18n/catalogs/agentLinks.ts`**: Removed the `agentLinks.summary` key from both English and Korean catalogs, as the counts display is gone.
- **`AgentLinksPage.test.tsx`**: Removed the assertion `expect(screen.getByText('4 agents · 2 answering · 0 links')).toBeInTheDocument()` since the summary element no longer renders.

## Decisions

- **Move actions to canvas toolbar rather than a slim header**: The canvas fills the page; a persistent header above it creates a split-attention problem. Integrating Refresh and Add link into the canvas toolbar keeps primary actions visible exactly where the user is focused. The alternative of collapsing the header to just buttons was rejected as it still wasted vertical space without changing the interaction model.
- **`leadingActions` / `actions` props over internal state in canvas**: Passing page-level actions as `ReactNode` props preserves the canvas component's single responsibility (layout and interaction) while giving the page full control over what appears in the toolbar. Embedding page actions directly in `AgentLinksCanvas` would couple the canvas to business logic it shouldn't own.
- **Float overlays at `top-14` instead of removing them**: Saving status, cycle warnings, and the empty-state card are all contextually tied to the canvas. Floating them below the toolbar row (approximately one toolbar height) prevents them from being obscured by the buttons while maintaining proximity to the canvas content.
- **`--color-border-strong` for the dot grid**: `--color-border-subtle` (#eaf0ed) was nearly indistinguishable from the light canvas background (#f5f8f7). Using `--color-border-strong` ensures the grid is perceptible in both themes without requiring a separate dark-mode override. If the token value changes in a future design-system update this choice should be revisited.
- **Screen-reader-only `<h1>`**: Removing the visible title breaks heading hierarchy for screen readers. A `sr-only` h1 preserves accessibility without consuming layout space.

## Result

The overview page gains ~100px of canvas height on all viewports. Refresh and Add link are now reachable from within the canvas toolbar row. Saving status, cycle warnings, and the empty state float over the canvas below the toolbar without blocking agent node interaction. The dot grid is visible in both light and dark themes. One test assertion tied to the removed summary string was deleted; all other tests remain.
