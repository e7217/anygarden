# fix(ui): keep the composer placeholder on one line on narrow screens (#867)

- Commit: `7c7f0ef` (7c7f0ef7026a5c8c512bed4be33c231a8e8db716)
- Author: Changyong Um
- Date: 2026-10-16T16:19:09+09:00
- PR: #867

## Situation

The `MessageInput` textarea uses `autoResize` logic that sizes the box to the typed content only. When the textarea is empty, the box collapses to its minimum height (`rows={1}`), but the placeholder text — which is longer than the box width on narrow viewports (375 px) — wrapped onto a second line. This caused a visible half-line to peek below the single-row box, giving the appearance of a broken or double-height input on phone-sized screens.

## Task

- Prevent the placeholder from wrapping when the textarea is empty.
- Show an ellipsis at the end of the placeholder so users know text is truncated.
- Ensure typed content continues to wrap and causes the box to grow as before (the `autoResize` behaviour must not be broken).
- Add Playwright E2E tests to lock this behaviour.

## Action

**`packages/cluster/frontend/src/components/MessageInput.tsx` (line ~578)**
- Extended the Tailwind `className` on the `<textarea>` with three pairs of Tailwind variants:
  - `placeholder-shown:overflow-hidden placeholder-shown:text-ellipsis placeholder-shown:whitespace-nowrap` — styles the *element itself* while the placeholder is visible, so Chromium's renderer clips the placeholder within one line and draws the ellipsis.
  - `placeholder:overflow-hidden placeholder:text-ellipsis placeholder:whitespace-nowrap` — styles the `::placeholder` pseudo-element directly (Firefox/Safari path).
  - These two together cover all major browsers; the ellipsis disappears when the field is focused (clipped but still on one line, which is acceptable).
- Added a multi-line comment explaining the cross-browser subtlety (`:placeholder-shown` vs `::placeholder`, and why both are needed).

**`packages/cluster/frontend/e2e/chat-width.spec.ts` (lines 278–314)**
- Added a new `test.describe('composer placeholder (#867)')` block with two tests:
  1. **`stays on one line with an ellipsis at 375px`** — asserts `whiteSpace: nowrap`, `overflowX: hidden`, `textOverflow: ellipsis` on the empty textarea, then asserts the box height equals one computed line height (±1 px tolerance).
  2. **`typed text still wraps and grows the box`** — fills the textarea with a long string, asserts `whiteSpace` is no longer `nowrap`, and asserts the box height exceeds one line by more than 1 px.

## Decisions

- **One line with an ellipsis, over a narrow-screen short placeholder or a taller empty box**: the bug is that the placeholder can wrap at all, so stopping the wrap fixes every width, locale and composer (room, thread, guest). A second, shorter string needs JS to pick by width plus a catalog key per locale. Growing the empty box to fit the placeholder would make the composer shrink as soon as typing starts and would break the `--footer-min-height` line shared with the sidebar footer. Both were rejected for those reasons.
- **Both `placeholder-shown:` and `placeholder:` variants**: verified in Chromium. With `::placeholder` styled alone, the text stayed on one line but was clipped without an ellipsis. With the empty box (`:placeholder-shown`) styled alone, the live check also showed no ellipsis. Only both together drew "…". Firefox and Safari were not checked (CI e2e runs Chromium only).
- **Focused state**: while the textarea is focused and still empty, Chromium drops the ellipsis and clips the placeholder on the same line. That is accepted: it stays on one line, and the placeholder disappears as soon as the user types.
- **Hint loss on phones**: the tail of the English hint ("# for rooms") is hidden at 375px. Mentions are also explained by the mention hint under the composer and the mention popover.
- **Assumption**: if `autoResize` ever sizes the box to the placeholder, revisit these classes.

## Result

- On 375 px viewports the placeholder renders on a single line with a trailing ellipsis; the box no longer shows a half second line.
- Typed text continues to wrap and grow the textarea exactly as before.
- Two new E2E tests in `chat-width.spec.ts` cover both the empty-state appearance and the typed-text growth path, preventing regressions.
