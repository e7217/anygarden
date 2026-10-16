# fix(ui): align the composer with the message text edges (#864)

- Commit: `cf2a939` (cf2a9392a12bf828c5636f21b5b19999f9c42611)
- Author: Changyong Um
- Date: 2026-10-16T15:48:51+09:00
- PR: #864

## Situation

The chat column's `chat-column` utility controlled max-width and container-query breakpoints, but horizontal padding (`px-6`) was applied separately on the message list `<div>` and on the composer's outer wrapper. This meant the composer box extended edge-to-edge within the column while message text started 24px inward, causing a visible misalignment between the input box and the prose above it. The thread panel used `px-4` (16px), compounding the inconsistency.

## Task

- Move the 24px side gutter (`padding-inline: 1.5rem`) into the `chat-column` CSS utility so every consumer inherits it automatically.
- Remove the redundant outer `px-6` / `px-4` from the message list, empty-state div, and composer outer wrapper.
- Add `data-testid="composer-box"` to the inner composer border-box so E2E tests can assert its edges.
- Update the thread panel reply list to use `px-6` (24px) to match.
- Write E2E tests that verify the composer box left/right edges equal agent-prose left/right edges at both 375px and 1440px viewport widths, and that the thread composer aligns with thread replies.

## Action

- `packages/cluster/frontend/src/index.css` (`@utility chat-column`, line ~321): added `padding-inline: 1.5rem` so the gutter is owned by the utility; updated the inline comment to document the rationale.
- `packages/cluster/frontend/src/components/ChatArea.tsx` (empty-state div, ~line 243; message-list div, ~line 262): removed `px-6` from both, since `chat-column` now carries the padding.
- `packages/cluster/frontend/src/components/MessageInput.tsx` (outer wrapper, ~line 474): removed `px-4` from the `message-composer` div; added `data-testid="composer-box"` to the inner border-box div (~line 511).
- `packages/cluster/frontend/src/components/ThreadPanel.tsx` (reply list div, ~line 154): changed `px-4` → `px-6` to align with the 24px gutter standard.
- `packages/cluster/frontend/e2e/chat-width.spec.ts`: extended the `message()` helper to accept an optional `root` parameter for thread replies; added a `msg-thread-reply` fixture; added `composerBox` locator helper; added a new `composer edges (#864)` test suite with three cases (375px, 1440px main chat, 1440px thread panel).
- `DESIGN.md`: updated the chat-column paragraph to explicitly state that the 24px side gutter is part of the utility and that the thread panel uses the same gutter.

## Decisions

- **Gutter in the utility vs. on each consumer**: centralising `padding-inline` in `chat-column` was chosen so any new consumer (e.g. a future pinned-message banner) inherits the gutter without a separate rule. The alternative — keeping per-component `px-6` — had already drifted (composer used `px-4`, thread used `px-4`) and would require ongoing audits.
- **Remove outer padding from composer wrapper rather than inner**: the border-box is the visual container users see; having padding outside it meant the border ran to the edge of the column while text did not. Removing outer padding and letting `chat-column` set the gutter keeps the border flush with the text columns.
- **`px-6` for thread panel**: the thread panel's reply list was on `px-4` (16px). Bumping to `px-6` (24px) aligns it with the main chat column gutter. The alternative of a separate thread-specific value was rejected because there is no design rationale for a different gutter there.
- **Assumption**: all direct children of `chat-column` that need the gutter are block-level and fill the column width. If a future consumer needs to opt out (e.g. a full-bleed image row), it will need a negative `margin-inline` override.

## Result

- Composer box left and right edges now coincide with agent-prose edges in both the main chat and the thread panel.
- E2E spec `composer edges (#864)` covers 375px mobile, 1440px desktop (including after collapsing both panels to the 896px cap), and the thread panel composer.
- No visual change beyond the edge alignment; spacing, border-radius, and focus ring are unaffected.
- DESIGN.md updated to reflect that the gutter is part of the utility.
