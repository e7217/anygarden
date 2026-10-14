# fix(sidebar): lead with the wordmark, create projects from their section and align the footer dividers (#827)

- Commit: `aea5288` (aea52885943627b6c4cda0cbfa13a2f915fc0093)
- Author: Changyong Um
- Date: 2026-10-14T15:28:50+09:00
- PR: #827

## Situation

The sidebar header placed the wordmark ("Anygarden") after the desktop collapse toggle, making the first visible element a control rather than a brand anchor. The "New Project" button lived at the bottom of the sidebar as a pinned footer item — visually adjacent to the agents section — so users could not tell which section it belonged to. Additionally, the sidebar footer and the one-line message composer had independent heights, meaning their top border lines were misaligned and appeared as two separate dividers instead of one clean horizontal rule.

## Task

- Reorder the sidebar header so the wordmark leads and the collapse toggle (and mobile close button) share the trailing slot.
- Promote the wordmark from `text-sm` to `text-body` and mark it semibold.
- Remove the bottom-pinned "New Project" button and replace it with a "Projects +" section header that mirrors the existing agents section header pattern.
- Introduce a `--footer-min-height` CSS custom property so the sidebar footer and the message composer share the same minimum height, causing their top border lines to align at the same vertical position.
- Update DESIGN.md to reflect the new layout contract.
- Update the sidebar collapse e2e test to assert the toggle sits in the trailing slot; add a new e2e that checks the footer/composer divider alignment.

## Action

- **`packages/cluster/frontend/src/components/Sidebar.tsx`**:
  - Moved the `<button aria-label="Go to home">` (wordmark) to appear before the desktop `PanelLeftClose` collapse button and the mobile close button in the header `div` (`Sidebar.tsx:439–465`). Updated its className from `text-sm` to `text-body`.
  - Removed `DialogTrigger` import and the bottom "New Project" `<div className="p-2">` block wrapping a `Dialog` with a trigger `Button`.
  - Added a "Projects +" section header `<div>` (same shape as the existing agents header) with a `data-testid="sidebar-add-project"` `<Plus>` icon button that opens the project dialog via `setProjectDialogOpen(true)` (`Sidebar.tsx:523–546`).
  - Extracted the `<Dialog>` for project creation out of the trigger wrapper; it is now rendered standalone, opened programmatically (`Sidebar.tsx:680–700`).
  - Added `data-testid="sidebar-footer"` and `min-h-[var(--footer-min-height)]` to the user-info footer div (`Sidebar.tsx:807`).
- **`packages/cluster/frontend/src/index.css`**:
  - Added `--footer-min-height: calc(var(--control-icon-size) + 2.4375rem)` inside the `:root` block with a comment explaining the arithmetic (icon-size + border + py-3 + composer border + p-1.5) (`index.css:128–132`).
- **`packages/cluster/frontend/src/components/MessageInput.tsx`**:
  - Added `data-testid="message-composer"` and `min-h-[var(--footer-min-height)]` to the outer wrapper div (`MessageInput.tsx:474`).
- **`packages/cluster/frontend/src/components/Sidebar.test.tsx`**:
  - Added `describe('Sidebar — layout (#827)')` with two unit tests: one asserting the wordmark button precedes the collapse toggle in DOM order and carries `text-body`; one asserting no "New Project" text exists and the `sidebar-add-project` button opens the Create Project dialog (`Sidebar.test.tsx:278–299`).
- **`packages/cluster/frontend/e2e/threads.spec.ts`**:
  - Updated the existing collapse test to assert `collapse.x + collapse.width > sidebar.x + sidebar.width - 24` (trailing slot) and changed expand restoration assertion from `after.x === before.x` to `after.x < 24` (rail upper-left) (`threads.spec.ts:385–401`).
  - Added new test `'sidebar footer and composer share one top divider line'` that asserts `|footer.y - composer.y| ≤ 1` (`threads.spec.ts:402–409`).
- **`packages/cluster/frontend/src/i18n/catalogs/chat.ts`**:
  - Added `'chat.projects': 'Projects'` / `'프로젝트'` to both `chatEn` and `chatKo` catalogs (`chat.ts:12`, `chat.ts:262`).
- **`DESIGN.md`**:
  - Rewrote the navigation sentence to reflect: wordmark leads at body size/semibold; collapse toggle in trailing header slot; each section carries its own create action; footer and composer share `--footer-min-height`.

## Decisions

- **Wordmark first, controls last**: The sidebar header previously put the collapse toggle at the leading edge. Moving the wordmark to lead and the control to trail follows standard app-shell conventions (logo/brand first, utility controls last) and is consistent with how the mobile close button was already positioned in the trailing slot. This meant the desktop toggle needed to share that slot rather than occupy the leading position.
- **Section-header `+` button vs. bottom-pinned button**: The bottom "New Project" button read as part of the agents section because it abutted the agent list with no visual separation. Moving creation into the projects section header makes the affordance self-documenting — the `+` icon is adjacent to the "Projects" label — and mirrors the pre-existing agents header pattern, reducing the number of UI patterns to maintain. The alternative (keeping a bottom button but adding a divider) was rejected because it added layout complexity for no structural benefit.
- **CSS custom property for footer height**: A single `--footer-min-height` token lets both the sidebar footer and the message composer opt in to the same minimum height without coordination at the component level. The alternative — absolute pixel values duplicated in both components — was rejected because it would drift on future control-size changes. The value is derived from `--control-icon-size` (already a token) plus the measured padding/border arithmetic so it stays in sync.
- **Assumption**: `--control-icon-size` remains the canonical height of one-line composer controls. If that token is redefined or the composer gains additional rows by default, `--footer-min-height` will need to be revisited.

## Result

- The sidebar header now leads with the Anygarden wordmark at body size; the desktop collapse toggle occupies the trailing slot alongside the mobile close button.
- Project creation is accessed via the "Projects" section header `+` icon; the bottom-pinned "New Project" button is gone.
- The sidebar footer and the one-line message composer share `--footer-min-height`, so their top border lines form a single horizontal rule at the same vertical position.
- Two unit tests and two e2e tests (one updated, one new) cover the layout contract; DESIGN.md updated to match.
