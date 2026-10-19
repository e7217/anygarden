# test(e2e): expect the room header to stay on one row with both sidebars open (#897)

- Commit: `454a29f` (454a29f8c436b3900b9aee01418738b8cf8b7197)
- Author: Changyong Um
- Date: 2026-10-19T14:24:37+09:00
- PR: #897

## Situation

The room header previously displayed a connection badge, an agent-count badge, and a representative-agent `<select>` at all times, causing it to wrap onto a second row when both sidebars were open and the chat column became narrow. Issue #897 refactored the header to show status-level items only when they require user attention (offline agents, disconnection), and moved the representative-agent selector into the participant-list popover. Before this series of commits the e2e test guarding the header geometry was written with the old, two-row expectation (`height > 60`), which had to be inverted.

## Task

- Update the e2e test name to match the new, intended behavior: the header stays on **one** row when both sidebars are open.
- Flip the height assertion from `toBeGreaterThan(60)` to `toBeLessThanOrEqual(60)`.
- Update the inline comment to explain the historical context (#897 removed the always-visible status row).
- Keep the rail-alignment assertion (`Math.abs(bottom(railHeader) - headerBox.y - headerBox.height) ≤ 0.5`) intact.

## Action

Modified `packages/cluster/frontend/e2e/right-rail.spec.ts` (lines 176–190):

- **Test name** changed from `'the line holds when the room header wraps onto two rows'` to `'the room header stays on one row when both sidebars squeeze the chat column'` — the test now documents the *fixed* behavior, not a workaround.
- **Inline comment** updated to explain that `#897` removed the always-visible status row, so the header no longer wraps at 1280 px with both sidebars open.
- **Height assertion** inverted: `expect(headerBox!.height).toBeGreaterThan(60)` → `expect(headerBox!.height).toBeLessThanOrEqual(60)`.
- Viewport setup (`1280×800`), `openRoom`, `roomHeader`, `railHeader` helpers, and the alignment assertion are unchanged.

## Decisions

The test name and assertion both needed to change together; updating only the assertion would have left a misleading name that implied wrapping was still expected.

Options weighed:

- **A. Delete the test** — the old test was already wrong; removing it would eliminate the false signal. Rejected: the rail-alignment behavior (header bottom flush with the rail header) is still worth guarding, and the one-row constraint is a regression risk now that the feature is trimmed.
- **B. Keep the old name, flip the assertion** — minimal diff. Rejected: the test name `'the line holds when the room header wraps onto two rows'` would now be factually wrong, making future readers assume wrapping is an accepted state.
- **C. Rename + flip assertion (chosen)** — makes the test a truthful specification of the new behavior and preserves the rail geometry guard. The updated comment records *why* the behavior changed (#897), which is the most valuable context for future readers.

Assumption: `toBeLessThanOrEqual(60)` is a safe bound at the 1280 px viewport. If a future font-size change or header padding increase pushes the single-row height above 60 px, this threshold will need adjustment.

Source: plan file `.tmp/plan-897-room-header-trim.md` §4 step 1 (e2e impact noted as "영향 spec 없음" — no direct e2e spec existed at plan time; this commit is the corrective follow-up after the main implementation landed).

## Result

- The e2e test now correctly asserts that the room header occupies exactly one row (height ≤ 60 px) when both sidebars are open at 1280 px width.
- The rail-alignment assertion is preserved, continuing to guard that the right-rail header aligns flush with the room header's bottom edge.
- No behavioral change to production code; this is a test-only correction.
- CI will no longer fail on this spec once the #897 implementation commits are present on the branch.
