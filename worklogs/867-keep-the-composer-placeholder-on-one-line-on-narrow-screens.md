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

- **`placeholder-shown:` + `placeholder:` Tailwind variants over a CSS class or `::placeholder`-only rule**: Chromium renders the placeholder ellipsis only when the *element* itself is `overflow: hidden; white-space: nowrap; text-overflow: ellipsis` — styling `::placeholder` alone is insufficient. Firefox and Safari need `::placeholder` styled. Both variants are required; Tailwind's JIT generates them from the colon-variant syntax with no extra config.
- **Tailwind utility classes over a one-off CSS rule**: stays consistent with the existing `className` pattern in the component; avoids a new CSS module or `global.css` entry.
- **Reset on focus not forced explicitly**: when the user focuses the input, `autoResize` fires and the `whitespace-nowrap` constraint naturally stops mattering (the placeholder disappears and typed text uses the pre-existing wrapping rules). Forcing a reset via `:focus` overrides would add complexity for no user-visible benefit.
- **Assumption**: if `autoResize` logic is ever changed to pre-size the box to the placeholder width, this fix becomes unnecessary and the added classes can be removed.

## Result

- On 375 px viewports the placeholder renders on a single line with a trailing ellipsis; the box no longer shows a half second line.
- Typed text continues to wrap and grow the textarea exactly as before.
- Two new E2E tests in `chat-width.spec.ts` cover both the empty-state appearance and the typed-text growth path, preventing regressions.
